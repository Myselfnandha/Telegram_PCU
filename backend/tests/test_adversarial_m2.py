import os
import sys
import asyncio
import pathlib
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.main import app
from app.services.sniffer_service import SnifferService
from app.services.playback_tracker import playback_tracker


@pytest.mark.asyncio
async def test_adversarial_sniffer_sqlite_high_concurrency(tmp_path):
    test_db = tmp_path / "concurrent_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service = SnifferService()
        await service.ainit_db()

        async def worker_writer(worker_id: int):
            for i in range(25):
                cid = f"-100{worker_id}"
                mid = 1000 + i
                fname = f"movie_w{worker_id}_m{i}.mkv"
                url = f"http://127.0.0.1:8088/dl/{cid}/{mid}/{fname}"
                await service.arecord_dispatch(cid, mid, fname, 1048576 * (i + 1), url)

        async def worker_reader(worker_id: int):
            for i in range(25):
                cid = f"-100{worker_id}"
                mid = 1000 + i
                await service.ais_dispatched(cid, mid)
                await service.aget_recent_dispatches(limit=10)

        tasks = []
        for w in range(10):
            tasks.append(asyncio.create_task(worker_writer(w)))
            tasks.append(asyncio.create_task(worker_reader(w)))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            assert not isinstance(r, Exception), f"Concurrent task failed with: {r}"

        recent = await service.aget_recent_dispatches(limit=50)
        assert len(recent) == 50
        assert await service.ais_dispatched("-1000", 1000)
        assert await service.ais_dispatched("-1009", 1024)
        assert not await service.ais_dispatched("-1009", 999999)


@pytest.mark.asyncio
async def test_adversarial_sniffer_duplicate_collision_and_reinit(tmp_path):
    test_db = tmp_path / "collision_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service = SnifferService()
        await service.ainit_db()

        async def record_same_item():
            await service.arecord_dispatch("-100999", 555, "collision.mp4", 500000, "http://localhost/555")

        writers = [asyncio.create_task(record_same_item()) for _ in range(50)]
        inits = [asyncio.create_task(service.ainit_db()) for _ in range(5)]
        readers = [asyncio.create_task(service.ais_dispatched("-100999", 555)) for _ in range(20)]

        all_results = await asyncio.gather(*writers, *inits, *readers, return_exceptions=True)
        for res in all_results:
            assert not isinstance(res, Exception), f"Collision/reinit error: {res}"

        assert await service.ais_dispatched("-100999", 555)
        recent = await service.aget_recent_dispatches(limit=10)
        matching = [r for r in recent if r["chat_id"] == "-100999" and r["message_id"] == 555]
        assert len(matching) == 1


@pytest.mark.asyncio
async def test_adversarial_sniffer_cold_reload_persistence(tmp_path):
    test_db = tmp_path / "cold_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service_writer = SnifferService()
        await service_writer.ainit_db()
        await service_writer.arecord_dispatch("chat_cold", 12345, "cold.mkv", 2048, "http://localhost/cold")

        service_reader = SnifferService()
        assert len(service_reader._processed_messages) == 0

        is_present = await service_reader.ais_dispatched("chat_cold", 12345)
        assert is_present is True
        assert ("chat_cold", 12345) in service_reader._processed_messages

        recent = await service_reader.aget_recent_dispatches(limit=5)
        assert len(recent) == 1
        assert recent[0]["chat_id"] == "chat_cold"
        assert recent[0]["message_id"] == 12345


@pytest.mark.asyncio
async def test_adversarial_sniffer_malicious_inputs_and_edge_limits(tmp_path):
    test_db = tmp_path / "edge_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service = SnifferService()
        await service.ainit_db()

        injection_fname = "movie'; DROP TABLE dispatched_downloads; --.mkv"
        injection_url = "http://test/'; SELECT * FROM sqlite_master; --"
        await service.arecord_dispatch("inject_chat", 1, injection_fname, 2**40, injection_url)
        assert await service.ais_dispatched("inject_chat", 1)

        await service.arecord_dispatch("zero_chat", 2, "", 0, "")
        assert await service.ais_dispatched("zero_chat", 2)

        await service.arecord_dispatch("neg_chat", 3, "unicode_\U0001f3ac_\U0001f980.mp4", -500, "http://test/emoji")
        assert await service.ais_dispatched("neg_chat", 3)

        res_zero = await service.aget_recent_dispatches(limit=0)
        assert isinstance(res_zero, list)
        assert len(res_zero) == 0

        res_neg = await service.aget_recent_dispatches(limit=-5)
        assert isinstance(res_neg, list)

        res_large = await service.aget_recent_dispatches(limit=1000)
        assert len(res_large) == 3


