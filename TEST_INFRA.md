# Test Infrastructure Documentation

## 1. Test Architecture Overview

The TG_TOOL automated verification infrastructure employs a systematic 4-tier requirement-driven methodology designed to validate application stability, UI responsiveness, media streaming fidelity, and data consistency without relying on fragile implementation coupling.

```
                              ┌────────────────────────────────────────┐
                              │          Test Infrastructure           │
                              │       TG_TOOL Power Suite v2           │
                              └────────────────────────────────────────┘
                                                  │
         ┌───────────────────┬────────────────────┼────────────────────┐
         ▼                   ▼                    ▼                    ▼
  ┌─────────────┐     ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
  │   Tier 1    │     │   Tier 2    │      │   Tier 3    │      │   Tier 4    │
  │   Feature   │     │  Boundary   │      │ Cross-Feat. │      │ Real-World  │
  │  Coverage   │     │  & Corners  │      │ Interaction │      │  Workflows  │
  │  (R1 - R9)  │     │             │      │  (Pairwise) │      │             │
  └─────────────┘     └─────────────┘      └─────────────┘      └─────────────┘
```

### 4-Tier Test Classification

1. **Tier 1: Feature Coverage (>=5 tests per requirement)**:
   - Primary behavioral verification and contract guarantees across all nine core requirements (R1 through R9).
   - Validates that every individual feature functions according to specifications under standard conditions.
2. **Tier 2: Boundary & Corner Cases**:
   - Precise numerical thresholds (e.g. 98.0% exact resume purge boundary, 97.9% retention, 99.5% purge).
   - Graceful fallback for offline, network timeouts, and missing remote resources (e.g. SVG fallback).
   - Rapid-burst debouncing stress (e.g. 10 duplicate toasts in 500ms).
   - Single-episode vs multi-season parsing and exotic file extensions.
3. **Tier 3: Cross-Feature Interactions (Pairwise)**:
   - State isolation when multiple features operate concurrently (e.g. selecting cards while continue watching progress updates).
   - Sniffer feed clearing persistence across new incoming Telegram live socket events.
   - Concurrency between upload queue throttling and real-time message sniffing.
   - Viewport and dock layout persistence across tab navigation.
4. **Tier 4: Real-World Workload Scenarios**:
   - End-to-end full lifecycle execution: ingest mixed Telegram media channels (movies, series, subtitles), resolve thumbnails, launch desktop VLC, track resume tiers (<30%, 30-70%, 70-98%), and verify auto-purge.
   - Unified management workflow: upload queue dispatching, FDM sniffer processing, feed clearing, and history tracking.

---

## 2. Feature Inventory & Mapping

| Req | Feature Name | Test Modules | Key Assertions & Contracts |
|---|---|---|---|
| **R1** | Proportional Screen Fit Layout | `test_e2e_requirements_r1_r9.py` | `DockedChannelPicker` (`w-[18%] min-w-[190px] max-w-[260px]`), `DockedContinueWatching` (`w-[18%] min-w-[190px] max-w-[260px]`), `CinemaView` flex layout (`flex-1 min-w-0`), responsive split-view sub-panes (`lg:w-52` / `xl:w-60`), non-clipping sidebars without forced mutual exclusion. |
| **R2** | Header & Navigation UI | `test_e2e_requirements_r1_r9.py` | Channel pill with border, status dot (`bg-emerald-500 animate-pulse`), icon and channel name; category tabs horizontally centered across viewport sizes; absence of legacy "1080p & 4K" ('hd') tab; "Sync Archive" renamed to "Sync". |
| **R3** | Frontend Performance Optimization | `test_e2e_requirements_r1_r9.py` | QueueManager `_notify_update(item)` throttles rapid chunk updates within 0.5s rate-limit window; terminal events delivered immediately; CSS containment utility classes (`.contain-card`, `.contain-row`, `content-visibility: auto`); telemetry stats decoupled from AppContext root provider; search debouncing contracts. |
| **R4** | Thumbnail Display & Multi-Stage Fallback | `test_e2e_requirements_r1_r9.py`, `test_thumbnail_multistage.py` | Multi-tier resolution: (1) native document thumbs, (2) PhotoStrippedSize/PhotoCachedSize decoding, (3) same-channel photo inspection (`grouped_id` or `id ± 1, 2`), (4) Cinemeta web poster search lookup, (5) default SVG placeholder (`image/svg+xml`) with caching headers; serialization of `thumb_url`. |
| **R5** | Media Playback & Resume Tracking | `test_e2e_requirements_r1_r9.py`, `test_vlc_resume_purge.py` | VLC primary playback launch (`api.launchVlc` / `/api/media/vlc/launch`); batch M3U playlist generation; multi-tier dynamic progress bar color classification (<30% sky/blue, 30-70% amber, 70-98% emerald); 98% auto-purge from SQLite `playback_history`; byte-stream offset progress inference. |
| **R6** | Grid Selection Checkbox Fix | `test_e2e_requirements_r1_r9.py` | Selection checkbox container elevated to `z-40`; hover overlay backdrop has `pointer-events-none` with `pointer-events-auto` on play buttons; checkbox click handlers call `stopPropagation()`; isolated selection across catalog grid, movie split, movie bundled, series split, and series grouped card presentations. |
| **R7** | Movie & Series Categorization | `test_e2e_requirements_r1_r9.py`, `test_media_categorization.py` | Movie clustering bundles non-video files (`.srt`, `.vtt`, `.nfo`, `.txt`, `.ass`) under Movie Title (Year); clean series title, season, and episode parsing across `S01E01`, `1x02`, `Season 1 Episode 2`; 100% of single-episode pilots surfaced under series; upload date timestamp preservation. |
| **R8** | Unified Management Dashboard | `test_e2e_requirements_r1_r9.py` | Consolidation of Uploader, Sniffer & FDM, and History views into `ManagementDashboardView.tsx`; 2-column split deck layout; primary navigation consolidated to 3 core tabs (`Cinema`, `Dashboard`, `Settings`); keyboard shortcut integration. |
| **R9** | Sniffer Persistence & Toast De-duplication | `test_e2e_requirements_r1_r9.py` | Backend `aclear_feed()` preserves `_processed_messages` deduplication set while emptying recent feed array; `localStorage.getItem('tg_sniffer_cleared_at')` persistence; AppContext sliding-window 2,500ms toast cooldown, repeat count aggregation `(Nx)`, and maximum 2 visible toasts. |

