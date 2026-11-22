# TG Power Suite — UI Screen & Component Inventory

> **Document:** Comprehensive Frontend Screen & Component Architecture Inventory  
> **Audited Path:** `frontend-reui/src/`  
> **Frontend Stack:** React 19 · Vite · Tailwind CSS v4 · ReUI / Radix UI Primitives · Lucide React  
> **Backend Integration:** FastAPI REST (`:8088`) + Rust Axum Native Engine (`:8089`)  

---

## 1. Master Screen & Viewport Matrix

| Screen / View / Modal | Component Path | State / Route Trigger | Backend API Dependencies | UX Score (1-10) | a11y Score (1-10) | Screenshot Reference |
|---|---|---|---|:---:|:---:|---|
| **Cinema View** | `src/views/CinemaView.tsx` | `view === 'cinema'` (Default) | `GET /api/media/all`<br>`POST /api/channels/scan`<br>`GET /stream/{id}` | 9.0 | 7.5 | `01_cinema_desktop_1440x900.png`<br>`02_cinema_large_desktop_1920x1080.png` |
| **Docked Channel Picker** | `src/components/DockedChannelPicker.tsx` | Left side dock (`isLeftDockOpen`) | `GET /api/channels/dialogs`<br>`POST /api/channels/scan` | 8.5 | 6.5 | `16_channel_drawer_overlay.png` |
| **Docked Continue Watching** | `src/components/DockedContinueWatching.tsx` | Right side dock (`isRightDockOpen`) | LocalStorage / `GET /api/playback/history` | 8.0 | 6.0 | `01_cinema_desktop_1440x900.png` |
| **Video Player Modal** | `src/components/VideoPlayerModal.tsx` | Card Click / Play Button | `GET /stream/{id}`<br>`POST /api/playback/progress` | 8.5 | 7.0 | `09_video_player_modal.png`<br>`15_video_player_playback_active.png` |
| **Management Dashboard** | `src/views/ManagementDashboardView.tsx` | `view === 'dashboard'` | Aggregated Sub-Deck APIs | 8.5 | 8.0 | `04_management_dashboard_all.png` |
| ↳ **Uploader Deck** | `src/components/UploaderDeck.tsx` | Dashboard Sub-Tab: "Uploader" | `POST /api/upload/chunk`<br>`GET /api/upload/status` | 8.5 | 8.0 | `05_uploader_deck.png` |
| ↳ **Sniffer Deck** | `src/components/SnifferDeck.tsx` | Dashboard Sub-Tab: "Sniffer" | `POST /api/sniffer/start`<br>`GET /api/sniffer/logs` | 8.0 | 7.5 | `06_sniffer_deck.png` |
| ↳ **History Deck** | `src/components/HistoryDeck.tsx` | Dashboard Sub-Tab: "History" | `GET /api/history/events`<br>`DELETE /api/history/clear` | 8.5 | 8.0 | `07_history_deck.png` |
| **Settings View** | `src/views/SettingsView.tsx` | `view === 'settings'` | `GET /api/settings`<br>`POST /api/settings/save` | 9.0 | 8.5 | `03_settings_view.png` |
| **Telegram Auth Modal** | `src/components/AuthModal.tsx` | Header Account Badge Click | `POST /api/auth/send_code`<br>`POST /api/auth/login`<br>`POST /api/auth/logout` | 7.5 | 7.5 | `10_auth_modal.png` |
| **Secondary Auth Modal** | `src/components/SecondaryAuthModal.tsx` | Settings > Bot / Secondary Account | `POST /api/auth/secondary_login`<br>`GET /api/auth/secondary_status` | 8.0 | 7.5 | `20_secondary_auth_modal.png` |
| **Global Header & Navigation**| `src/components/Header.tsx` | Persistent Top Bar | `GET /api/auth/status`<br>`GET /api/health` | 8.5 | 7.0 | Top of all screens |

---

## 2. Detailed Screen Inventories

