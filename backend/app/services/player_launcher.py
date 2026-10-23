import os
import shutil
import subprocess
import logging
import threading
import time
import json
import urllib.request
import urllib.parse
import re
import base64
import socket
from pathlib import Path
from typing import Optional, Dict, Any, Union, List
from app.services.playback_tracker import playback_tracker
from app.services.subtitle_service import subtitle_service

logger = logging.getLogger("player_launcher")


def get_free_port(default_port: int = 8089) -> int:
    """Finds an open port starting from default_port for the VLC HTTP telemetry server."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", default_port))
            return default_port
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def get_desktop_env() -> Dict[str, str]:
    """Retrieve the active host desktop environment (Wayland/X11) for graphical players."""
    env = os.environ.copy()
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        env["DISPLAY"] = ":0"
    if not env.get("XAUTHORITY"):
        import glob
        auth_files = glob.glob("/tmp/xauth_*") + [str(Path.home() / ".Xauthority")]
        for af in auth_files:
            if os.path.exists(af):
                env["XAUTHORITY"] = af
                break
    return env


class PlayerLauncher:
    """
    Deep Module for Host Media Player Lifecycle Management.
    Encapsulates binary resolution, desktop environment detection,
    codec/hardware-acceleration flags, subtitles, fast seeking, and progress tracking.
    """
    def __init__(self):
        self._active_proc: Optional[subprocess.Popen] = None
        self._vlc_port: Optional[int] = 8089
        self._vlc_password: str = "tgsuite"
        self._active_media_info: Dict[str, Any] = {}

    def kill_previous_player(self):
        """Terminate any existing player instance spawned by TG Power Suite."""
        if self._active_proc and self._active_proc.poll() is None:
            try:
                self._active_proc.terminate()
                try:
                    self._active_proc.wait(timeout=0.8)
                except subprocess.TimeoutExpired:
                    self._active_proc.kill()
            except Exception as e:
                logger.debug(f"Previous player cleanup notice: {e}")
            finally:
                self._active_proc = None

    def _start_vlc_monitor(
        self,
        proc: subprocess.Popen,
        chat_id: Optional[Union[int, str]] = None,
        message_id: Optional[int] = None,
        filename: Optional[str] = None,
        port: int = 8089,
        password: str = "tgsuite",
        items: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Background telemetry daemon for VLC.
        Periodically polls VLC's native Lua HTTP interface, extracts real-time playback timestamp,
        handles multi-episode playlist transitions (auto-binge sync), and synchronizes progress into SQLite.
        """
        def monitor_loop():
            auth_header = "Basic " + base64.b64encode(f":{password}".encode("ascii")).decode("ascii")
            status_url = f"http://127.0.0.1:{port}/requests/status.json"
            playlist_url = f"http://127.0.0.1:{port}/requests/playlist.json"

            curr_chat_id = str(chat_id) if chat_id else ""
            curr_message_id = int(message_id) if message_id else 0
            curr_filename = filename or ""
            last_saved = -1
            last_plid = None

            # Wait up to 10s for VLC HTTP daemon to be ready
            for _ in range(20):
                if proc.poll() is not None:
                    return
                try:
                    req = urllib.request.Request(status_url, headers={"Authorization": auth_header})
                    with urllib.request.urlopen(req, timeout=0.8) as resp:
                        if resp.status == 200:
                            logger.info(f"VLC HTTP telemetry connected on port {port} (active message: {curr_message_id})")
                            break
                except Exception:
                    time.sleep(0.5)

            while proc.poll() is None:
                try:
                    req = urllib.request.Request(status_url, headers={"Authorization": auth_header})
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            cur_time = int(data.get("time", 0))
                            duration = int(data.get("length", 0))
                            cur_plid = data.get("currentplid")

                            # Detect multi-episode playlist transition (Binge Watching Enhancement)
                            if items and cur_plid is not None and cur_plid != last_plid:
                                last_plid = cur_plid
                                try:
                                    pl_req = urllib.request.Request(playlist_url, headers={"Authorization": auth_header})
                                    with urllib.request.urlopen(pl_req, timeout=1.0) as pl_resp:
                                        if pl_resp.status == 200:
                                            pl_data = json.loads(pl_resp.read().decode("utf-8"))
                                            def find_current(node):
                                                if node.get("current") == "current" or node.get("id") == str(cur_plid):
                                                    return node
                                                for child in node.get("children", []):
                                                    res = find_current(child)
                                                    if res:
                                                        return res
                                                return None
                                            curr_leaf = find_current(pl_data)
                                            if curr_leaf:
                                                uri = curr_leaf.get("uri", "")
                                                m = re.search(r"/dl/([^/]+)/(\d+)/([^/?#]+)", uri)
                                                if m:
                                                    new_cid = m.group(1)
                                                    new_mid = int(m.group(2))
                                                    new_fn = urllib.parse.unquote(m.group(3))
                                                    if new_mid != curr_message_id:
                                                        if curr_message_id and last_saved > 0:
                                                            try:
                                                                playback_tracker.update_progress(
                                                                    chat_id=str(curr_chat_id),
                                                                    message_id=int(curr_message_id),
                                                                    filename=curr_filename,
                                                                    last_position_seconds=float(last_saved)
                                                                )
                                                            except Exception:
                                                                pass
                                                        curr_chat_id = new_cid
                                                        curr_message_id = new_mid
                                                        curr_filename = new_fn
                                                        last_saved = -1
                                                        logger.info(f"VLC Binge Tracker: advanced to episode '{curr_filename}' (message {curr_message_id})")
                                except Exception as pl_err:
                                    logger.debug(f"Playlist check notice: {pl_err}")

                            if curr_chat_id and curr_message_id and cur_time > 0 and abs(cur_time - last_saved) >= 2:
                                last_saved = cur_time
                                playback_tracker.update_progress(
                                    chat_id=str(curr_chat_id),
                                    message_id=int(curr_message_id),
                                    filename=curr_filename,
                                    last_position_seconds=float(cur_time),
                                    duration_seconds=float(duration) if duration > 0 else 0.0
                                )
                except Exception:
                    pass
                time.sleep(2.5)

            if curr_chat_id and curr_message_id and last_saved > 0:
                try:
                    playback_tracker.update_progress(
                        chat_id=str(curr_chat_id),
                        message_id=int(curr_message_id),
                        filename=curr_filename,
                        last_position_seconds=float(last_saved)
                    )
                    logger.info(f"VLC closed: final progress {last_saved}s saved for '{curr_filename}'")
                except Exception as e:
                    logger.debug(f"Final progress commit error: {e}")

        t = threading.Thread(target=monitor_loop, daemon=True, name=f"vlc-tracker-{message_id or 'batch'}")
        t.start()

    def _start_mpv_monitor(
        self,
        proc: subprocess.Popen,
        chat_id: Union[int, str],
        message_id: int,
        filename: str,
        socket_path: str
    ):
        """Background IPC daemon for MPV player to sync watch progress into SQLite."""
        def monitor_loop():
            for _ in range(20):
                if proc.poll() is not None:
                    return
                if os.path.exists(socket_path):
                    break
                time.sleep(0.5)

            if not os.path.exists(socket_path):
                return

            last_saved = -1
            while proc.poll() is None and os.path.exists(socket_path):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(1.5)
                        s.connect(socket_path)
                        s.sendall(b'{"command": ["get_property", "time-pos"]}\n')
                        resp = s.recv(1024).decode("utf-8")
                        data = json.loads(resp.strip())
                        cur_time = int(data.get("data", 0))
                        if cur_time > 0 and abs(cur_time - last_saved) >= 2:
                            last_saved = cur_time
                            playback_tracker.update_progress(
                                chat_id=str(chat_id),
                                message_id=int(message_id),
                                filename=filename,
                                last_position_seconds=float(cur_time)
                            )
                except Exception:
                    pass
                time.sleep(2.5)

            try:
                if os.path.exists(socket_path):
                    os.unlink(socket_path)
            except Exception:
                pass

        t = threading.Thread(target=monitor_loop, daemon=True, name=f"mpv-tracker-{message_id}")
        t.start()

    def launch(
        self,
        stream_url: str,
        filename: str = "video.mp4",
        player: str = "vlc",
        resume_sec: int = 0,
        chat_id: Optional[Union[int, str]] = None,
        message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Launches host media player (VLC or MPV) with direct streaming, instant seek flags,
        embedded/cached subtitles, and automated two-way playback progress tracking.
        """
        vlc_bin = shutil.which("vlc") or "/usr/bin/vlc"
        mpv_bin = shutil.which("mpv") or "/usr/bin/mpv"

        chosen_bin = None
        target = (player or "vlc").lower()
        if target == "mpv" and os.path.exists(mpv_bin):
            chosen_bin = mpv_bin
        elif os.path.exists(vlc_bin):
            chosen_bin = vlc_bin
        elif os.path.exists(mpv_bin):
            chosen_bin = mpv_bin

        if not chosen_bin:
            logger.warning("No media player binary (VLC or MPV) found on system path.")
            return {
                "success": False,
                "launched": False,
                "error": "No media player (VLC/MPV) installed",
                "stream_url": stream_url
            }

        self.kill_previous_player()

        # If resume_sec not explicitly provided, look up saved progress from SQLite
        if resume_sec <= 0 and chat_id and message_id:
            try:
                prog = playback_tracker.get_playback_progress(str(chat_id), int(message_id))
                if prog and prog.get("progress_percent", 0) < 96.0:
                    pos = prog.get("last_position_seconds", 0)
                    if pos and pos > 10:
                        resume_sec = int(pos)
            except Exception:
                pass

        # Check for cached subtitle
        sub_file = subtitle_service.get_cached_subtitle(filename)
        desktop_env = get_desktop_env()
        cores = os.cpu_count() or 4
        # On <=8 thread CPUs (like Core i5-10310U 15W), cap decoder at 2 threads so GUI never starves or throttles
        safe_threads = min(2, max(1, cores // 4)) if cores <= 8 else min(3, cores // 4)

        player_name = "mpv" if "mpv" in chosen_bin else "vlc"
        launched = False
        vlc_port = get_free_port(8089)
        mpv_sock = f"/tmp/tg_mpv_{message_id or os.getpid()}.sock"

        try:
            if player_name == "mpv":
                cmd = [
                    chosen_bin,
                    stream_url,
                    f"--force-media-title={filename}",
                    f"--input-ipc-server={mpv_sock}",
                    "--hr-seek=no",
                    "--hr-seek-framedrop=yes",
                    "--slang=eng,tam,hin,all",
                    "--sid=1",
                    "--cache=yes",
                    "--cache-pause=no",
                    "--cache-secs=30",
                    "--demuxer-max-bytes=128M",
                    "--demuxer-readahead-secs=45",
                    f"--vd-lavc-threads={safe_threads}",
                    "--vd-lavc-fast=yes"
                ]
                if sub_file and sub_file.exists():
                    cmd.append(f"--sub-file={str(sub_file)}")
                elif chat_id and message_id:
                    from app.config import PROXY_PORT
                    cmd.append(f"--sub-file=http://127.0.0.1:{PROXY_PORT}/api/media/subtitles/download/{chat_id}/{message_id}")
                if resume_sec > 10:
                    cmd.append(f"--start={resume_sec}")
                    logger.info(f"Resuming {filename} at {resume_sec}s in MPV")
            else:
                cmd = [
                    chosen_bin,
                    stream_url,
                    "--meta-title", filename,
                    "--extraintf=http",
                    f"--http-port={vlc_port}",
                    "--http-password=tgsuite",
                    "--no-qt-privacy-ask",
                    "--no-qt-error-dialogs",
                    "--input-fast-seek",
                    "--sub-language=eng,tam,hin,all",
                    "--sub-track=0",
                    "--audio-language=tam,eng,hin,tel,mal,kan,all",
                    "--deinterlace=-1",
                    "--deinterlace-mode=auto",
                    f"--avcodec-threads={safe_threads}",
                    "--avcodec-hw=any",
                    "--avcodec-fast",
                    "--network-caching=1000",
                    "--file-caching=1000",
                    "--http-reconnect",
                    "--no-video-title-show",
                    "--one-instance"
                ]
                if sub_file and sub_file.exists():
                    cmd.append(f"--sub-file={str(sub_file)}")
                elif chat_id and message_id:
                    from app.config import PROXY_PORT
                    cmd.append(f"--input-slave=http://127.0.0.1:{PROXY_PORT}/api/media/subtitles/download/{chat_id}/{message_id}")
                if resume_sec > 10:
                    cmd.append(f"--start-time={resume_sec}")
                    logger.info(f"Resuming {filename} at {resume_sec}s in VLC")

            self._active_proc = subprocess.Popen(
                cmd,
                env=desktop_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            launched = True
            logger.info(f"Launched {player_name.upper()} player (Direct Stream Pipeline) for: {stream_url}")

            if player_name == "vlc":
                self._vlc_port = vlc_port
                self._active_media_info = {
                    "chat_id": str(chat_id) if chat_id else "",
                    "message_id": int(message_id) if message_id else 0,
                    "filename": filename,
                    "stream_url": stream_url
                }

            if chat_id and message_id:
                try:
                    playback_tracker.update_progress(
                        chat_id=str(chat_id),
                        message_id=int(message_id),
                        filename=filename,
                        last_position_seconds=resume_sec
                    )
                except Exception as pe:
                    logger.debug(f"Could not seed playback progress: {pe}")

                # Start real-time background monitor to sync VLC progress back to TG Power Suite
                if player_name == "vlc":
                    self._start_vlc_monitor(self._active_proc, chat_id, int(message_id), filename, vlc_port)
                elif player_name == "mpv":
                    self._start_mpv_monitor(self._active_proc, chat_id, int(message_id), filename, mpv_sock)

        except Exception as e:
            logger.warning(f"Could not launch media player subprocess: {e}")
            return {
                "success": False,
                "launched": False,
                "error": str(e),
                "stream_url": stream_url
            }

        return {
            "success": True,
            "launched": launched,
            "stream_url": stream_url,
            "player": player_name,
            "has_subtitles": bool(sub_file and sub_file.exists()),
            "resume_seconds": resume_sec
        }

    def launch_batch(
        self,
        items: list,
        title: str = "Season Playlist",
        player: str = "vlc",
        host: str = "127.0.0.1:8088"
    ) -> Dict[str, Any]:
        """
        Launches host player with an entire TV series or multi-episode season queued.
        """
        import urllib.parse
        vlc_bin = shutil.which("vlc") or "/usr/bin/vlc"
        mpv_bin = shutil.which("mpv") or "/usr/bin/mpv"

        stream_urls = []
        for item in items:
            cid = item.get("chat_id")
            mid = item.get("message_id")
            fn = item.get("filename", "video.mp4")
            raw_url = item.get("stream_url")
            if not raw_url and cid and mid:
                enc = urllib.parse.quote(fn)
                raw_url = f"http://{host}/dl/{cid}/{mid}/{enc}"
            elif raw_url and raw_url.startswith("/"):
                raw_url = f"http://{host}{raw_url}"
            if raw_url:
                stream_urls.append(raw_url)

        chosen_bin = vlc_bin if os.path.exists(vlc_bin) else (mpv_bin if os.path.exists(mpv_bin) else None)
        if not chosen_bin or not stream_urls:
            return {"success": False, "launched": False, "episodes_count": 0, "title": title}

        self.kill_previous_player()
        desktop_env = get_desktop_env()
        launched = False

        try:
            cores = os.cpu_count() or 4
            safe_threads = min(2, max(1, cores // 4)) if cores <= 8 else min(3, cores // 4)
            vlc_port = get_free_port(8089)
            if "mpv" in chosen_bin:
                cmd = [
                    chosen_bin,
                    *stream_urls,
                    "--hr-seek=no",
                    "--slang=eng,tam,hin,all",
                    "--cache=yes",
                    f"--vd-lavc-threads={safe_threads}",
                    "--demuxer-max-bytes=128M",
                    "--demuxer-readahead-secs=45"
                ]
            else:
                cmd = [
                    chosen_bin,
                    *stream_urls,
                    "--extraintf=http",
                    f"--http-port={vlc_port}",
                    "--http-password=tgsuite",
                    "--no-qt-privacy-ask",
                    "--no-qt-error-dialogs",
                    "--input-fast-seek",
                    "--sub-language=eng,tam,hin,all",
                    "--audio-language=tam,eng,hin,tel,mal,kan,all",
                    "--deinterlace=-1",
                    "--deinterlace-mode=auto",
                    f"--avcodec-threads={safe_threads}",
                    "--avcodec-hw=any",
                    "--avcodec-fast",
                    "--network-caching=1000",
                    "--file-caching=1000",
                    "--http-reconnect",
                    "--no-video-title-show",
                    "--one-instance"
                ]

            self._active_proc = subprocess.Popen(
                cmd,
                env=desktop_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            launched = True
            logger.info(f"Launched batch binge session for {len(stream_urls)} episodes: {title}")

            if "vlc" in chosen_bin and items:
                first_item = items[0]
                self._vlc_port = vlc_port
                self._active_media_info = {
                    "chat_id": str(first_item.get("chat_id", "")),
                    "message_id": int(first_item.get("message_id", 0)),
                    "filename": first_item.get("filename", ""),
                    "items": items
                }
                self._start_vlc_monitor(
                    self._active_proc,
                    chat_id=first_item.get("chat_id"),
                    message_id=int(first_item.get("message_id", 0)),
                    filename=first_item.get("filename", "video.mp4"),
                    port=vlc_port,
                    items=items
                )
        except Exception as e:
            logger.warning(f"Could not launch batch player subprocess: {e}")

        return {
            "success": True,
            "launched": launched,
            "episodes_count": len(stream_urls),
            "title": title
        }

    def build_single_playlist(self, filename: str, stream_url: str, sub_url: Optional[str] = None) -> str:
        """Constructs an optimized single-stream .m3u playlist with safe VLC directives."""
        clean_filename = filename.replace(".m3u", "")
        lines = [
            "#EXTM3U",
            f"#EXTINF:-1,{clean_filename}",
            "#EXTVLCOPT:network-caching=1000",
            "#EXTVLCOPT:file-caching=1000",
            "#EXTVLCOPT:http-reconnect",
            "#EXTVLCOPT:input-fast-seek",
            "#EXTVLCOPT:avcodec-hw=any",
            "#EXTVLCOPT:avcodec-fast",
            "#EXTVLCOPT:deinterlace=-1",
            "#EXTVLCOPT:deinterlace-mode=auto",
            "#EXTVLCOPT:sub-language=eng,tam,hin,all",
            "#EXTVLCOPT:sub-track=0",
            "#EXTVLCOPT:audio-language=tam,eng,hin,tel,mal,kan,all",
            "#EXTVLCOPT:avcodec-threads=2"
        ]
        if sub_url:
            lines.append(f"#EXTVLCOPT:sub-file={sub_url}")
        lines.append(stream_url)
        return "\n".join(lines) + "\n"

    def build_playlist(self, items: list, host: str, title: str = "Season_Playlist") -> str:
        """Constructs a multi-item .m3u playlist for series/seasons with hardware acceleration."""
        import urllib.parse
        m3u_lines = ["#EXTM3U"]
        for item in items:
            cid = item.get("chat_id")
            mid = item.get("message_id")
            fn = item.get("filename", "episode.mp4")
            enc = urllib.parse.quote(fn)
            url = f"http://{host}/dl/{cid}/{mid}/{enc}"
            m3u_lines.append(f"#EXTINF:-1,{fn}")
            m3u_lines.append("#EXTVLCOPT:network-caching=1000")
            m3u_lines.append("#EXTVLCOPT:file-caching=1000")
            m3u_lines.append("#EXTVLCOPT:http-reconnect")
            m3u_lines.append("#EXTVLCOPT:input-fast-seek")
            m3u_lines.append("#EXTVLCOPT:avcodec-hw=any")
            m3u_lines.append("#EXTVLCOPT:deinterlace=-1")
            m3u_lines.append("#EXTVLCOPT:deinterlace-mode=auto")
            m3u_lines.append(url)
        return "\n".join(m3u_lines) + "\n"

    def get_vlc_live_status(self) -> Dict[str, Any]:
        """Queries VLC's active Lua HTTP status to provide real-time remote control state."""
        port = self._vlc_port or 8089
        auth_header = "Basic " + base64.b64encode(f":{self._vlc_password}".encode("ascii")).decode("ascii")
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/requests/status.json", headers={"Authorization": auth_header})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    time_sec = int(data.get("time", 0))
                    len_sec = int(data.get("length", 0))
                    vol = int(data.get("volume", 256))
                    vol_pct = min(200, max(0, round((vol / 256.0) * 100)))
                    state = data.get("state", "unknown")
                    fullscreen = bool(data.get("fullscreen", False))
                    meta = data.get("information", {}).get("category", {}).get("meta", {})
                    fn = meta.get("filename") or self._active_media_info.get("filename", "")
                    return {
                        "active": True,
                        "state": state,
                        "time": time_sec,
                        "length": len_sec,
                        "volume": vol,
                        "volume_percent": vol_pct,
                        "fullscreen": fullscreen,
                        "filename": fn,
                        "chat_id": self._active_media_info.get("chat_id"),
                        "message_id": self._active_media_info.get("message_id")
                    }
        except Exception as e:
            logger.debug(f"VLC status poll error: {e}")
        return {"active": False}

    def send_vlc_control(self, command: str, val: Optional[Union[str, int]] = None) -> Dict[str, Any]:
        """Dispatches playback, seek, and volume control commands directly to VLC."""
        if command == "pl_stop":
            self.kill_previous_player()
            return {"success": True, "state": "stopped", "active": False}

        port = self._vlc_port or 8089
        auth_header = "Basic " + base64.b64encode(f":{self._vlc_password}".encode("ascii")).decode("ascii")
        q_val = f"&val={urllib.parse.quote(str(val))}" if val is not None else ""
        url = f"http://127.0.0.1:{port}/requests/status.json?command={command}{q_val}"

        try:
            req = urllib.request.Request(url, headers={"Authorization": auth_header})
            with urllib.request.urlopen(req, timeout=1.2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    vol = int(data.get("volume", 256))
                    return {
                        "success": True,
                        "state": data.get("state"),
                        "time": data.get("time"),
                        "length": data.get("length"),
                        "volume": vol,
                        "volume_percent": min(200, max(0, round((vol / 256.0) * 100))),
                        "fullscreen": data.get("fullscreen")
                    }
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Command failed"}


player_launcher = PlayerLauncher()
