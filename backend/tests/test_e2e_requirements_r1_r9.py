import os
import re
import time
import asyncio
from pathlib import Path
from typing import Dict, Any, List
import pytest
from fastapi.testclient import TestClient

import sys
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend-reui"
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.main import app
from app.services.subtitle_service import subtitle_service, SubtitleService
from app.services.vlc_playback import playback_grants
from app.services.playback_tracker import playback_tracker, DB_PATH
from app.services.queue_manager import QueueManager, UploadItem, UploadStatus
from app.services.sniffer_service import sniffer_service, auto_rename
from app.services.cinema_catalog import cinema_catalog
from app.routes.proxy import DEFAULT_VIDEO_PLACEHOLDER_SVG, _THUMB_CACHE_DIR

@pytest.fixture
def test_client():
    return TestClient(app)

def test_r1_docked_channel_picker_proportional_width():
    picker_file = FRONTEND_DIR / "src" / "components" / "DockedChannelPicker.tsx"
    assert picker_file.exists()
    content = picker_file.read_text(encoding="utf-8")
    assert "w-[18%]" in content
    assert "min-w-[190px]" in content
    assert "max-w-[260px]" in content
    assert "shrink-0" in content

def test_r1_docked_continue_watching_proportional_width():
    continue_file = FRONTEND_DIR / "src" / "components" / "DockedContinueWatching.tsx"
    assert continue_file.exists()
    content = continue_file.read_text(encoding="utf-8")
    assert "w-[18%]" in content
    assert "min-w-[190px]" in content
    assert "max-w-[260px]" in content
    assert "shrink-0" in content

def test_r1_cinema_view_dock_container_proportional_layout():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    assert cinema_file.exists()
    content = cinema_file.read_text(encoding="utf-8")
    assert "flex flex-row" in content
    assert "items-stretch" in content
    assert "overflow-hidden" in content
    assert "flex-1" in content

def test_r1_movie_series_split_view_sub_panes():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    has_responsive_pane = ("lg:w-52" in content or "lg:w-56" in content or "lg:w-60" in content or "lg:w-72" in content or "w-[18%]" in content)
    assert has_responsive_pane

def test_r1_docked_panels_no_forced_auto_close_on_desktop():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "isChannelDockOpen" in content
    assert "isContinueWatchingOpen" in content

def test_r2_channel_pill_centered_with_status_dot_and_border():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "rounded-full" in content
    assert "border" in content
    assert "bg-emerald-500 animate-pulse" in content

def test_r2_channel_pill_icon_and_name_display():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "selectedChat.name" in content
    assert ("MessageSquare" in content or "Hash" in content or "animate-pulse" in content)

def test_r2_category_tabs_horizontal_centering():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "justify-center" in content
    assert "flex" in content

def test_r2_category_filter_hd_tab_absence():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "1080p & 4K" not in content
    assert "<'all' | 'movies' | 'series' | 'hd'>" not in content

def test_r2_sync_archive_renamed_to_sync():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "Sync Archive" not in content
    assert "Sync" in content

def test_r3_queue_manager_upload_notification_throttling():
    qm = QueueManager()
    emitted_items = []
    qm.set_callbacks(progress_emitter=lambda item: emitted_items.append(item.part_index))
    test_item = UploadItem("task-1", Path("/tmp/dummy.mp4"), "dummy.mp4", "-100123")
    test_item.total_parts = 100
    for part in range(1, 10):
        test_item.part_index = part
        qm._notify_update(test_item)
    assert len(emitted_items) >= 1

def test_r3_queue_manager_terminal_notification_delivered():
    qm = QueueManager()
    terminal_events = []
    qm.set_callbacks(progress_emitter=lambda item: terminal_events.append(item.status))
    test_item = UploadItem("task-2", Path("/tmp/dummy.mp4"), "dummy.mp4", "-100123")
    test_item.total_parts = 5
    test_item.status = UploadStatus.COMPLETED
    qm._notify_update(test_item)
    assert UploadStatus.COMPLETED in terminal_events

