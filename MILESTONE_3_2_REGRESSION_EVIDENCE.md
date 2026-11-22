# Milestone 3.2: Production Regression Evidence & Verification Report

**Branch:** `feature/multi-tenant-persistent-turbo`  
**Execution Timestamp:** 2026-11-22T18:28:22Z  
**Verification Tool:** Pytest 9.1.1 + Playwright 1.58.0 + JUnit XML Parser  
**Test Suite Status:** **496 / 496 PASSED (100% Clean Pass)**

---

## 1. Executive Summary & Inventory Reconciliation

### 1.1 Resolution of Test Count Discrepancy (491 vs 514)
An earlier manual table in `FULL_REGRESSION_RESULTS.md` reported row totals summing to 508–514 tests, while the automated summary reported 491 tests. 

An empirical trace using `pytest --collect-only` and AST parsing isolated the exact root cause:
1. **Omitted Test Files from Manual Table:** The manual table completely omitted three test files:
   - `test_challenger2_m1_adversarial.py` (5 tests)
   - `test_challenger2_m1_deep.py` (4 tests)
   - `test_challenger_m1_empirical.py` (5 tests)
   - *Total omitted:* 14 Playwright browser integration tests.
2. **Handwritten Overcounting:** Several rows in the manual table had estimated test counts (e.g. `test_e2e_requirements_r1_r9` estimated at 64 instead of the actual 60; `test_fast_seek` estimated at 40 instead of 38).
3. **Canonical Baseline Count:** The actual machine-collected test inventory prior to Milestone 3.2 was **491 tests across 59 test files** (477 backend unit/integration tests + 14 Playwright browser integration tests).
4. **Current Milestone 3.2 Inventory:** With the addition of `test_master_key_reconciliation.py` (5 tests for cryptographic alias reconciliation and PBKDF2 hashing verification), the definitive suite totals **496 tests across 60 test files**.

### 1.2 Summary Results

| Metric | Machine Count |
| :--- | :--- |
| **Total Collected Tests** | **496** |
| **Total Test Files** | **60** |
| **Passed Tests** | **496** (100.0%) |
| **Failed Tests** | **0** |
| **Errors** | **0** |
| **Skipped Tests** | **0** |
| **Deselected Tests** | **0** |
| **Suite Execution Time** | **187.64s (~3m 07s)** |
| **Machine-Readable Artifact** | `backend/tests/junit_results.xml` (62,428 bytes) |

---

## 2. Playwright Browser End-to-End Suite Verification

All 14 Playwright end-to-end integration tests were executed against the live local backend and production-built React frontend running on port 8088.

### 2.1 Remediations Applied
1. **ARIA Modal Accessibility Isolation:**
   - In `frontend-reui/src/views/cinema/DockedContinueWatching.tsx` and `DockedChannelPicker.tsx`, removed unconditional `role="dialog"` on desktop viewports. Made dialog role conditional on mobile viewports (`window.innerWidth < 1024`).
   - *Result:* Resolves `test_selection_checkbox_click_strictly_toggles_and_never_plays` and `test_checkbox_click_event_isolation_and_no_play_bubbling`.
2. **Responsive Header Layout Occlusion:**
   - In `frontend-reui/src/views/CinemaView.tsx`, split the header into a clean 2-row layout:
     - **Row 1:** Catalog branding, Channel selector pill, Sort dropdown, Search input, Sync button.
     - **Row 2:** Centered category tabs (`role="tablist"` with `sm:absolute sm:left-1/2 sm:-translate-x-1/2`) and right-aligned View Mode controls (`.ml-auto`).
   - *Result:* Eliminates horizontal collision at 768px, 900px, 1024px, and 1200px. Resolves `test_category_tab_occlusion_of_navigation_controls_under_edge_widths` and `test_category_tab_collision_with_sibling_controls_regression`.

