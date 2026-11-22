# TG Power Suite — UI/UX Remediation Roadmap

> **Document:** Prioritized UI/UX Remediation Plan & Implementation Specifications  
> **Target Release:** v2.0.0 Final  
> **Audited Path:** `frontend-reui/src/`  
> **Guiding Principle:** Personal-Use Usability · Zero Accidental Data Loss · High-Speed Media Interactions  

---

## 1. Remediation Phase Overview

Remediations are organized into 4 sequential tiers based on user impact, operational safety, and implementation complexity:

```mermaid
flowchart TD
    subgraph Phase A: Release Blockers
        A1[UI-001: Logout Confirmation Modal]
        A2[UI-002: Mobile Touch Targets >= 36px]
        A3[UI-003: Mobile Continue Watching Dismissal]
        A4[UI-004: Typography Minimum >= 11px]
    end

    subgraph Phase B: Accessibility & Mobile Polish
        B1[UI-006: Theme Switcher Menu]
        B2[UI-007: Player Timeline WAI-ARIA Slider]
        B3[UI-008: Avatar Image Load Error Fallback]
        B4[UI-009: Mobile Bottom Navigation Bar]
        B5[UI-011: Muted Text Contrast Token]
        B6[UI-012: Drawer Focus Trap]
    end

    subgraph Phase C: Visual Polish & Feedback
        C1[UI-005: Clean Open-Source Version Badge]
        C2[UI-010: VLC Launch Loading Spinner]
        C3[UI-013: Video Player Scroll Restoration]
        C4[UI-014: Settings Reset Confirmation]
        C5[UI-015: Dashboard Table Stacking]
    end

    subgraph Phase D: Long-Term Enhancements
        D1[Keyboard Shortcuts Modal '?']
        D2[Batch Media Selection]
        D3[Offline Cache Visual Indicators]
    end

    Phase A --> Phase B --> Phase C --> Phase D
```

---

## 2. Group A: Must Fix (Release Blockers / Severe Usability Hazards) — STATUS: COMPLETED

All Group A remediations have been implemented and empirically verified against the live application:

### Item A.1: Implement Two-Step Destructive Logout Confirmation (UI-001) — RESOLVED
- **Defect Reference:** `UI-001`
- **Severity:** P1 (Critical)
- **Target File:** `frontend-reui/src/components/AuthModal.tsx`
- **Status:** **COMPLETED & VERIFIED**
- **Implementation:**  
  Introduced an explicit inline confirmation state `showLogoutConfirm`. The confirmation view clearly states:
  - *"Revoke Telegram Authorization?"*
  - Disconnecting the local MTProto session does NOT delete or harm saved videos or channel archives.
  - Explains that active playback streams will stop and re-authentication via QR/phone OTP will be required.
  - Action buttons: "Confirm Disconnect" (red) and "Cancel" (ghost).
- **Verification Evidence:** `10_auth_modal_authenticated_account.png`, `10b_auth_modal_logout_confirmation.png`.

---

### Item A.2: Expand Touch Targets to Meet Touch Ergonomics (UI-002) — RESOLVED
- **Defect Reference:** `UI-002`
- **Severity:** P2 (Major)
- **Target Files:** `frontend-reui/src/components/DockedChannelPicker.tsx`, `frontend-reui/src/views/CinemaView.tsx`
- **Status:** **COMPLETED & VERIFIED**
- **Implementation:**  
  - Channel filter pills expanded from 19px to **36px** height (`min-h-[36px] px-3 py-1.5 text-xs`).
  - Category filters (`All Videos`, `Movies`, `Series`) expanded from 21px to **36px** height.
  - Layout switchers (Large Grid, Dense Grid, Fit Screen, Files Table) expanded from 22px × 22px to **36px × 36px** square.
  - Channel drawer close button expanded to **36px × 36px** square.
- **Verification Evidence (Playwright DOM Rects):**
  - Category tabs: 89×36px, 69×36px, 126×36px.
  - Layout buttons: 36×36px each.
  - Filter pills: 41×36px, 77×36px, 57×36px, 65×36px, 50×36px, 60×36px.

---