### 2.1. Cinema View (`CinemaView.tsx`)
- **File Location:** `frontend-reui/src/views/CinemaView.tsx` (~2,800 lines)
- **Primary Purpose:** Core media discovery, indexing, and streaming hub. Presents Telegram media archives organized by channel, format (All, Movies, Series), resolution, and size.
- **Key Sub-Components:**
  - `HeroFeaturedSection`: Prominent banner displaying the most recently watched or trending high-bitrate media.
  - `CatalogControlsBar`: Search input with debounce, category tabs (All, Movies, Series), layout switcher (Large Grid, Dense Grid, Compact Fit, Table View), and multi-criteria sorting (Date, Size, Duration, Title).
  - `MediaCard`: Rich media card with dynamic video thumbnail, duration badge, resolution indicator (`4K`, `1080p`, `720p`), channel avatar tag, and dual play actions (Internal Player vs VLC Loopback).
  - `EmptyState`: Clean empty search/filter graphic with one-click filter reset button.
- **Backend API Endpoints:**
  - `GET /api/media/all?channel_id={id}`: Fetches indexed video messages with mime-types, file sizes, and Telegram message IDs.
  - `POST /api/channels/scan`: Triggers incremental background MTProto crawl of the selected channel.
  - `GET /stream/{message_id}`: High-performance streaming proxy with HTTP 206 Partial Content Range support.
- **UX & Visual Assessment:**
  - *Strengths:* Exceptional responsiveness; 273+ video items render without scroll jitter; thumbnail fallbacks are elegant; instant client-side text filtering (<16ms).
  - *Weaknesses:* Filter pills and layout switchers measure only 19px–22px high, causing high touch error rates on mobile. Category text rendered at `9px` is difficult to read.
  - *Rating:* **9.0 / 10** (Visual), **7.5 / 10** (Accessibility).
- **Screenshot Evidence:** `01_cinema_desktop_1440x900.png`, `02_cinema_large_desktop_1920x1080.png`, `11_cinema_search_filtering.png`, `12_cinema_empty_search_state.png`.

---

### 2.2. Docked Channel Picker (`DockedChannelPicker.tsx`)
- **File Location:** `frontend-reui/src/components/DockedChannelPicker.tsx`
- **Primary Purpose:** Left navigation rail/drawer allowing users to switch between Telegram Saved Messages, joined channels, supergroups, private chats, and bots.
- **Key Sub-Components:**
  - `ChannelSearchInput`: Real-time filtering of dialogs by title and username.
  - `DialogFilterPills`: Quick-filter chips (`All`, `Channels`, `Chats`, `Groups`, `Bots`, `Saved`).
  - `ChannelListItem`: Avatar image with fallback initials, unread badge, verified badge, and last-activity timestamp.
  - `SyncButton`: On-demand channel refresh trigger.
- **Backend API Endpoints:**
  - `GET /api/channels/dialogs`: Returns user's dialog list with cached metadata.
  - `POST /api/channels/refresh`: Forces MTProto refresh of dialog entities.
- **UX & Visual Assessment:**
  - *Strengths:* Smooth slide-in/slide-out animations; sticky headers; active channel clearly highlighted in theme accent color.
  - *Weaknesses:* Filter pills measure only 19px high × 25px–57px wide (severely violates 24px/44px minimum). Avatar load failure leaves a blank gray box because `onError` merely sets `display: none` without activating the SVG fallback.
  - *Rating:* **8.5 / 10** (Visual), **6.5 / 10** (Accessibility).
- **Screenshot Evidence:** `16_channel_drawer_overlay.png`.

---

### 2.3. Docked Continue Watching (`DockedContinueWatching.tsx`)
- **File Location:** `frontend-reui/src/components/DockedContinueWatching.tsx`
- **Primary Purpose:** Right navigation rail showing media with incomplete playback progress, allowing instant 1-click resume at the exact second last watched.
- **Key Sub-Components:**
  - `PlaybackProgressCard`: Thumbnail preview, title, formatted time remaining (`mm:ss left`), and continuous progress bar (98% completion threshold).
  - `DismissButton`: Removes an item from the in-progress queue.
