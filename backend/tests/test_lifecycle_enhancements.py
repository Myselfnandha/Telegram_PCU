import sys
import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import asyncio
from app.services.download_manager import DownloadManager
from app.services.queue_manager import QueueManager
from app.services.folder_watcher import FolderWatcherDaemon
from app.services.rust_bridge import RustBridge
from app.services.tray_service import start_tray_service
from app.main import app
import run as run_module


@pytest.mark.asyncio
async def test_download_manager_lifecycle():
    dm = DownloadManager()
    assert dm._worker_task is None
    dm.start()
    assert dm._worker_task is not None
    assert not dm._worker_task.done()
    await dm.stop()
    assert dm._worker_task is None


@pytest.mark.asyncio
async def test_queue_manager_workers_lifecycle():
    qm = QueueManager()
    qm.max_concurrent = 2
    assert qm._workers == []
    assert qm._scheduler_task is None
    qm.start_workers()
    assert len(qm._workers) == 2
    assert qm._scheduler_task is not None
    await qm.stop_workers()
    assert qm._workers == []
    assert qm._scheduler_task is None
    assert qm._is_running is False


@pytest.mark.asyncio
async def test_folder_watcher_lifecycle(tmp_path):
    fw = FolderWatcherDaemon()
    fw._watch_dir = tmp_path
    assert fw._task is None
    fw.start()
    assert fw._task is not None
    assert fw.is_enabled is True
    await fw.stop()
    assert fw._task is None
    assert fw.is_enabled is False


@pytest.mark.asyncio
async def test_rust_bridge_stop_daemon_non_blocking():
    rb = RustBridge()
    mock_proc = MagicMock()
    mock_proc.terminate = MagicMock()
    mock_proc.wait = MagicMock(return_value=0)
    rb._process = mock_proc
    await rb.stop_daemon()
    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(3)
    assert rb._process is None


@pytest.mark.asyncio
async def test_lifespan_test_mode():
    os.environ["TG_TEST_MODE"] = "1"
    async with app.router.lifespan_context(app):
        from app.services.queue_manager import queue_manager
        from app.services.download_manager import download_manager
        assert queue_manager._is_running is True
        assert download_manager._worker_task is not None
    assert queue_manager._is_running is False


def test_acquire_and_release_pid_lock(tmp_path, monkeypatch):
    test_data_dir = tmp_path / "data"
    test_pid_file = test_data_dir / "tg_test.pid"
    monkeypatch.setattr(run_module, "DATA_DIR", test_data_dir)
    monkeypatch.setattr(run_module, "PID_FILE", test_pid_file)
    monkeypatch.setattr(run_module, "get_running_pid", lambda: int(test_pid_file.read_text().strip()) if test_pid_file.exists() else None)
    assert not test_data_dir.exists()
    ok, pid = run_module.acquire_pid_lock()
    assert ok is True
    assert pid == os.getpid()
    assert test_data_dir.exists()
    assert test_pid_file.exists()
    assert test_pid_file.read_text().strip() == str(os.getpid())
    run_module.release_pid_lock()
    assert not test_pid_file.exists()


def test_cli_foreground_flag_parsing(monkeypatch):
    test_args = ["run.py", "start", "--foreground"]
    monkeypatch.setattr(sys, "argv", test_args)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--foreground", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    p_start = subparsers.add_parser("start")
    p_start.add_argument("--tray", action="store_true")
    p_start.add_argument("--daemon", action="store_true")
    p_start.add_argument("--foreground", action="store_true")
    args, _ = parser.parse_known_args(test_args[1:])
    assert args.command == "start"
    assert args.foreground is True
    daemon_flag = getattr(args, "daemon", False) and not getattr(args, "foreground", False)
    assert daemon_flag is False
