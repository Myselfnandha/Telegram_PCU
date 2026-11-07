import os
import sys
import time
import signal
import asyncio
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import app
from app.services.download_manager import DownloadManager, DownloadStatus
from app.services.queue_manager import QueueManager, UploadItem, UploadStatus
from app.services.rust_bridge import RustBridge
import run as run_module


@pytest.mark.asyncio
async def test_adversarial_rust_bridge_non_blocking_event_loop():
    rb = RustBridge()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(5)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    await asyncio.sleep(0.5)
    rb._process = proc

    tick_count = 0
    stop_ticks = False

    async def ticker():
        nonlocal tick_count
        while not stop_ticks:
            tick_count += 1
            await asyncio.sleep(0.05)

    ticker_task = asyncio.create_task(ticker())
    await rb.stop_daemon()
    stop_ticks = True
    await ticker_task

    assert tick_count >= 15
    assert proc.poll() is not None


@pytest.mark.asyncio
async def test_adversarial_lifespan_repeated_cycles_under_queued_tasks(tmp_path):
    os.environ["TG_TEST_MODE"] = "1"
    dummy_file = tmp_path / "dummy.bin"
    dummy_file.write_bytes(b"data" * 100)

    from app.services.queue_manager import queue_manager
    from app.services.download_manager import download_manager

    for cycle in range(5):
        async with app.router.lifespan_context(app):
            assert queue_manager._is_running is True
            assert download_manager._worker_task is not None
            for i in range(3):
                item = UploadItem(
                    task_id=f"adv_{cycle}_{i}",
                    original_filename=f"adv_{i}.bin",
                    file_path=dummy_file,
                    chat_id="12345"
                )
                await queue_manager.add_task(item)
        assert queue_manager._is_running is False
        assert download_manager._worker_task is None

    current_tasks = [t for t in asyncio.all_tasks() if not t.done()]
    assert len(current_tasks) <= 2


@pytest.mark.asyncio
async def test_adversarial_queue_manager_active_upload_cancellation(tmp_path):
    test_file = tmp_path / "upload.bin"
    test_file.write_bytes(b"content" * 500)

    qm = QueueManager()
    qm.max_concurrent = 1
    qm.start_workers()

    async def slow_sender(**kwargs):
        for _ in range(50):
            await asyncio.sleep(0.05)
        return None

    mock_client = AsyncMock()
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    with patch("app.services.queue_manager.send_file_to_telegram", slow_sender), \
         patch("app.services.queue_manager.TelegramClientManager.get_active_streaming_clients", AsyncMock(return_value=[mock_client])), \
         patch("app.services.queue_manager.TelegramClientManager.get_client", AsyncMock(return_value=mock_client)):

        item = UploadItem(
            task_id="adv_active_1",
            original_filename="upload.bin",
            file_path=test_file,
            chat_id="999"
        )
        await qm.add_task(item)

        for _ in range(20):
            await asyncio.sleep(0.05)
            if item.status == UploadStatus.UPLOADING:
                break

        assert item.status == UploadStatus.UPLOADING
        await asyncio.wait_for(qm.stop_workers(), timeout=2.0)
        assert qm._workers == []
        assert qm._scheduler_task is None
        assert qm._is_running is False


def test_adversarial_pid_lock_isolation_and_edge_cases(tmp_path):
    test_data = tmp_path / "data"
    test_pid = test_data / "test.pid"

    orig_data_dir = run_module.DATA_DIR
    orig_pid_file = run_module.PID_FILE
    orig_subproc = subprocess.run

    def mock_subprocess_run(cmd, *args, **kwargs):
        if "systemctl" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return orig_subproc(cmd, *args, **kwargs)

    run_module.DATA_DIR = test_data
    run_module.PID_FILE = test_pid
    subprocess.run = mock_subprocess_run

    try:
        ok, pid = run_module.acquire_pid_lock()
        assert ok is True
        assert pid == os.getpid()
        assert test_pid.read_text().strip() == str(os.getpid())
        run_module.release_pid_lock()
        assert not test_pid.exists()

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        try:
            test_data.mkdir(parents=True, exist_ok=True)
            test_pid.write_text(str(child.pid))
            collision_ok, active_pid = run_module.acquire_pid_lock()
            assert collision_ok is False
            assert active_pid == child.pid
        finally:
            child.kill()
            child.wait()

        stale_ok, new_pid = run_module.acquire_pid_lock()
        assert stale_ok is True
        assert new_pid == os.getpid()
        run_module.release_pid_lock()

        test_pid.write_text("CORRUPTED_NON_NUMERIC_GARBAGE")
        rec_ok, rec_pid = run_module.acquire_pid_lock()
        assert rec_ok is True
        assert rec_pid == os.getpid()
        run_module.release_pid_lock()

        test_pid.write_text("")
        empty_ok, empty_pid = run_module.acquire_pid_lock()
        assert empty_ok is True
        assert empty_pid == os.getpid()
        run_module.release_pid_lock()

    finally:
        run_module.DATA_DIR = orig_data_dir
        run_module.PID_FILE = orig_pid_file
        subprocess.run = orig_subproc


def test_adversarial_pid_lock_negative_pid_vulnerability(tmp_path):
    test_data = tmp_path / "data"
    test_pid = test_data / "test.pid"

    orig_data_dir = run_module.DATA_DIR
    orig_pid_file = run_module.PID_FILE
    orig_subproc = subprocess.run

    def mock_subprocess_run(cmd, *args, **kwargs):
        if "systemctl" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return orig_subproc(cmd, *args, **kwargs)

    run_module.DATA_DIR = test_data
    run_module.PID_FILE = test_pid
    subprocess.run = mock_subprocess_run

    try:
        test_data.mkdir(parents=True, exist_ok=True)
        test_pid.write_text("-1")
        pid = run_module.get_running_pid()
        assert pid is None or pid > 0, f"VULNERABILITY: get_running_pid returned non-positive PID {pid}"
    finally:
        run_module.DATA_DIR = orig_data_dir
        run_module.PID_FILE = orig_pid_file
        subprocess.run = orig_subproc


@pytest.mark.asyncio
async def test_adversarial_download_manager_active_stream_cancellation(tmp_path):
    dm = DownloadManager()
    dm.start()

    class MockStream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @property
        def status_code(self):
            return 200
        @property
        def headers(self):
            return {"Content-Length": "10485760"}
        async def aiter_bytes(self, chunk_size=262144):
            for _ in range(100):
                await asyncio.sleep(0.05)
                yield b"x" * chunk_size

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def stream(self, method, url, **kwargs):
            return MockStream()

    with patch("httpx.AsyncClient", MockClient):
        task = await dm.add_url_download("http://example.com/test.bin")
        while task.status != DownloadStatus.DOWNLOADING or task.downloaded_bytes == 0:
            await asyncio.sleep(0.01)

        stop_task = asyncio.create_task(dm.stop())
        done, _ = await asyncio.wait([stop_task], timeout=0.5)
        if stop_task not in done:
            stop_task.cancel()
            if dm._worker_task and not dm._worker_task.done():
                dm._worker_task.cancel()
            pytest.fail("DownloadManager.stop() deadlocked on shutdown during active stream download (worker loop blocked on _queue.get())")

        assert dm._worker_task is None