- **Backend API Endpoints:**
  - `GET /api/playback/history`: Reads playback resume positions.
  - `DELETE /api/playback/history/{id}`: Clears individual history entry.
- **UX & Visual Assessment:**
  - *Strengths:* Clear percentage progress bar; instant resumption without re-seeking; chronological ordering.
  - *Weaknesses:* The dismiss "X" button is styled with `opacity-0 group-hover:opacity-100`. On touch devices (smartphones and tablets), hover does not exist. Users cannot dismiss cards without accidentally tapping the card and triggering video playback.
  - *Rating:* **8.0 / 10** (Visual), **6.0 / 10** (Touch UX).
- **Screenshot Evidence:** `01_cinema_desktop_1440x900.png`.

---

### 2.4. Video Player Modal (`VideoPlayerModal.tsx`)
- **File Location:** `frontend-reui/src/components/VideoPlayerModal.tsx`
- **Primary Purpose:** Full-featured in-app HTML5 video player with on-screen controls, buffer visualization, speed selector, aspect ratio toggle, and keyboard shortcuts.
- **Key Sub-Components:**
  - `VideoViewport`: HTML5 `<video>` tag integrated with browser MSE engine and custom chunk streaming pipeline.
  - `OSDControlsBar`: Auto-hiding control bar with Play/Pause, Skip ±10s, Volume/Mute slider, Time display (`00:00 / 45:12`), Playback speed menu (0.5x to 2.0x), Picture-in-Picture (PiP), Fullscreen, and VLC External Launch.
  - `TimelineSeekBar`: Dual-layer progress bar indicating both buffered range and current playback position.
- **Backend API Endpoints:**
  - `GET /stream/{message_id}`: Chunked HTTP Range endpoint supporting byte seeks.
  - `POST /api/playback/progress`: Auto-syncs playback timestamp every 5 seconds.
- **UX & Visual Assessment:**
  - *Strengths:* Smooth hardware-accelerated playback; rapid seeking; elegant glassmorphism OSD; responsive keyboard shortcuts (Space to toggle, 'F' for fullscreen).
  - *Weaknesses:* The timeline scrubber is a custom `<div>` without standard ARIA slider attributes (`role="slider"`, `aria-valuenow`, `aria-valuemin`, `aria-valuemax`). Keyboard focus cannot step through the timeline using arrow keys without mouse interaction.
  - *Rating:* **8.5 / 10** (Visual), **7.0 / 10** (Accessibility).
- **Screenshot Evidence:** `09_video_player_modal.png`, `15_video_player_playback_active.png`.

---

### 2.5. Management Dashboard (`ManagementDashboardView.tsx`)
- **File Location:** `frontend-reui/src/views/ManagementDashboardView.tsx`
- **Primary Purpose:** Central operations console housing three distinct engineering workflows: Uploader Deck, Sniffer Deck, and History Deck.
- **Key Sub-Components:**
  - `DashboardTabBar`: Segmented switch between "All Decks" (grid view) and individual isolated decks.
  - `UploaderDeck`: Telegram file chunker and upload queue monitor.
  - `SnifferDeck`: Channel monitoring daemon and real-time event log streamer.
  - `HistoryDeck`: Chronological ledger of downloads, streams, and MTProto transactions.
- **Backend API Endpoints:**
  - Sub-deck REST APIs for chunk uploads, sniffer process control, and log retrieval.
- **UX & Visual Assessment:**
  - *Strengths:* Cohesive modular layout; consistent card containers; informative empty states; live SSE/WebSocket log stream.
  - *Weaknesses:* On medium tablet screens (768px–1024px), the 3-column deck grid cramps data tables, causing text truncation in log entries.
  - *Rating:* **8.5 / 10** (Visual), **8.0 / 10** (Functional).
- **Screenshot Evidence:** `04_management_dashboard_all.png`, `05_uploader_deck.png`, `06_sniffer_deck.png`, `07_history_deck.png`.

---

