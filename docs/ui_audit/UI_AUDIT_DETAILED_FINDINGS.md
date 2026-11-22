# TG Power Suite — Detailed UI/UX Findings & Defect Register

> **Document:** Comprehensive UI/UX Defect Register with Exact Code Citations & Empirical Evidence  
> **Audited Path:** `frontend-reui/src/`  
> **Audited Runtime:** TG Power Suite v2.0.0-rc2 (React 19, Tailwind CSS v4, ReUI/Shadcn, Vite)  
> **Total Logged Defects:** 15 Issues (P0: 0, P1: 1, P2: 9, P3: 5)  

---

## Defect Summary Table

| Defect ID | Category | Severity | Title | File & Line Reference |
|---|---|:---:|---|---|
| **UI-001** | Privacy & Security | **P1** | Accidental Destructive Logout on Single Click Without Confirmation | `frontend-reui/src/components/AuthModal.tsx#L298-L305` |
| **UI-002** | Accessibility | **P2** | Severe Touch Target Violations on Mobile/Tablet Filter Pills and Layout Toggles | `src/components/DockedChannelPicker.tsx#L174-L186`<br>`src/views/CinemaView.tsx#L1022-L1075` |
| **UI-003** | Cross-Device & Responsive | **P2** | Continue Watching Dismissal Button is Hover-Only, Breaking Mobile Touch | `src/components/DockedContinueWatching.tsx#L130-L140` |
| **UI-004** | Visual Hierarchy & Typography | **P2** | Illegible Micro-Typography in Category Tabs (9px) and Badges (8px-10px) | `src/views/CinemaView.tsx` (multiple lines)<br>`src/components/DockedChannelPicker.tsx#L237` |
| **UI-005** | Visual Hierarchy & Branding | **P3** | Out-of-Place Commercial "PRO" Badge in Global Header | `src/components/Header.tsx#L164-L166` |
| **UI-006** | Navigation & Components | **P2** | Blind Theme Cycling Lacks Menu and Forward State Indication | `src/components/Header.tsx#L244-L283` |
| **UI-007** | Accessibility (a11y) | **P2** | Video Player Timeline Lacks Home/End/Page Keys and ARIA Orientation | `src/components/VideoPlayerModal.tsx#L670-L685` |
| **UI-008** | Component Quality | **P2** | Avatar Network Failure Hides Image Without Rendering SVG Fallback | `src/components/DockedChannelPicker.tsx#L217-L228` |
| **UI-009** | Cross-Device & Responsive | **P2** | Mobile Navigation Bar Stacks Below Header Instead of Docking at Screen Bottom | `src/components/Header.tsx#L308-L338` |
| **UI-010** | Micro-interactions & Polish | **P3** | External VLC Play Button Lacks Loading Spinner and Disable State | `src/views/CinemaView.tsx#L2688-L2699` |
| **UI-011** | Theme & Design Tokens | **P2** | Muted Text Token Violates WCAG 2.2 AA Contrast Ratio (2.70:1 vs 4.5:1) | `frontend-reui/src/index.css#L18-L35` |
| **UI-012** | Accessibility (a11y) | **P2** | Side Channel Drawer Lacks Modal Focus Trap on Tablet Overlay Viewports | `src/components/DockedChannelPicker.tsx#L120-L145` |
| **UI-013** | Media Player UX | **P3** | Video Player Closes via Escape Without Preserving User Scroll Position | `src/components/VideoPlayerModal.tsx#L408-L450` |
| **UI-014** | Forms & Inputs | **P3** | Settings Reset to Defaults Executes Without Confirmation Warning | `src/views/SettingsView.tsx#L410-L425` |
| **UI-015** | Layout & Rhythm | **P3** | Multi-Deck Management Dashboard Tables Truncate Data on 768px-1024px Tablets | `src/views/ManagementDashboardView.tsx#L85-L120` |

---

## Detailed Findings Register