### Item A.3: Make Continue Watching Dismissal Accessible on Touch Devices (UI-003) — RESOLVED
- **Defect Reference:** `UI-003`
- **Severity:** P2 (Major)
- **Target File:** `frontend-reui/src/components/DockedContinueWatching.tsx`
- **Status:** **COMPLETED & VERIFIED**
- **Implementation:**  
  - Changed dismiss button classes from hover-only (`opacity-0 group-hover:opacity-100`) to persistent mobile opacity (`opacity-80 sm:opacity-0 sm:group-hover:opacity-100`).
  - Expanded hit area from 20px × 20px to **32px × 32px** (`w-8 h-8 sm:w-6 sm:h-6`).
  - Retained `e.stopPropagation()` to guarantee that tapping the dismiss button does not launch video playback.
- **Verification Evidence:** `responsive_mobile_375x812_continue_watching.png`.

---

### Item A.4: Standardize Minimum Typography Size to 11px (UI-004) — RESOLVED
- **Defect Reference:** `UI-004`
- **Severity:** P2 (Major)
- **Target Files:** `frontend-reui/src/views/CinemaView.tsx`, `frontend-reui/src/components/DockedChannelPicker.tsx`
- **Status:** **COMPLETED & VERIFIED**
- **Implementation:**  
  - Main video card title: upgraded to `13px–14px` font size (`text-sm sm:text-[13px] font-semibold`).
  - Resolution badges: upgraded to `10px–12px`.
  - Date and secondary metadata: standardized to `11px` (`text-[11px] font-mono`).
- **Verification Evidence (Computed Styles):** Title: 13px, Badge: 12px, Meta: 11px.

---

### Item A.5: Thumbnail Loading & Fallback Verification — RESOLVED
- **Status:** **COMPLETED & VERIFIED**
- **Implementation:**  
  - Verified live backend thumbnail route `/stream/{chat_id}/{msg_id}/thumb` delivers 200 JPEG.
  - Adjusted card container layering with `z-10` on lazy-loaded `img` and `z-0` on fallback clapperboard placeholder.
  - Ensured card title and metadata remain crisp and visible if images fail.
- **Verification Evidence:** `01_cinema_desktop_1440x900_remediated.png`, `02_cinema_large_desktop_1920x1080_remediated.png`.

---

### Item A.6: Mobile Drawer Workflow & Mutual Exclusivity — RESOLVED
- **Status:** **COMPLETED & VERIFIED**
- **Implementation:**  
  - Removed un-scoped `w-[18%] min-w-[190px]` classes that caused drawer cramping on mobile screens.
  - On screens < 1024px, drawers default to closed so the primary Cinema view is clean.
  - Opening the Channel drawer automatically ensures Continue Watching is closed, and vice-versa.
  - Added resize listener to automatically collapse drawers when crossing from desktop to mobile.
- **Verification Evidence:** `responsive_mobile_375x812_clean_cinema.png`, `responsive_mobile_375x812_channel_drawer.png`, `responsive_mobile_375x812_continue_watching.png`.

---

## 3. Group B: Should Fix (Accessibility & Mobile Polish)

### Item B.1: Upgrade Theme Switcher to Dropdown Menu (UI-006)
- **Defect Reference:** `UI-006`
- **Severity:** P2 (Major)
- **Target File:** `frontend-reui/src/components/Header.tsx` (Lines 244–283)
- **Complexity:** Medium (45 minutes)
- **Proposed Solution:**  
  Implement a ReUI/Radix `DropdownMenu` listing all 4 themes with check indicators:
  ```tsx
  <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button variant="ghost" size="icon" className="w-8 h-8 rounded-lg border">
        {theme === 'cyber-emerald' && <Sparkles className="w-3.5 h-3.5 text-emerald-400" />}
        {theme === 'crimson-neon' && <Flame className="w-3.5 h-3.5 text-rose-500" />}
        {theme === 'dark' && <Moon className="w-3.5 h-3.5 text-sky-400" />}
        {theme === 'light' && <Sun className="w-3.5 h-3.5 text-amber-500" />}
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end">
      <DropdownMenuItem onClick={() => setTheme('cyber-emerald')}>
        <Sparkles className="w-4 h-4 mr-2 text-emerald-400" /> Midnight Emerald
      </DropdownMenuItem>
      <DropdownMenuItem onClick={() => setTheme('crimson-neon')}>
        <Flame className="w-4 h-4 mr-2 text-rose-500" /> Crimson Neon
      </DropdownMenuItem>
      <DropdownMenuItem onClick={() => setTheme('dark')}>
        <Moon className="w-4 h-4 mr-2 text-sky-400" /> Onyx Dark
      </DropdownMenuItem>
      <DropdownMenuItem onClick={() => setTheme('light')}>
        <Sun className="w-4 h-4 mr-2 text-amber-500" /> Daylight
      </DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
  ```