---

## 3. Thresholds and System Tolerances

| Parameter | Authoritative Value | Test Verification |
|---|---|---|
| **Resume History Auto-Purge** | Exact `98.0%` boundary | Progress `< 98.0%` retained; progress `>= 98.0%` permanently deleted from `playback_history`. |
| **Dynamic Progress Color Tiers** | `< 30%`, `30% - 70%`, `70% - 98%` | `< 30%` -> `bg-sky-400` / `bg-sky-500`<br>`30% - 70%` -> `bg-amber-400` / `bg-amber-500`<br>`70% - 98%` -> `bg-emerald-400` / `bg-emerald-500` |
| **Toast Deduplication Window** | `2,500 ms` | Identical toast messages within 2.5s increment counter `(Nx)` instead of stacking. |
| **Maximum Visible Toasts** | `2 cards` | Excess toasts are trimmed from queue to prevent screen flooding. |
| **Upload Notification Throttle** | `0.5 s` rate-limit | Chunk progress updates throttled to prevent UI frame drops and WebSocket saturation. |
| **Proportional Sidebar Widths** | `18%` (min `190px`, max `260px`) | Prevents content squishing on 1024px-1920px viewports without horizontal clipping. |
| **Playback Token Lifetime** | Configurable (default unconstrained) | HMAC-SHA256 signature verification matching chat ID and message ID. |

---

## 4. Test Suite Execution Directory Layout

```
backend/tests/
├── test_e2e_requirements_r1_r9.py   # Comprehensive Requirement-Driven E2E Suite (60 tests, Tiers 1-4)
├── test_vlc_resume_purge.py          # Deep VLC Playback, Grants & 98% Resume Purge Suite (8 tests)
├── test_thumbnail_multistage.py      # Deep Multi-Stage Thumbnail Pipeline Suite (7 tests)
├── test_media_categorization.py      # Deep Movie/Series Categorization & Bundling Suite (9 tests)
└── ... (existing regression test suites)
```

## 5. Execution Commands

```bash
# Execute full comprehensive requirement-driven test suite:
/home/nandha/Desktop/TG_TOOL/venv/bin/pytest backend/tests/test_e2e_requirements_r1_r9.py -v

# Execute focused companion suites:
/home/nandha/Desktop/TG_TOOL/venv/bin/pytest backend/tests/test_vlc_resume_purge.py -v
/home/nandha/Desktop/TG_TOOL/venv/bin/pytest backend/tests/test_thumbnail_multistage.py -v
/home/nandha/Desktop/TG_TOOL/venv/bin/pytest backend/tests/test_media_categorization.py -v

# Execute all 4 new test suites simultaneously:
/home/nandha/Desktop/TG_TOOL/venv/bin/pytest backend/tests/test_e2e_requirements_r1_r9.py backend/tests/test_vlc_resume_purge.py backend/tests/test_thumbnail_multistage.py backend/tests/test_media_categorization.py -v
```