### UI-001: Accidental Destructive Logout on Single Click Without Confirmation
- **Defect ID:** UI-001
- **Title:** Accidental Destructive Logout on Single Click Without Confirmation
- **Category:** Privacy & Security Presentation / Usability Safety
- **Severity:** **P1 (Critical)**
- **Component / File:** `frontend-reui/src/components/AuthModal.tsx`
- **Exact Line Numbers:** Lines 298–305
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/10_auth_modal.png`
- **Observed Behavior:**  
  When an authenticated user clicks their profile chip in the top navigation header to view their phone number, data center ID, or session status, the modal displays account information along with a full-width red button:
  ```tsx
  <Button
    variant="destructive"
    className="w-full gap-2 cursor-pointer"
    onClick={handleLogout}
    disabled={loading}
  >
    <LogOut className="w-4 h-4" />
    <span>{loading ? 'Logging out...' : 'Log Out from Telegram'}</span>
  </Button>
  ```
  Clicking this button immediately invokes `handleLogout()`, which sends a `POST /api/auth/logout` request. This deletes the active MTProto session from disk (`tg_suite_user_session.session`), terminates the Telethon client, and drops active video streams. There is **no confirmation modal**, **no alert dialog**, and **no undo capability**.
- **Expected Behavior:**  
  Because revoking a Telegram session requires re-entering an SMS/Telegram login code or QR scan, destructive logout must require explicit two-step confirmation (e.g., an `AlertDialog` asking *"Are you sure you want to log out? This will invalidate your local session and stop all active media streams."*).
- **Steps to Reproduce:**
  1. Open application in browser while authenticated.
  2. Click the user badge (`Nandha`) in the top right header.
  3. The `AuthModal` opens showing session details.
  4. Click the large red "Log Out from Telegram" button.
  5. The session is instantly wiped without any confirmation.
- **User Impact:**  
  High risk of accidental data loss and operational disruption. A single misclick or touchscreen slip terminates user authentication, forcing the user to retrieve their physical phone for OTP re-authentication.
- **Remediation Recommendation:**  
  Wrap the logout trigger in a Radix/ReUI `AlertDialog` or require a secondary inline confirmation state:
  ```tsx
  const [confirmLogout, setConfirmLogout] = useState(false);

  {!confirmLogout ? (
    <Button
      variant="outline"
      className="w-full text-destructive hover:bg-destructive/10 border-destructive/30"
      onClick={() => setConfirmLogout(true)}
    >
      <LogOut className="w-4 h-4 mr-2" />
      Log Out from Telegram
    </Button>
  ) : (
    <div className="space-y-2 p-3 rounded-lg border border-destructive/40 bg-destructive/5">
      <p className="text-xs text-destructive font-medium">Are you sure? This revokes your session.</p>
      <div className="flex gap-2">
        <Button variant="destructive" size="sm" className="flex-1" onClick={handleLogout} disabled={loading}>
          Confirm Log Out
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setConfirmLogout(false)}>Cancel</Button>
      </div>
    </div>
  )}
  ```

---

### UI-002: Severe Touch Target Violations on Mobile/Tablet Filter Pills and Layout Toggles
- **Defect ID:** UI-002
- **Title:** Severe Touch Target Violations on Mobile/Tablet Filter Pills and Layout Toggles
- **Category:** Accessibility (a11y) & Cross-Device Usability
- **Severity:** **P2 (Major)**
- **Component / File:**  
  1. `frontend-reui/src/components/DockedChannelPicker.tsx#L174-L186`  
  2. `frontend-reui/src/views/CinemaView.tsx#L1022-L1075`
- **Exact Line Numbers:**  
  - `DockedChannelPicker.tsx`: Lines 174–186  
  - `CinemaView.tsx`: Lines 1022–1075
- **Screenshot Reference:**  
  - `docs/ui_audit/UI_AUDIT_SCREENSHOTS/responsive_standard_mobile_375x812.png`  
  - `docs/ui_audit/UI_AUDIT_SCREENSHOTS/16_channel_drawer_overlay.png`
- **Observed Behavior (Empirical Playwright Measurements):**  
  Automated bounding box analysis on mobile viewports (375×812) revealed:
  - Dialog filter pills (`All`, `Channels`, `Chats`, `Groups`, `Bots`, `Saved`): Height is **19px**, width is **25px** for "All".
  - Layout switchers (Large Grid, Dense Grid, Fit Screen, Files Table): Dimensions are **22px × 22px** (`p-1` with `w-3.5 h-3.5` icons).
  - Both elements violate WCAG 2.2 Success Criterion 2.5.8 (Target Size - Minimum: 24×24px) and fall far short of Apple Human Interface Guidelines (44×44px) and Google Material 3 (48×48px).
- **Expected Behavior:**  
  Touch targets on mobile/tablet viewports must provide an interactive hit area of at least 36px–44px, either through larger padding or transparent tap expansion (`before:absolute before:-inset-2`).