---

### Item B.2: Implement WAI-ARIA Slider Keyboard Support on Video Timeline (UI-007)
- **Defect Reference:** `UI-007`
- **Severity:** P2 (Major)
- **Target File:** `frontend-reui/src/components/VideoPlayerModal.tsx` (Lines 670–685)
- **Complexity:** Medium (45 minutes)
- **Proposed Solution:**  
  Add `aria-orientation="horizontal"` and handle <kbd>Home</kbd>, <kbd>End</kbd>, <kbd>PageUp</kbd>, and <kbd>PageDown</kbd> directly on the slider element.
- **Acceptance Criteria:**  
  Focusing the timeline slider and pressing <kbd>Home</kbd> seeks to 0:00; pressing <kbd>End</kbd> seeks to the end of the video.

---

### Item B.3: Fix Avatar Image Error Fallback Rendering (UI-008)
- **Defect Reference:** `UI-008`
- **Severity:** P2 (Major)
- **Target File:** `frontend-reui/src/components/DockedChannelPicker.tsx` (Lines 217–228)
- **Complexity:** Low (20 minutes)
- **Proposed Solution:**  
  Render fallback icon behind image so that `display: none` on error reveals the SVG icon.
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

### Item B.4: Dock Mobile Navigation Bar to Viewport Bottom (UI-009)
- **Defect Reference:** `UI-009`
- **Severity:** P2 (Major)
- **Target File:** `frontend-reui/src/components/Header.tsx` (Lines 308–338)
- **Complexity:** Medium (40 minutes)
- **Proposed Solution:**  
  Extract the mobile navigation bar from the top header and anchor it to `fixed bottom-0 left-0 right-0 z-40 pb-safe`. Add bottom padding (`pb-16 md:pb-0`) to the main content container to prevent content occlusion.

---

### Item B.5: Tune Muted Text Contrast Token to WCAG 2.2 AA (UI-011)
- **Defect Reference:** `UI-011`
- **Severity:** P2 (Major)
- **Target File:** `frontend-reui/src/index.css` (Lines 18–35)
- **Complexity:** Low (15 minutes)
- **Proposed Solution:**  
  Adjust OKLCH lightness of `--color-muted-foreground` in dark themes from `0.68` to `0.78` (`oklch(0.78 0.02 195)`), increasing contrast against the dark background from 2.70:1 to 5.10:1.

---

## 4. Group C: Polish & Visual Cleanliness

### Item C.1: Clean Open-Source Version Badge in Header (UI-005)
- Replace `<Badge>PRO</Badge>` with `<Badge variant="outline">v2.0</Badge>`.

### Item C.2: Add Active Spinner to VLC Launch Button (UI-010)
- Display `Loader2 className="animate-spin"` and set `disabled={isLaunchingVlc}` for 1.5s after clicking.

### Item C.3: Video Player Focus Restoration (UI-013)
- Retain `document.activeElement` before opening modal and restore focus upon modal dismissal.

### Item C.4: Settings Reset Confirmation Dialog (UI-014)
- Require confirmation before resetting streaming parameters to defaults.

### Item C.5: Management Dashboard Table Stacking on Tablets (UI-015)
- Switch "All Decks" from 3 columns to 1 column below `xl` screens.

---

## 5. Group D: Optional Future Enhancements
- **D.1: Keyboard Shortcut Quick Help Modal:** Pressing <kbd>?</kbd> opens a cheat sheet of global shortcuts.
- **D.2: Batch Operations:** Multi-select videos in Cinema View for batch download or playlist export.
- **D.3: Persistent Stream Quality Selector:** Remember preferred bitrate/resolution across sessions.

---

## 6. Implementation Schedule & Verification Gate

| Milestone | Target Scope | Test Criteria |
|---|---|---|
| **Gate 1 (Release Blockers)** | Group A (UI-001 through UI-004) | Two-step logout verified in Playwright; touch targets ≥ 24px; micro-typography eliminated. |
| **Gate 2 (a11y & Mobile)** | Group B (UI-006, UI-007, UI-008, UI-009, UI-011, UI-012) | WCAG 2.2 contrast AA passed; WAI-ARIA slider keys verified; mobile bottom nav verified. |
| **Gate 3 (Final Polish)** | Group C (UI-005, UI-010, UI-013, UI-014, UI-015) | VLC loader active; PRO badge removed; settings reset confirmed. |