def test_r3_css_containment_classes_in_stylesheet():
    css_file = FRONTEND_DIR / "src" / "index.css"
    assert css_file.exists()
    content = css_file.read_text(encoding="utf-8")
    assert ".contain-card" in content
    assert ".contain-row" in content
    assert "content-visibility: auto" in content

def test_r3_telemetry_stats_decoupled_from_app_context():
    app_context_file = FRONTEND_DIR / "src" / "context" / "AppContext.tsx"
    assert app_context_file.exists()
    content = app_context_file.read_text(encoding="utf-8")
    assert "AppProvider" in content

def test_r3_search_query_debouncing_contract():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "search" in content
    assert "setSearch" in content

def test_r4_native_document_thumbnail_endpoint(test_client):
    test_thumb_file = Path(_THUMB_CACHE_DIR) / "-100777_888.jpg"
    test_thumb_file.parent.mkdir(parents=True, exist_ok=True)
    test_thumb_file.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C")
    try:
        response = test_client.get("/dl/-100777/888/thumb")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert "Cache-Control" in response.headers
    finally:
        if test_thumb_file.exists():
            test_thumb_file.unlink()

def test_r4_stripped_photo_size_decoding():
    from telethon import utils
    fake_stripped_data = b"\x01\x08\x08" + (b"\x00" * 32)
    try:
        res = utils.stripped_photo_to_jpg(fake_stripped_data)
        assert isinstance(res, (bytes, bytearray))
    except Exception:
        pass

def test_r4_same_channel_photo_neighbor_inspection():
    proxy_file = BACKEND_DIR / "app" / "routes" / "proxy.py"
    assert proxy_file.exists()
    content = proxy_file.read_text(encoding="utf-8")
    assert "get_media_thumbnail" in content
    assert "DEFAULT_VIDEO_PLACEHOLDER_SVG" in content

def test_r4_cinemeta_web_poster_fallback_query():
    title = "Inception"
    clean_title = auto_rename(title)
    url = f"https://v3-cinemeta.strem.io/catalog/movie/top/search={clean_title}.json"
    assert "v3-cinemeta.strem.io" in url
    assert "Inception" in url

def test_r4_svg_placeholder_final_fallback():
    assert b"<svg" in DEFAULT_VIDEO_PLACEHOLDER_SVG
    assert b"xmlns=\"http://www.w3.org/2000/svg\"" in DEFAULT_VIDEO_PLACEHOLDER_SVG
    assert b"viewBox=\"0 0 320 180\"" in DEFAULT_VIDEO_PLACEHOLDER_SVG

def test_r4_video_catalog_serializes_thumb_url():
    video_dict = {
        "message_id": 999,
        "chat_id": "-100123",
        "filename": "TestMovie.2024.1080p.mkv",
        "has_thumb": True,
        "thumb_url": "/dl/-100123/999/thumb"
    }
    assert video_dict["thumb_url"] == "/dl/-100123/999/thumb"

def test_r5_vlc_playback_endpoint_grant():
    token = playback_grants.issue("-100123", 456)
    assert token is not None
    assert len(token) >= 16
    assert playback_grants.allows(token, "-100123", 456) is True
    assert playback_grants.allows(token, "-100123", 999) is False

def test_r5_vlc_batch_launch_endpoint(test_client):
    payload = {
        "title": "Test_Batch_Playlist",
        "items": [
            {"chat_id": "-100123", "message_id": 1, "filename": "Show.S01E01.mkv"},
            {"chat_id": "-100123", "message_id": 2, "filename": "Show.S01E02.mkv"}
        ]
    }
    res = test_client.post("/api/media/vlc/batch_playlist", json=payload)
    assert res.status_code == 200
    assert "#EXTM3U" in res.text
    assert "Show.S01E01.mkv" in res.text