### 2.6. Settings View (`SettingsView.tsx`)
- **File Location:** `frontend-reui/src/views/SettingsView.tsx`
- **Primary Purpose:** Configuration center for streaming buffers, VLC execution paths, Telegram download concurrency, session management, and secondary bot authentication.
- **Key Sub-Components:**
  - `StreamingPreferencesCard`: Chunk size slider (512KB–8MB), buffer lookahead slider, and default player toggle (Internal vs VLC).
  - `TelegramSessionCard`: Active session metadata, authorized phone number, data center ID, and session migration status.
  - `SecondaryAccountCard`: Configuration and QR / Bot Token auth for secondary parallel streaming sessions.
- **Backend API Endpoints:**
  - `GET /api/settings`: Fetches active configuration values.
  - `POST /api/settings/save`: Validates and persists configuration to local store.
- **UX & Visual Assessment:**
  - *Strengths:* Excellent information grouping; clean sliders with real-time value callouts; clear separation of streaming vs authentication settings.
  - *Weaknesses:* Reset to defaults lacks confirmation; some technical descriptions (e.g., "MTProto Chunk Pipeline Prefetch") lack explanatory tooltips for casual users.
  - *Rating:* **9.0 / 10** (Visual), **8.5 / 10** (Usability).
- **Screenshot Evidence:** `03_settings_view.png`.

---

### 2.7. Telegram Auth Modal (`AuthModal.tsx`)
- **File Location:** `frontend-reui/src/components/AuthModal.tsx`
- **Primary Purpose:** Interactive modal for primary Telegram MTProto login (Phone number input, SMS/Telegram code verification, 2FA Cloud Password prompt) and session status inspection.
- **Key Sub-Components:**
  - `PhoneInputStep`: International phone number entry with automated formatting.
  - `CodeVerificationStep`: 5-digit verification code input with auto-submit.
  - `TwoFactorStep`: Cloud password input with password reveal toggle.
  - `SessionInfoStep`: Displays authenticated account name (`myself_nandha`), User ID, and Destructive "Log Out" action.
- **Backend API Endpoints:**
  - `POST /api/auth/send_code`
  - `POST /api/auth/login`
  - `POST /api/auth/logout`
- **UX & Visual Assessment:**
  - *Strengths:* Clean step transitions; clear status feedback; robust input masking for phone numbers.
  - *Weaknesses:* **CRITICAL USABILITY DEFECT (UI-001):** Once logged in, clicking the user badge opens the modal where the primary prominent action is `<Button variant="destructive">Log Out from Telegram</Button>`. Tapping this button immediately invalidates the Telegram session with zero confirmation prompt.
  - *Rating:* **7.5 / 10** (Visual), **5.0 / 10** (Usability Hazard).
- **Screenshot Evidence:** `10_auth_modal.png`.

---

### 2.8. Global Header & Navigation (`Header.tsx`)
- **File Location:** `frontend-reui/src/components/Header.tsx`
- **Primary Purpose:** Top-level brand identity, view switching, theme cycling, Telegram connectivity indicator, and user account trigger.
- **Key Sub-Components:**
  - `BrandLogo`: "TG Power Suite" icon and text, accompanied by a `<Badge>PRO</Badge>`.
  - `NavTabs`: Segmented navigation buttons for "Cinema", "Dashboard", and "Settings".
  - `ThemeCycleButton`: Icon button that cycles through the 4 available color palettes.
  - `AccountBadge`: Displays authenticated user first name with online status beacon.
  - `MobileNavBar`: Secondary navigation bar rendered conditionally on screens < 768px.
- **Backend API Endpoints:**
  - `GET /api/auth/status`: Heartbeat for Telegram connection state.
- **UX & Visual Assessment:**
  - *Strengths:* Crisp typography; smooth active tab transition; responsive sticky positioning.
  - *Weaknesses:* Commercial "PRO" badge feels out of place for open-source personal software (`UI-005`). Blind theme cycling lacks dropdown menu (`UI-006`). Below 768px, mobile nav renders directly below the header instead of docking at the bottom of the screen (`UI-009`).
  - *Rating:* **8.5 / 10** (Visual), **7.0 / 10** (Mobile Ergonomics).
- **Screenshot Evidence:** Appears at the top of all desktop and mobile screenshots.
