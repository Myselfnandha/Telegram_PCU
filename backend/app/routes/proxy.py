"""
TG Power Suite — Proxy Router Aggregation Facade.
Decomposed into modular sub-routers:
- proxy_streaming.py: MTProto high-performance streaming, Range parsing, direct downloads, and transmuxing.
- proxy_vlc.py: Desktop VLC player launching, loopback restriction, m3u playlists, and watch progress.
- proxy_thumbnails.py: Multi-stage thumbnail generation, web posters, and hover keyframe scrubbing.
- proxy_subtitles.py: Embedded subtitle extraction, WebVTT/SRT conversion, and prefetching.
"""

from fastapi import APIRouter

# Import sub-routers
from app.routes.proxy_streaming import router as streaming_router
from app.routes.proxy_vlc import router as vlc_router
from app.routes.proxy_thumbnails import router as thumbnails_router
from app.routes.proxy_subtitles import router as subtitles_router

# Initialize primary proxy router and mount sub-routers
router = APIRouter(tags=["proxy"])
router.include_router(streaming_router)
router.include_router(vlc_router)
router.include_router(thumbnails_router)
router.include_router(subtitles_router)

# Re-export public symbols from proxy_streaming for backward compatibility
from app.routes.proxy_streaming import (
    StreamIteratorHandle,
    parse_http_range,
    _prefetch_non_watched_blocks,
    prewarm_media_stream,
    compute_dynamic_align_unit,
    handle_proxy_download,
    handle_transmux_stream,
    probe_media_streams,
    get_chat_videos,
    trigger_proxy_download,
    get_stream_cache_status,
    clear_stream_cache,
    get_stream_buffer_stats,
    get_mobile_playback_info,
    prewarm_media_endpoint,
    FILE_REF_ERRORS,
    _STREAM_MESSAGE_CACHE,
    _ACTIVE_STREAM_TASKS,
    _ACTIVE_MEDIA_CONNS,
    _ACTIVE_STREAM_ITERS,
    _ACTIVE_PREFETCH_TASKS,
    _PREBORROW_ACTIVE_DCS,
    _PREBORROW_LOCK,
    _STREAM_PROBE_CACHE,
)

# Re-export public symbols from proxy_thumbnails
from app.routes.proxy_thumbnails import (
    DEFAULT_VIDEO_PLACEHOLDER_SVG,
    _THUMB_CACHE_DIR,
    _PREVIEW_CACHE_DIR,
    _THUMB_SEMAPHORE,
    search_web_poster,
    get_media_thumbnail,
    get_media_preview_frame,
)

# Re-export public symbols from proxy_subtitles
from app.routes.proxy_subtitles import (
    get_stream_subtitles,
    get_subtitle_status,
    prefetch_media_subtitles,
    download_media_subtitles,
    _SUBTITLE_CACHE,
)

# Re-export public symbols from proxy_vlc
from app.routes.proxy_vlc import (
    VlcPlayPayload,
    PlaybackProgressPayload,
    play_desktop_vlc,
    launch_desktop_vlc,
    launch_vlc_batch,
    generate_batch_playlist,
    generate_single_vlc_playlist,
    get_continue_watching,
    dismiss_continue_watching,
    update_playback_progress_api,
)

# Re-export services and helpers previously accessible from proxy.py
from app.services.cinema_catalog import cinema_catalog, get_streamable_message
from app.services.rate_governor import rate_governor
from app.services.stream_cache import stream_cache_service, BLOCK_SIZE
from app.services.subtitle_service import subtitle_service
from app.services.playback_tracker import playback_tracker
from app.services.vlc_playback import playback_grants
from app.services.stream_saver import stream_saver_service
from app.routes.history import get_cinema_cached_videos_db, save_cinema_cached_videos_db
from app.services.stream_buffer import stream_buffer, _SLIDING_STREAM_BUFFER, StreamSlidingBuffer
from app.services.stream_engine import stream_engine
from app.routes.settings import (
    OfflineSaverPayload,
    get_offline_saver_settings,
    set_offline_saver_settings,
)