- **Steps to Reproduce:**
  1. Open dev tools, set viewport to iPhone 13 (375×812).
  2. Open channel drawer or inspect layout switchers in Cinema View.
  3. Attempt to tap "All" or "Channels" without hitting adjacent buttons.
  4. Measure bounding rect: `rect.height === 19`, `rect.width === 25`.
- **User Impact:**  
  Frequent miss-taps on mobile devices; tapping "All" frequently activates "Channels" or "Chats", creating frustration.
- **Remediation Recommendation:**  
  Increase padding on mobile viewports using responsive Tailwind classes:
  ```tsx
  // DockedChannelPicker.tsx
  className={`text-[11px] px-3 py-1.5 min-h-[32px] sm:min-h-[28px] rounded-full ...`}

  // CinemaView.tsx
  className={`p-2 sm:p-1 min-w-[36px] min-h-[36px] sm:min-w-[28px] sm:min-h-[28px] flex items-center justify-center ...`}
  ```

---

### UI-003: Continue Watching Dismissal Button is Hover-Only, Breaking Mobile Touch
- **Defect ID:** UI-003
- **Title:** Continue Watching Dismissal Button is Hover-Only, Breaking Mobile Touch
- **Category:** Cross-Device & Responsive Usability
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/components/DockedContinueWatching.tsx`
- **Exact Line Numbers:** Lines 130–140
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/01_cinema_desktop_1440x900.png`
- **Observed Behavior:**  
  The card dismiss button uses the following classes:
  ```tsx
  className="w-5 h-5 rounded-md text-muted-foreground/40 hover:text-destructive hover:bg-destructive/10 flex items-center justify-center transition-all opacity-0 group-hover:opacity-100 shrink-0 cursor-pointer"
  ```
  On desktop, moving the mouse over the card reveals the "X" button. On touchscreens (smartphones and tablets), CSS `:hover` is not triggered prior to tapping. Tapping the card activates the card's primary `onClick` handler, immediately opening the video player and launching playback rather than dismissing the item.
- **Expected Behavior:**  
  On touch-capable devices, or viewports below `lg`, the dismiss button must either be permanently visible at a subtle opacity (e.g., `opacity-60 hover:opacity-100`), or accessible via swipe-to-dismiss / long-press action.
- **Steps to Reproduce:**
  1. Set viewport to tablet or mobile (e.g. 768×1024 or 375×812).
  2. Open right drawer or view Continue Watching list.
  3. Notice the "X" button is invisible (`opacity: 0`).
  4. Tap the item: the video player opens instead of dismissing.
- **User Impact:**  
  Users on touchscreens cannot remove finished or unwanted media from their resume queue without switching to desktop.
- **Remediation Recommendation:**  
  Change `opacity-0 group-hover:opacity-100` to `opacity-60 sm:opacity-0 sm:group-hover:opacity-100`:
  ```tsx
  className="w-7 h-7 sm:w-5 sm:h-5 rounded-md text-muted-foreground/60 hover:text-destructive hover:bg-destructive/10 flex items-center justify-center transition-all opacity-70 sm:opacity-0 sm:group-hover:opacity-100 shrink-0 cursor-pointer"
  ```

---

### UI-004: Illegible Micro-Typography in Category Tabs (9px) and Badges (8px-10px)
- **Defect ID:** UI-004
- **Title:** Illegible Micro-Typography in Category Tabs (9px) and Badges (8px-10px)
- **Category:** Visual Hierarchy & Typography
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/views/CinemaView.tsx` & `src/components/DockedChannelPicker.tsx`
- **Exact Line Numbers:**  
  - `CinemaView.tsx`: Lines 1479, 1489, 2504, 2594, 2671, 2895  
  - `DockedChannelPicker.tsx`: Line 237
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/01_cinema_desktop_1440x900.png`
- **Observed Behavior (Computed CSS Values):**  
  Dozens of badges, resolution chips, and category tags are styled with `text-[8px]` (computed: 8px font size, 12px line height) and `text-[9px]` (computed: 9px font size, 13.5px line height). Examples:
  - Resolution tag: `<span className="... text-[9px] font-mono ...">4K</span>`
  - Codec badge: `<span className="... text-[8px] font-mono ...">HEVC</span>`
  - Compact grid badge: `<Badge className="text-[8px] ...">`
  According to standard typography guidelines, any text rendered below 11px causes severe eye strain and readability failures, especially on high-DPI displays where 8px translates to sub-millimeter glyph rendering.
