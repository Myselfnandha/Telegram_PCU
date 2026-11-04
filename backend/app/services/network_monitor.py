import os
import socket
import struct
import asyncio
import logging
from typing import Optional, Callable, List, Dict, Any

from app.telegram_client import TelegramClientManager, sender_pool
from app.services.queue_manager import queue_manager
from app.services.network_service import NetworkService

logger = logging.getLogger("network_monitor")

RTMGRP_LINK = 0x1
RTMGRP_NOTIFY = 0x2
RTMGRP_NEIGH = 0x4
RTMGRP_TC = 0x8
RTMGRP_IPV4_IFADDR = 0x10
RTMGRP_IPV4_MROUTE = 0x20
RTMGRP_IPV4_ROUTE = 0x40
RTMGRP_IPV4_RULE = 0x80
RTMGRP_IPV6_IFADDR = 0x100
RTMGRP_IPV6_MROUTE = 0x200
RTMGRP_IPV6_ROUTE = 0x400

class NetworkMonitor:
    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._is_online: bool = True
        self._debounce_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._listeners: List[Callable[[bool, Dict[str, Any]], Any]] = []
        self._network_service = NetworkService()
        self._running: bool = False

    @property
    def is_online(self) -> bool:
        return self._is_online

    def add_listener(self, fn: Callable[[bool, Dict[str, Any]], Any]):
        self._listeners.append(fn)

    def check_connectivity(self) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.6)
                s.connect(("1.1.1.1", 80))
                return True
        except Exception:
            return False

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        if self._running:
            return
        self._running = True
        self._loop = loop or asyncio.get_running_loop()
        self._is_online = self.check_connectivity()

        has_netlink = hasattr(socket, "AF_NETLINK") and hasattr(socket, "NETLINK_ROUTE")
        netlink_started = False

        if has_netlink:
            try:
                self._sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
                self._sock.setblocking(False)
                groups = RTMGRP_LINK | RTMGRP_IPV4_IFADDR | RTMGRP_IPV4_ROUTE | RTMGRP_IPV6_IFADDR
                self._sock.bind((0, groups))
                self._loop.add_reader(self._sock.fileno(), self._on_netlink_event)
                netlink_started = True
                logger.info("NetworkMonitor: Linux Netlink route watcher initialized (zero-latency carrier/route detection).")
            except Exception as e:
                logger.warning(f"NetworkMonitor: Netlink socket binding failed ({e}). Falling back to active socket polling.")
                if self._sock:
                    try:
                        self._sock.close()
                    except Exception:
                        pass
                    self._sock = None

        if not netlink_started:
            self._poll_task = self._loop.create_task(self._poll_loop())
            logger.info("NetworkMonitor: Active socket polling fallback initialized.")

    def stop(self):
        self._running = False
        if self._sock and self._loop:
            try:
                self._loop.remove_reader(self._sock.fileno())
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    def _on_netlink_event(self):
        try:
            while True:
                data = self._sock.recv(4096)
                if not data:
                    break
        except BlockingIOError:
            pass
        except Exception as e:
            logger.debug(f"NetworkMonitor: Netlink drain error: {e}")

        self._trigger_debounced_check()

    def _trigger_debounced_check(self):
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        if self._loop and self._running:
            self._debounce_task = self._loop.create_task(self._debounced_eval())

    async def _debounced_eval(self):
        try:
            await asyncio.sleep(0.4)
            current_online = self.check_connectivity()
            if current_online != self._is_online:
                await self._handle_state_transition(current_online)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"NetworkMonitor: Debounced eval error: {e}")

    async def _poll_loop(self):
        while self._running:
            try:
                await asyncio.sleep(2.5)
                current_online = self.check_connectivity()
                if current_online != self._is_online:
                    await self._handle_state_transition(current_online)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"NetworkMonitor: Poll loop notice: {e}")

    async def _handle_state_transition(self, new_online: bool):
        prev = self._is_online
        self._is_online = new_online
        lan_ip = self._network_service.get_local_ip() if new_online else "127.0.0.1"

        from app.socket_handlers import sio

        if not new_online:
            logger.warning(
                "NetworkMonitor: Network interface disconnected / carrier down. "
                "Freezing in-flight transfers and purging stale MTProto sockets."
            )
            queue_manager.pause_all_tasks()
            try:
                await sender_pool.disconnect_all()
            except Exception as pe:
                logger.debug(f"NetworkMonitor: Sender pool purge notice: {pe}")

            try:
                from app.services.rust_bridge import rust_bridge
                rust_bridge.trip_circuit(15.0)
            except Exception:
                pass

            payload = {"online": False, "reason": "carrier_down", "ip": "127.0.0.1"}
            try:
                await sio.emit("network:status", payload)
            except Exception:
                pass

            for fn in self._listeners:
                try:
                    res = fn(False, payload)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass
        else:
            logger.info(
                f"NetworkMonitor: Network interface reconnected (LAN IP: {lan_ip}). "
                f"Reconnecting Telegram MTProto and resuming transfers."
            )
            try:
                await TelegramClientManager.ensure_connected(timeout=10.0)
            except Exception as ce:
                logger.debug(f"NetworkMonitor: Reconnect primary client notice: {ce}")

            try:
                if await TelegramClientManager.is_secondary_authorized():
                    sec_client = await TelegramClientManager.get_secondary_client()
                    if sec_client and not sec_client.is_connected():
                        await sec_client.connect()
            except Exception as se:
                logger.debug(f"NetworkMonitor: Reconnect secondary client notice: {se}")

            try:
                from app.services.rust_bridge import rust_bridge
                rust_bridge.reset_circuit()
                asyncio.create_task(rust_bridge.ensure_sessions_registered())
            except Exception:
                pass

            queue_manager.resume_all_tasks()

            payload = {"online": True, "reason": "reconnected", "ip": lan_ip}
            try:
                await sio.emit("network:status", payload)
            except Exception:
                pass

            for fn in self._listeners:
                try:
                    res = fn(True, payload)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

network_monitor = NetworkMonitor()