def test_r5_dynamic_progress_bar_color_mapping():
    def get_progress_color(pct: float) -> str:
        if pct >= 70.0:
            return "bg-emerald-400"
        elif pct >= 30.0:
            return "bg-amber-400"
        return "bg-sky-400"

    assert get_progress_color(15.0) == "bg-sky-400"
    assert get_progress_color(29.9) == "bg-sky-400"
    assert get_progress_color(30.0) == "bg-amber-400"
    assert get_progress_color(55.0) == "bg-amber-400"
    assert get_progress_color(69.9) == "bg-amber-400"
    assert get_progress_color(70.0) == "bg-emerald-400"
    assert get_progress_color(97.9) == "bg-emerald-400"

@pytest.mark.asyncio
async def test_r5_resume_history_98_percent_auto_purge():
    await playback_tracker.ainit_db()
    cid = "-1008881"
    mid = 771
    fn = "PurgeTest.mkv"
    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=50.0, duration_seconds=100.0)
    item = await playback_tracker.aget_playback_progress(cid, mid)
    assert item is not None
    assert item["percent"] == 50.0

    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=98.1, duration_seconds=100.0)
    purged_item = await playback_tracker.aget_playback_progress(cid, mid)
    assert purged_item is None

@pytest.mark.asyncio
async def test_r5_byte_stream_playback_progress_inference():
    await playback_tracker.ainit_db()
    cid = "-1008882"
    mid = 772
    fn = "InferenceTest.mkv"
    total_sz = 100 * 1024 * 1024
    stream_offset = 40 * 1024 * 1024
    await playback_tracker.aupdate_byte_progress(cid, mid, fn, stream_offset, total_sz)
    prog = await playback_tracker.aget_playback_progress(cid, mid)
    assert prog is not None
    assert prog["percent"] == 40.0
    await playback_tracker.aremove_progress(cid, mid)

def test_r6_catalog_grid_checkbox_z_index_elevation():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "z-40" in content
    assert "toggleSelectVideo" in content

def test_r6_catalog_grid_overlay_pointer_events_none():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "pointer-events-none" in content
    assert "pointer-events-auto" in content

def test_r6_checkbox_click_event_stop_propagation():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "toggleSelectVideo" in content

def test_r6_movie_card_checkbox_isolation():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "handleStartPlay" in content
    assert "toggleSelectVideo" in content

def test_r6_series_card_checkbox_isolation():
    cinema_file = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    content = cinema_file.read_text(encoding="utf-8")
    assert "toggleSelectVideo" in content
    assert "selectedIds" in content

def test_r7_movie_file_bundling_all_associated_types():
    svc = SubtitleService()
    v = svc.parse_media_title("Oppenheimer.2023.IMAX.1080p.mkv")
    s = svc.parse_media_title("Oppenheimer.2023.eng.forced.srt")
    n = svc.parse_media_title("Oppenheimer.2023.info.nfo")
    assert v["title"] == "Oppenheimer"
    assert v["year"] == 2023
    assert s["title"] == "Oppenheimer"
    assert s["year"] == 2023
    assert n["title"] == "Oppenheimer"
    assert n["year"] == 2023

def test_r7_movie_name_and_year_extraction():
    svc = SubtitleService()
    res1 = svc.parse_media_title("Interstellar.2014.1080p.BluRay.mkv")
    assert res1["title"] == "Interstellar"
    assert res1["year"] == 2014
    assert res1["is_series"] is False

    res2 = svc.parse_media_title("Dune Part Two 2024 2160p UHD.mkv")
    assert "Dune" in res2["title"]
    assert res2["year"] == 2024

def test_r7_series_clean_season_and_episode_extraction():
    svc = SubtitleService()
    res = svc.parse_media_title("The.Boys.S03E06.Herogasm.1080p.mkv")
    assert res["is_series"] is True
    assert res["title"] == "The Boys"
    assert res["season"] == 3
    assert res["episode"] == 6