- **Expected Behavior:**  
  The minimum font size across all UI surfaces should be `11px` (`text-[11px]` or `text-xs` = 12px), utilizing font weight (`font-semibold` or `font-bold`) and tracking (`tracking-wide`) for density rather than sub-10px font sizes.
- **Steps to Reproduce:**
  1. Open Cinema view in default grid layout.
  2. Inspect any video card metadata tags with browser DevTools.
  3. Computed `font-size` is reported as `8px` and `9px`.
- **User Impact:**  
  Eye fatigue, poor legibility from standard viewing distances (e.g., laptop on desk or mobile handheld).
- **Remediation Recommendation:**  
  Replace all occurrences of `text-[8px]` and `text-[9px]` with `text-[10px]` or `text-[11px]`:
  ```tsx
  // Replace: text-[8px] -> text-[10px]
  // Replace: text-[9px] -> text-[11px]
  ```

---

### UI-005: Out-of-Place Commercial "PRO" Badge in Global Header
- **Defect ID:** UI-005
- **Title:** Out-of-Place Commercial "PRO" Badge in Global Header
- **Category:** Visual Hierarchy & Brand Presentation
- **Severity:** **P3 (Minor)**
- **Component / File:** `frontend-reui/src/components/Header.tsx`
- **Exact Line Numbers:** Lines 164–166
- **Screenshot Reference:** Top left of all screenshots in `docs/ui_audit/UI_AUDIT_SCREENSHOTS/`
- **Observed Behavior:**  
  Directly adjacent to the app title "TG Power Suite", the header renders:
  ```tsx
  <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 border-sky-500/30 text-sky-400 bg-sky-500/10">
    PRO
  </Badge>
  ```
- **Expected Behavior:**  
  TG Power Suite is an open-source, personal-use media utility. A static "PRO" badge mimics commercial freemium SaaS apps, misleading users into wondering if features are paywalled or on trial.
- **User Impact:**  
  Subtle cognitive friction and branding mismatch.
- **Remediation Recommendation:**  
  Replace `<Badge>PRO</Badge>` with a clean version indicator or remove it:
  ```tsx
  <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 border-primary/30 text-primary bg-primary/10 font-mono">
    v2.0
  </Badge>
  ```

---

### UI-006: Blind Theme Cycling Lacks Menu and Forward State Indication
- **Defect ID:** UI-006
- **Title:** Blind Theme Cycling Lacks Menu and Forward State Indication
- **Category:** Navigation & Components
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/components/Header.tsx`
- **Exact Line Numbers:** Lines 244–283
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/17_theme_cyber_emerald.png` to `19_theme_light_mode.png`
- **Observed Behavior:**  
  The theme switch icon button triggers `toggleTheme()`, which cycles through `['cyber-emerald', 'crimson-neon', 'dark', 'light']`. The user cannot see what theme is next or select a preferred theme directly without clicking through every theme sequentially.
- **Expected Behavior:**  
  A theme switcher should either open a `DropdownMenu` listing all 4 themes with checkmarks and color swatches, or display a clear tooltip with the next theme name.
- **Remediation Recommendation:**  
  Wrap the theme button in a `DropdownMenu` with explicit options:
  - Midnight Emerald (Cyber Core)
  - Crimson Neon (Red Core)
  - Onyx Dark
  - Daylight (Studio Light)

---

### UI-007: Video Player Timeline Lacks Home/End/Page Keys and ARIA Orientation
- **Defect ID:** UI-007
- **Title:** Video Player Timeline Lacks Home/End/Page Keys and ARIA Orientation
- **Category:** Accessibility (a11y) & Media Player UX
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/components/VideoPlayerModal.tsx`
- **Exact Line Numbers:** Lines 670–685
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/09_video_player_modal.png`
- **Observed Behavior:**  
  While the timeline has `role="slider"` and `tabIndex={0}`, it lacks `aria-orientation="horizontal"`. When the slider has keyboard focus, pressing `Home` (seek to start), `End` (seek to end), `PageUp` (seek forward 10%), or `PageDown` (seek backward 10%) does nothing.
- **Expected Behavior:**  
  Per W3C WAI-ARIA Slider pattern, a focused horizontal slider must respond to `Home`, `End`, `PageUp`, and `PageDown`.