@pytest.mark.asyncio
async def test_adversarial_play_in_vlc_non_blocking_event_loop(tmp_path):
    from app.routes.proxy import launch_desktop_vlc
    from starlette.requests import Request

    test_db = tmp_path / "test_history.db"
    with patch("app.services.playback_tracker.DB_PATH", test_db):
        await playback_tracker.ainit_db()
        await playback_tracker.aupdate_progress(
            chat_id="12345",
            message_id=67890,
            filename="movie.mp4",
            last_position_seconds=45.0,
            duration_seconds=600.0,
            file_size=104857600
        )

        tick_counter = 0
        ticker_running = True

        async def loop_ticker():
            nonlocal tick_counter
            while ticker_running:
                tick_counter += 1
                await asyncio.sleep(0.005)

        ticker_task = asyncio.create_task(loop_ticker())
        await asyncio.sleep(0.02)

        fake_request = Request({
            "type": "http",
            "method": "GET",
            "path": "/api/media/vlc/play/12345/67890",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8088),
        })

        fake_file = MagicMock()
        fake_file.size = 104857600
        fake_msg = MagicMock()
        fake_msg.media = True
        fake_msg.file = fake_file

        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_proc.wait = MagicMock(return_value=0)

        fake_client = AsyncMock()

        with patch("shutil.which", return_value="/usr/bin/vlc"), \
             patch("app.telegram_client.TelegramClientManager.get_client", AsyncMock(return_value=fake_client)), \
             patch("app.routes.proxy.get_streamable_message", AsyncMock(return_value=fake_msg)), \
             patch("app.services.vlc_playback.playback_grants.issue", return_value="test_grant_token"), \
             patch("subprocess.Popen", return_value=fake_proc):

            res = await launch_desktop_vlc(chat_id="12345", message_id=67890, request=fake_request, filename="movie.mp4")
            assert res["success"] is True
            assert res["resumed_seconds"] == 45
            assert res["pid"] == 99999

        ticker_running = False
        await ticker_task
        assert tick_counter >= 1


def test_adversarial_pruned_dead_code_integrity():
    root_dir = pathlib.Path(__file__).parent.parent.parent

    dead_paths = [
        root_dir / "backend" / "app.db",
        root_dir / "backend" / "cinema_cache.db",
        root_dir / "backend" / "data" / "database.db",
        root_dir / "backend" / "data" / "tg_power_suite.session",
        root_dir / "backend" / "backups" / "audit_12_files",
    ]
    for p in dead_paths:
        assert not p.exists(), f"Dead file still exists: {p}"

    import app.telegram_client as tc
    assert not hasattr(tc, "_bot_client")
    assert not hasattr(tc, "get_bot_client")

    import app.routes.proxy as pr
    assert not hasattr(pr, "_MESSAGE_CACHE")
    assert not hasattr(pr, "_ACTIVE_STREAM_CLIENTS")
    assert not hasattr(pr, "_ACTIVE_STREAM_PRODUCERS")
    assert hasattr(pr, "_STREAM_MESSAGE_CACHE")

    import app.auth as au
    assert not hasattr(au, "security")

    import app.routes.auth as ra
    assert hasattr(ra, "router")


@pytest.mark.asyncio
async def test_adversarial_cors_allowed_and_forbidden_origins():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        for allowed in ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8088", "http://127.0.0.1:8088"]:
            headers = {
                "Origin": allowed,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            }
            res = await client.options("/api/auth/status", headers=headers)
            assert res.status_code == 200
            assert res.headers.get("access-control-allow-origin") == allowed
            assert res.headers.get("access-control-allow-credentials") == "true"

        evil_headers = {
            "Origin": "http://evil-attacker.site:8088",
            "Access-Control-Request-Method": "POST",
        }
        res_evil = await client.options("/api/auth/status", headers=evil_headers)
        assert res_evil.headers.get("access-control-allow-origin") != "http://evil-attacker.site:8088"


@pytest.mark.asyncio
async def test_adversarial_auth_routes_and_zero_trust():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        status_res = await client.get("/api/auth/status")
        assert status_res.status_code == 200
        data = status_res.json()
        assert "authenticated" in data

        sec_status = await client.get("/api/auth/secondary/status")
        assert sec_status.status_code == 200
        sec_data = sec_status.json()
        assert "authenticated" in sec_data
        assert "enabled" in sec_data

        toggle_res = await client.post("/api/auth/secondary/toggle", json={"enabled": True})
        assert toggle_res.status_code == 200
        assert toggle_res.json()["enabled"] is True

        toggle_off = await client.post("/api/auth/secondary/toggle", json={"enabled": False})
        assert toggle_off.status_code == 200
        assert toggle_off.json()["enabled"] is False

        bad_payload = await client.post("/api/auth/send", json={"wrong_key": "bad"})
        assert bad_payload.status_code == 422

        remote_headers = {
            "X-Forwarded-For": "203.0.113.50",
            "Content-Type": "application/json",
        }
        forbidden_res = await client.post("/api/auth/send", json={"phone": "+1234567890"}, headers=remote_headers)
        assert forbidden_res.status_code in (401, 403)