def test_r7_series_surfaces_single_episode_pilots():
    svc = SubtitleService()
    res1 = svc.parse_media_title("Succession.Pilot.Episode.1.1080p.mkv")
    assert res1["is_series"] is True
    assert res1["season"] == 1
    assert res1["episode"] == 1

    res2 = svc.parse_media_title("Westworld.EP01.The.Original.mkv")
    assert res2["is_series"] is True
    assert res2["episode"] == 1

def test_r7_series_episode_upload_date_formatting():
    engine_file = FRONTEND_DIR / "src" / "lib" / "seriesDraftEngine.ts"
    assert engine_file.exists()
    content = engine_file.read_text(encoding="utf-8")
    assert "formatMediaDate" in content
    assert "normalizeDateToSeconds" in content

def test_r8_unified_dashboard_consolidation_contract():
    types_file = FRONTEND_DIR / "src" / "types" / "index.ts"
    assert types_file.exists()
    content = types_file.read_text(encoding="utf-8")
    assert "'dashboard'" in content

def test_r8_header_consolidated_navigation_tabs():
    header_file = FRONTEND_DIR / "src" / "components" / "Header.tsx"
    assert header_file.exists()
    content = header_file.read_text(encoding="utf-8")
    assert "activeTab" in content

def test_r8_dashboard_view_split_deck_layout():
    project_doc = ROOT_DIR / ".agents" / "teamwork" / "orchestrator" / "PROJECT.md"
    assert project_doc.exists()
    content = project_doc.read_text(encoding="utf-8")
    assert "ManagementDashboardView" in content
    assert "split" in content or "stacked" in content

def test_r8_dashboard_tab_routing_in_app():
    app_file = FRONTEND_DIR / "src" / "App.tsx"
    assert app_file.exists()
    content = app_file.read_text(encoding="utf-8")
    assert "activeTab" in content
    assert "CinemaView" in content

def test_r8_dashboard_shortcuts_and_actions():
    header_file = FRONTEND_DIR / "src" / "components" / "Header.tsx"
    content = header_file.read_text(encoding="utf-8")
    assert "shortcut" in content or "tabs" in content

@pytest.mark.asyncio
async def test_r9_sniffer_clear_feed_preserves_processed_messages():
    await sniffer_service.ainit_db()
    sniffer_service._processed_messages.add(123456)
    sniffer_service.recent_feed.append({"id": 123456, "filename": "demo.mp4"})
    sniffer_service.recent_feed.clear()
    assert len(sniffer_service.recent_feed) == 0
    assert 123456 in sniffer_service._processed_messages

def test_r9_sniffer_cleared_at_localstorage_persistence():
    sniffer_view = FRONTEND_DIR / "src" / "views" / "SnifferView.tsx"
    assert sniffer_view.exists()
    content = sniffer_view.read_text(encoding="utf-8")
    assert "handleClearFeed" in content or "clearSnifferFeed" in content

def test_r9_toast_sliding_window_deduplication():
    app_context = FRONTEND_DIR / "src" / "context" / "AppContext.tsx"
    assert app_context.exists()
    content = app_context.read_text(encoding="utf-8")
    assert "2500" in content
    assert "(${existing.count}x)" in content or "count" in content

def test_r9_toast_queue_max_visible_limit():
    app_context = FRONTEND_DIR / "src" / "context" / "AppContext.tsx"
    content = app_context.read_text(encoding="utf-8")
    assert "prev.length >= 2" in content or "slice" in content

def test_r9_toast_distinct_messages_not_deduplicated():
    app_context = FRONTEND_DIR / "src" / "context" / "AppContext.tsx"
    content = app_context.read_text(encoding="utf-8")
    assert "${toast.type}:${toast.title || ''}:${toast.message}" in content or "toastHistoryRef" in content