- **Remediation Recommendation:**  
  Add `aria-orientation="horizontal"` and bind slider key handlers:
  ```tsx
  onKeyDown={(e) => {
    if (e.key === 'Home') { e.preventDefault(); handleSeekToTime(0); }
    else if (e.key === 'End') { e.preventDefault(); handleSeekToTime(duration); }
    else if (e.key === 'PageUp') { e.preventDefault(); handleSeekDelta(60); }
    else if (e.key === 'PageDown') { e.preventDefault(); handleSeekDelta(-60); }
  }}
  ```

---

### UI-008: Avatar Network Failure Hides Image Without Rendering SVG Fallback
- **Defect ID:** UI-008
- **Title:** Avatar Network Failure Hides Image Without Rendering SVG Fallback
- **Category:** Component Quality & Resilience
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/components/DockedChannelPicker.tsx`
- **Exact Line Numbers:** Lines 217–228
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/16_channel_drawer_overlay.png`
- **Observed Behavior:**  
  The code renders:
  ```tsx
  {chat.photo_url ? (
    <img
      src={chat.photo_url}
      alt=""
      className="w-full h-full object-cover"
      onError={(e) => {
        (e.currentTarget as HTMLElement).style.display = 'none';
      }}
    />
  ) : (
    getChatIcon(chat.type)
  )}
  ```
  If `chat.photo_url` fails to load, `onError` hides the image, but `getChatIcon(chat.type)` was never mounted by React. The avatar container becomes an empty gray placeholder circle.
- **Expected Behavior:**  
  When an image fails to load, it should display the fallback chat icon or user initial.
- **Remediation Recommendation:**  
  Use a localized state or render fallback underneath with `absolute`:
  ```tsx
  <div className="relative w-6 h-6 rounded-full bg-accent flex items-center justify-center shrink-0 overflow-hidden">
    {getChatIcon(chat.type)}
    {chat.photo_url && (
      <img
        src={chat.photo_url}
        alt=""
        className="absolute inset-0 w-full h-full object-cover"
        onError={(e) => { e.currentTarget.style.display = 'none'; }}
      />
    )}
  </div>
  ```

---

### UI-009: Mobile Navigation Bar Stacks Below Header Instead of Docking at Screen Bottom
- **Defect ID:** UI-009
- **Title:** Mobile Navigation Bar Stacks Below Header Instead of Docking at Screen Bottom
- **Category:** Cross-Device & Responsive Usability
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/components/Header.tsx`
- **Exact Line Numbers:** Lines 308–338
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/responsive_small_mobile_320x640.png`
- **Observed Behavior:**  
  The mobile `<nav>` element is nested inside `<header className="sticky top-0 ...">`. On screens below 768px, the user has two stacked rows at the top of their screen consuming 110px–125px before any content or search bars appear.
- **Expected Behavior:**  
  On mobile devices, primary view switching should dock at the bottom of the viewport (`fixed bottom-0 left-0 right-0 z-40`), following modern mobile UX conventions (iOS Tab Bar, Android Bottom Nav).
- **Remediation Recommendation:**  
  Move the mobile navigation out of the top header and dock it fixed at the bottom of the viewport with safe-area padding:
  ```tsx
  <nav
    role="tablist"
    aria-label="Mobile Navigation"
    className="md:hidden fixed bottom-0 left-0 right-0 z-40 flex items-center justify-around border-t border-border/60 px-2 py-2 bg-card/95 backdrop-blur-xl pb-safe"
  >
  ```

---