### 2.2 Playwright Results (14 / 14 Passed)
- `test_dock_breakpoint_boundary_1023_vs_1024`: **PASSED**
- `test_dock_upper_bound_at_ultra_wide_viewports`: **PASSED**
- `test_element_hit_testing_checkbox_above_hover_overlay`: **PASSED**
- `test_movie_and_series_view_checkbox_event_isolation`: **PASSED**
- `test_category_tab_occlusion_of_navigation_controls_under_edge_widths`: **PASSED**
- `test_layout_dock_proportions_and_no_overflow_1024_to_1920`: **PASSED**
- `test_category_tab_centering_and_header_pill_symmetry`: **PASSED**
- `test_selection_checkbox_click_strictly_toggles_and_never_plays`: **PASSED**
- `test_absence_of_hd_across_frontend_source_and_dom`: **PASSED**
- `test_viewport_range_layout_clipping_and_overflow`: **PASSED**
- `test_category_tab_centering_edge_cases`: **PASSED**
- `test_category_tab_collision_with_sibling_controls_regression`: **PASSED**
- `test_checkbox_click_event_isolation_and_no_play_bubbling`: **PASSED**
- `test_absence_of_hd_in_types_filters_and_rendering`: **PASSED**

---

## 3. Production React Build Verification

The React 19 + TypeScript + Vite 8 frontend was built independently in production mode:
- Command: `npm --prefix frontend-reui run build`
- Output: `✓ built in 1.75s` (Exit code: 0)
- Bundle sizes:
  - `dist/index.html`: 1.12 kB
  - `dist/assets/index-02gz1TaW.css`: 158.44 kB (gzip: 23.72 kB)
  - `dist/assets/CinemaView-D3Ca4Xbt.js`: 138.08 kB (gzip: 29.67 kB)
  - `dist/assets/ManagementDashboardView-JeWd_nci.js`: 222.60 kB (gzip: 58.75 kB)
  - `dist/assets/react-vendor-CqoTxOzJ.js`: 210.92 kB (gzip: 65.85 kB)
- Zero build errors or unresolvable imports.

---

## 4. Machine-Verified Test Suite Breakdown (From `junit_results.xml`)

The table below is generated directly from `backend/tests/junit_results.xml`:

| Test Module / Suite | Total | Passed | Failed | Skipped | Errors |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `backend.tests.test_adversarial_m1` | 6 | 6 | 0 | 0 | 0 |
| `backend.tests.test_adversarial_m2` | 8 | 8 | 0 | 0 | 0 |
| `backend.tests.test_all` | 14 | 14 | 0 | 0 | 0 |
| `backend.tests.test_audit_remediation` | 7 | 7 | 0 | 0 | 0 |
| `backend.tests.test_audit_security` | 7 | 7 | 0 | 0 | 0 |
| `backend.tests.test_challenger2_m1_adversarial` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_challenger2_m1_deep` | 4 | 4 | 0 | 0 | 0 |
| `backend.tests.test_challenger_2_m1_it2` | 11 | 11 | 0 | 0 | 0 |
| `backend.tests.test_challenger_m1_empirical` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_challenger_m1_stress` | 9 | 9 | 0 | 0 | 0 |
| `backend.tests.test_challenger_m2_boundaries` | 20 | 20 | 0 | 0 | 0 |
| `backend.tests.test_challenger_m2_empirical` | 15 | 15 | 0 | 0 | 0 |
| `backend.tests.test_chats_mobile` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_chunk_based_resume` | 2 | 2 | 0 | 0 | 0 |
| `backend.tests.test_client_tier_and_sockets` | 8 | 8 | 0 | 0 | 0 |
| `backend.tests.test_companion_tools_m3` | 11 | 11 | 0 | 0 | 0 |
| `backend.tests.test_deep_codebase_design` | 3 | 3 | 0 | 0 | 0 |
| `backend.tests.test_e2e_requirements_r1_r9` | 60 | 60 | 0 | 0 | 0 |
| `backend.tests.test_e2e_streaming_integration` | 1 | 1 | 0 | 0 | 0 |
| `backend.tests.test_fast_seek` | 38 | 38 | 0 | 0 | 0 |
| `backend.tests.test_fdm_multi_account` | 3 | 3 | 0 | 0 | 0 |
| `backend.tests.test_generator_teardown_all_states` | 2 | 2 | 0 | 0 | 0 |
| `backend.tests.test_inlined_sniffer_design` | 7 | 7 | 0 | 0 | 0 |
| `backend.tests.test_lifecycle_enhancements` | 7 | 7 | 0 | 0 | 0 |
| `backend.tests.test_lifecycle_stress` | 16 | 16 | 0 | 0 | 0 |
| `backend.tests.test_master_key_reconciliation` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_media_categorization` | 9 | 9 | 0 | 0 | 0 |
| `backend.tests.test_milestone_1_persistence` | 13 | 13 | 0 | 0 | 0 |
| `backend.tests.test_milestone_2_suite` | 20 | 20 | 0 | 0 | 0 |
| `backend.tests.test_milestone_3_suite` | 12 | 12 | 0 | 0 | 0 |
| `backend.tests.test_mobile_streaming` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_multi_account_pool` | 4 | 4 | 0 | 0 | 0 |
| `backend.tests.test_network_monitor` | 3 | 3 | 0 | 0 | 0 |
| `backend.tests.test_network_resilience` | 3 | 3 | 0 | 0 | 0 |
| `backend.tests.test_parallel_mtproto_engine` | 10 | 10 | 0 | 0 | 0 |
| `backend.tests.test_phase1_vlc_probe_coexistence` | 1 | 1 | 0 | 0 | 0 |
| `backend.tests.test_phase2_producer_lifecycle` | 1 | 1 | 0 | 0 | 0 |
| `backend.tests.test_phase4_rust_session_and_chunks` | 2 | 2 | 0 | 0 | 0 |
| `backend.tests.test_phase6_loop_rebinding` | 1 | 1 | 0 | 0 | 0 |
| `backend.tests.test_postgres_rls` | 2 | 2 | 0 | 0 | 0 |
| `backend.tests.test_production_verification` | 9 | 9 | 0 | 0 | 0 |
| `backend.tests.test_qr_login` | 7 | 7 | 0 | 0 | 0 |
| `backend.tests.test_range_and_media_gzip` | 12 | 12 | 0 | 0 | 0 |
| `backend.tests.test_resuming_architecture` | 3 | 3 | 0 | 0 | 0 |
| `backend.tests.test_rust_bridge` | 8 | 8 | 0 | 0 | 0 |
| `backend.tests.test_seek_episode_aware` | 4 | 4 | 0 | 0 | 0 |
| `backend.tests.test_series_draft_engine` | 8 | 8 | 0 | 0 | 0 |
| `backend.tests.test_session_security_rigorous` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_stream_buffer` | 11 | 11 | 0 | 0 | 0 |
| `backend.tests.test_stream_cache` | 9 | 9 | 0 | 0 | 0 |
| `backend.tests.test_stream_cache_isolation` | 3 | 3 | 0 | 0 | 0 |
| `backend.tests.test_stream_engine_unified` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_stream_engine_verification` | 1 | 1 | 0 | 0 | 0 |
| `backend.tests.test_streaming_relay_and_hybrid` | 6 | 6 | 0 | 0 | 0 |
| `backend.tests.test_subtitle_service` | 5 | 5 | 0 | 0 | 0 |
| `backend.tests.test_tenant_authorization_audit` | 4 | 4 | 0 | 0 | 0 |
| `backend.tests.test_thumbnail_multistage` | 9 | 9 | 0 | 0 | 0 |
| `backend.tests.test_turbo_qualification` | 4 | 4 | 0 | 0 | 0 |
| `backend.tests.test_vlc_playback_grants` | 10 | 10 | 0 | 0 | 0 |
| `backend.tests.test_vlc_resume_purge` | 8 | 8 | 0 | 0 | 0 |
| **TOTAL** | **496** | **496** | **0** | **0** | **0** |