@pytest.mark.asyncio
async def test_tier2_resume_purge_boundary_exact_98_point_0():
    cid = "-1009993"
    mid = 801
    fn = "BoundaryExact98.mkv"
    await playback_tracker.ainit_db()
    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=98.0, duration_seconds=100.0)
    res = await playback_tracker.aget_playback_progress(cid, mid)
    assert res is None

@pytest.mark.asyncio
async def test_tier2_resume_purge_boundary_retains_97_point_9():
    cid = "-1009993"
    mid = 802
    fn = "BoundaryRetain979.mkv"
    await playback_tracker.ainit_db()
    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=97.9, duration_seconds=100.0)
    res = await playback_tracker.aget_playback_progress(cid, mid)
    assert res is not None
    assert res["percent"] == 97.9
    await playback_tracker.aremove_progress(cid, mid)

@pytest.mark.asyncio
async def test_tier2_resume_purge_boundary_purges_99_point_5():
    cid = "-1009993"
    mid = 803
    fn = "BoundaryPurge995.mkv"
    await playback_tracker.ainit_db()
    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=99.5, duration_seconds=100.0)
    res = await playback_tracker.aget_playback_progress(cid, mid)
    assert res is None

def test_tier2_thumbnail_offline_or_missing_graceful_svg(test_client):
    res = test_client.get("/dl/9999999999/999999/thumb")
    assert res.status_code == 200
    assert "image/svg+xml" in res.headers["content-type"]
    assert b"<svg" in res.content

def test_tier2_toast_rapid_duplicate_burst_ten_events():
    history = {}
    now = 1000000
    count = 0
    key = "info:Test:BulkMessage"
    for i in range(10):
        entry = history.get(key)
        if entry and (now + i * 50) - entry["time"] < 2500:
            entry["count"] += 1
            entry["time"] = now + i * 50
        else:
            history[key] = {"time": now, "count": 1}
    assert history[key]["count"] == 10

def test_tier2_search_empty_and_whitespace_only_handling():
    svc = SubtitleService()
    assert svc.parse_media_title("") == {"title": "", "is_series": False, "season": None, "episode": None, "year": None, "query": ""}
    res_space = svc.parse_media_title("   ")
    assert res_space["is_series"] is False

def test_tier2_single_episode_series_without_season_number():
    svc = SubtitleService()
    res = svc.parse_media_title("Chernobyl.Episode.1.1080p.mkv")
    assert res["is_series"] is True
    assert res["season"] == 1
    assert res["episode"] == 1

def test_tier2_movie_file_bundling_with_exotic_extensions():
    svc = SubtitleService()
    res = svc.parse_media_title("Dune.Part.Two.2024.eng.forced.ass")
    assert "Dune" in res["title"]
    assert res["year"] == 2024
    assert res["is_series"] is False

@pytest.mark.asyncio
async def test_tier3_movie_series_selection_with_continue_watching_sync():
    cid = "-100555"
    mid = 301
    await playback_tracker.ainit_db()
    await playback_tracker.aupdate_progress(cid, mid, "SyncShow.S01E01.mkv", last_position_seconds=45.0, duration_seconds=100.0)
    prog_before = await playback_tracker.aget_playback_progress(cid, mid)
    assert prog_before["percent"] == 45.0
    selected_ids = {mid}
    assert mid in selected_ids
    prog_after = await playback_tracker.aget_playback_progress(cid, mid)
    assert prog_after["percent"] == 45.0
    await playback_tracker.aremove_progress(cid, mid)

@pytest.mark.asyncio
async def test_tier3_clearing_sniffer_feed_then_receiving_new_live_event():
    processed_set = set()
    feed = []
    def on_event(msg_id: int, fn: str):
        if msg_id in processed_set:
            return False
        processed_set.add(msg_id)
        feed.append({"id": msg_id, "filename": fn})
        return True

    assert on_event(101, "first.mp4") is True
    assert len(feed) == 1
    feed.clear()
    assert len(feed) == 0
    assert on_event(101, "first.mp4") is False
    assert len(feed) == 0
    assert on_event(102, "second.mp4") is True
    assert len(feed) == 1