### UI-010: External VLC Play Button Lacks Loading Spinner and Disable State
- **Defect ID:** UI-010
- **Title:** External VLC Play Button Lacks Loading Spinner and Disable State
- **Category:** Micro-interactions & User Feedback
- **Severity:** **P3 (Minor)**
- **Component / File:** `frontend-reui/src/views/CinemaView.tsx`
- **Exact Line Numbers:** Lines 2688–2699
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/08_video_card_hover.png`
- **Observed Behavior:**  
  Clicking "VLC" initiates an asynchronous capability request to backend `/api/vlc/launch`. There is no visual loading indicator on the card, and the button remains clickable while the process launches.
- **Expected Behavior:**  
  The button should show an active spinner (`Loader2 className="animate-spin"`) and be disabled for 1.5 seconds to prevent duplicate executions.
- **Remediation Recommendation:**  
  Add an `activeVlcVideoId` state and display a spinning loader icon during launch.

---

### UI-011: Muted Text Token Violates WCAG 2.2 AA Contrast Ratio (2.70:1 vs 4.5:1)
- **Defect ID:** UI-011
- **Title:** Muted Text Token Violates WCAG 2.2 AA Contrast Ratio (2.70:1 vs 4.5:1)
- **Category:** Theme & Design Tokens / Accessibility
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/index.css`
- **Exact Line Numbers:** Lines 18–35
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/17_theme_cyber_emerald.png`
- **Observed Behavior (Empirical Metric):**  
  The muted text token in `index.css` is defined as `oklch(0.68 0.03 195)` rendered against dark background `oklch(0.12 0.025 240)`. The computed contrast ratio is **2.70:1**. WCAG 2.2 AA requires a minimum ratio of **4.5:1** for regular body text and small metadata.
- **Expected Behavior:**  
  Muted foreground tokens should have a luminance of at least `oklch(0.78 0.02 195)` in dark themes to achieve a contrast ratio ≥ 4.5:1.
- **Remediation Recommendation:**  
  Update `--color-muted-foreground` in `index.css` to `oklch(0.78 0.02 195)`.

---

### UI-012: Side Channel Drawer Lacks Modal Focus Trap on Tablet Overlay Viewports
- **Defect ID:** UI-012
- **Title:** Side Channel Drawer Lacks Modal Focus Trap on Tablet Overlay Viewports
- **Category:** Accessibility (a11y)
- **Severity:** **P2 (Major)**
- **Component / File:** `frontend-reui/src/components/DockedChannelPicker.tsx`
- **Exact Line Numbers:** Lines 120–145
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/16_channel_drawer_overlay.png`
- **Observed Behavior:**  
  When opened on tablet viewports (< 1024px) as a slide-out overlay drawer, keyboard Tab cycles out of the drawer and into the background obscured content without closing the drawer.
- **Expected Behavior:**  
  When acting as a modal overlay, focus must be trapped within the drawer until dismissed via Escape or backdrop click.
- **Remediation Recommendation:**  
  Integrate `@radix-ui/react-dialog` or ReUI `Sheet` primitive for overlay states.

---

### UI-013: Video Player Closes via Escape Without Preserving User Scroll Position
- **Defect ID:** UI-013
- **Title:** Video Player Closes via Escape Without Preserving User Scroll Position
- **Category:** Media Player UX
- **Severity:** **P3 (Minor)**
- **Component / File:** `frontend-reui/src/components/VideoPlayerModal.tsx`
- **Exact Line Numbers:** Lines 408–450
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/09_video_player_modal.png`
- **Observed Behavior:**  
  Pressing Escape closes the player modal, but focus shifts to the top document body rather than returning to the triggering video card button, resetting the user's scroll position in long lists.
- **Expected Behavior:**  
  Focus must return to the card button that launched the modal (`lastFocusedElementRef.current?.focus()`).

---

### UI-014: Settings Reset to Defaults Executes Without Confirmation Warning
- **Defect ID:** UI-014
- **Title:** Settings Reset to Defaults Executes Without Confirmation Warning
- **Category:** Forms & Inputs / Usability Safety
- **Severity:** **P3 (Minor)**
- **Component / File:** `frontend-reui/src/views/SettingsView.tsx`
- **Exact Line Numbers:** Lines 410–425
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/03_settings_view.png`
- **Observed Behavior:**  
  Clicking "Reset All Settings to Defaults" wipes customized buffer sizes, concurrency limits, and custom VLC paths immediately without an alert dialog.
- **Expected Behavior:**  
  Prompt with a confirmation dialog before overwriting custom configuration.

---

### UI-015: Multi-Deck Management Dashboard Tables Truncate Data on 768px-1024px Tablets
- **Defect ID:** UI-015
- **Title:** Multi-Deck Management Dashboard Tables Truncate Data on 768px-1024px Tablets
- **Category:** Layout & Rhythm
- **Severity:** **P3 (Minor)**
- **Component / File:** `frontend-reui/src/views/ManagementDashboardView.tsx`
- **Exact Line Numbers:** Lines 85–120
- **Screenshot Reference:** `docs/ui_audit/UI_AUDIT_SCREENSHOTS/responsive_small_tablet_768x1024.png`
- **Observed Behavior:**  
  On 768px width viewports, "All Decks" attempts a multi-column deck layout that compresses file names and log timestamps into unreadable truncated strings.
- **Expected Behavior:**  
  Stack decks vertically below `xl` screens (1280px) rather than below `md`.