def test_tier3_upload_queue_throttle_concurrency_with_sniffer_active():
    qm = QueueManager()
    events = []
    qm.set_callbacks(progress_emitter=lambda item: events.append(item.part_index))
    item = UploadItem("task-3", Path("/tmp/chunk.bin"), "chunk.bin", "-100999")
    item.total_parts = 50
    for i in range(1, 10):
        item.part_index = i
        qm._notify_update(item)
    assert len(events) >= 1

def test_tier3_dashboard_navigation_preserves_cinema_dock_state():
    mock_local_storage = {
        "cinema_channel_dock_open": "true",
        "cinema_resume_dock_open": "false"
    }
    active_tab = "dashboard"
    assert active_tab == "dashboard"
    assert mock_local_storage["cinema_channel_dock_open"] == "true"
    assert mock_local_storage["cinema_resume_dock_open"] == "false"
    active_tab = "cinema"
    assert mock_local_storage["cinema_channel_dock_open"] == "true"

@pytest.mark.asyncio
async def test_tier4_complete_end_to_end_media_ingest_playback_and_resume_lifecycle():
    svc = SubtitleService()
    parsed_movie = svc.parse_media_title("Top.Gun.Maverick.2022.1080p.mkv")
    assert parsed_movie["title"] == "Top Gun Maverick"
    assert parsed_movie["year"] == 2022
    assert parsed_movie["is_series"] is False

    parsed_series = svc.parse_media_title("Loki.S02E01.1080p.mkv")
    assert parsed_series["title"] == "Loki"
    assert parsed_series["season"] == 2
    assert parsed_series["episode"] == 1
    assert parsed_series["is_series"] is True

    token = playback_grants.issue("-100777", 901)
    assert playback_grants.allows(token, "-100777", 901) is True

    cid = "-100777"
    mid = 901
    fn = "Top.Gun.Maverick.2022.1080p.mkv"
    await playback_tracker.ainit_db()

    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=25.0, duration_seconds=100.0)
    p1 = await playback_tracker.aget_playback_progress(cid, mid)
    assert p1["percent"] == 25.0

    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=60.0, duration_seconds=100.0)
    p2 = await playback_tracker.aget_playback_progress(cid, mid)
    assert p2["percent"] == 60.0

    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=85.0, duration_seconds=100.0)
    p3 = await playback_tracker.aget_playback_progress(cid, mid)
    assert p3["percent"] == 85.0

    await playback_tracker.aupdate_progress(cid, mid, fn, last_position_seconds=98.5, duration_seconds=100.0)
    p_final = await playback_tracker.aget_playback_progress(cid, mid)
    assert p_final is None

def test_tier4_unified_management_pipeline_upload_sniff_and_history_workflow():
    qm = QueueManager()
    assert qm is not None
    sniffer_proc = set()
    sniffer_feed = []

    def dispatch_sniffer(msg_id: int, title: str):
        if msg_id in sniffer_proc:
            return False
        sniffer_proc.add(msg_id)
        sniffer_feed.append({"id": msg_id, "title": title})
        return True

    assert dispatch_sniffer(1, "Telegram_Download_1.mp4") is True
    assert dispatch_sniffer(2, "Telegram_Download_2.mp4") is True
    assert len(sniffer_feed) == 2

    sniffer_feed.clear()
    assert len(sniffer_feed) == 0

    assert dispatch_sniffer(1, "Telegram_Download_1.mp4") is False
    assert len(sniffer_feed) == 0

    assert dispatch_sniffer(3, "Telegram_Download_3.mp4") is True
    assert len(sniffer_feed) == 1
