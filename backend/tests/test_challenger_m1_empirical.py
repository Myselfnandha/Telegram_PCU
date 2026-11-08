import os
import sys
import time
import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend-reui"

BASE_URL = "http://localhost:8088"

@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        yield context
        browser.close()

def test_viewport_range_layout_clipping_and_overflow(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector("aside.slide-in-from-left-3", timeout=10000)

    viewports = [1024, 1080, 1152, 1200, 1280, 1366, 1440, 1536, 1600, 1680, 1728, 1800, 1920]

    for width in viewports:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(200)

        metrics = page.evaluate("""() => {
            const docWidth = document.documentElement.scrollWidth;
            const winWidth = window.innerWidth;
            const bodyWidth = document.body.scrollWidth;

            const leftAside = document.querySelector('aside.slide-in-from-left-3');
            const rightAside = document.querySelector('aside.slide-in-from-right-3');
            const centerCatalog = leftAside ? leftAside.nextElementSibling : null;

            const leftRect = leftAside ? leftAside.getBoundingClientRect() : null;
            const rightRect = rightAside ? rightAside.getBoundingClientRect() : null;
            const centerRect = centerCatalog ? centerCatalog.getBoundingClientRect() : null;

            return {
                width: winWidth,
                docWidth,
                bodyWidth,
                hasDocOverflow: docWidth > winWidth + 1,
                hasBodyOverflow: bodyWidth > winWidth + 1,
                leftRect,
                rightRect,
                centerRect
            };
        }""")

        assert not metrics["hasDocOverflow"], f"Horizontal doc overflow at {width}px: scrollWidth={metrics['docWidth']} > innerWidth={metrics['width']}"
        assert not metrics["hasBodyOverflow"], f"Horizontal body overflow at {width}px: bodyWidth={metrics['bodyWidth']} > innerWidth={metrics['width']}"

        assert metrics["leftRect"] is not None, f"Left dock missing at {width}px"
        assert metrics["rightRect"] is not None, f"Right dock missing at {width}px"
        assert metrics["centerRect"] is not None, f"Center catalog missing at {width}px"

        left_w = metrics["leftRect"]["width"]
        right_w = metrics["rightRect"]["width"]
        center_w = metrics["centerRect"]["width"]

        assert 185 <= left_w <= 265, f"Left dock width out of bounds at {width}px: {left_w}"
        assert 185 <= right_w <= 265, f"Right dock width out of bounds at {width}px: {right_w}"
        assert center_w > 400, f"Center catalog crushed or too small at {width}px: {center_w}"

        assert metrics["leftRect"]["left"] >= 0, f"Left dock clipped at {width}px: left={metrics['leftRect']['left']}"
        assert metrics["rightRect"]["right"] <= width + 1, f"Right dock clipped at {width}px: right={metrics['rightRect']['right']}"

        assert metrics["leftRect"]["right"] <= metrics["centerRect"]["left"] + 2, f"Left dock overlaps center content at {width}px"
        assert metrics["centerRect"]["right"] <= metrics["rightRect"]["left"] + 2, f"Center content overlaps right dock at {width}px"

    page.close()

def test_category_tab_centering_edge_cases(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector('[role="tablist"][aria-label="Video category filters"]', timeout=10000)

    edge_widths = [768, 900, 1024, 1200]

    for width in edge_widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(200)

        centering = page.evaluate("""() => {
            const tablist = document.querySelector('[role="tablist"][aria-label="Video category filters"]');
            if (!tablist) return null;
            const tablistRect = tablist.getBoundingClientRect();
            const parent = tablist.closest('.relative.min-h-\\\\[38px\\\\]') || tablist.parentElement.parentElement;
            const parentRect = parent.getBoundingClientRect();
            const tablistCenter = tablistRect.left + tablistRect.width / 2;
            const parentCenter = parentRect.left + parentRect.width / 2;

            return {
                width: window.innerWidth,
                tablistRect: { left: tablistRect.left, right: tablistRect.right, width: tablistRect.width },
                tablistCenter,
                parentCenter,
                diff: Math.abs(tablistCenter - parentCenter)
            };
        }""")

        assert centering is not None, f"Tablist not found at {width}px"
        assert centering["tablistRect"]["left"] >= 0 and centering["tablistRect"]["right"] <= width + 50, f"Category tabs not within bounds at {width}px"

    page.close()

def test_category_tab_collision_with_sibling_controls_regression(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector('[role="tablist"][aria-label="Video category filters"]', timeout=10000)

    viewports = [1024, 1200]
    collisions = []

    for width in viewports:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(200)

        targets = [
            ("Switch to large grid layout", "[aria-label=\"Switch to large grid layout\"]"),
            ("Switch to dense grid layout", "[aria-label=\"Switch to dense grid layout\"]"),
            ("Switch to screen-fit compact grid", "[aria-label=\"Switch to screen-fit compact grid\"]"),
            ("Switch to files table layout", "[aria-label=\"Switch to files table layout\"]")
        ]

        for label, selector in targets:
            try:
                btn = page.locator(selector).first
                btn.scroll_into_view_if_needed()
                btn.click(timeout=2500)
            except Exception as e:
                collisions.append({
                    "width": width,
                    "target": label,
                    "error": str(e).split("\n")[0]
                })

        try:
            page.locator('[aria-label="Switch to dense grid layout"]').first.click()
        except Exception:
            pass

    page.close()
    assert len(collisions) == 0, f"Category tab overlay collided with and blocked controls: {collisions}"

def test_checkbox_click_event_isolation_and_no_play_bubbling(browser_context):
    page = browser_context.new_page()
    playback_intercepted = []

    def on_request(request):
        url = request.url
        if "/api/media/vlc/launch" in url or "/api/media/play" in url or ("/stream/" in url and not url.endswith("/thumb")):
            playback_intercepted.append(url)

    page.on("request", on_request)
    page.goto(BASE_URL)
    page.evaluate("() => localStorage.setItem('cinema_view_mode', 'dense')")
    page.reload()
    page.wait_for_selector(".group.relative.rounded-2xl", timeout=15000)

    card = page.locator(".group.relative.rounded-2xl").first
    card.scroll_into_view_if_needed()
    card.hover()
    page.wait_for_timeout(200)

    checkbox_btn = card.locator("button.backdrop-blur-md").first
    assert checkbox_btn.is_visible(), "Checkbox button is not visible on hover"

    initially_selected = "bg-primary" in (checkbox_btn.get_attribute("class") or "")

    checkbox_btn.click()
    page.wait_for_timeout(300)

    after_click_selected = "bg-primary" in (checkbox_btn.get_attribute("class") or "")
    assert after_click_selected != initially_selected, "Checkbox click failed to toggle selection state"
    assert len(playback_intercepted) == 0, f"Playback was unexpectedly triggered by checkbox click: {playback_intercepted}"

    modal_open = page.locator('[role="dialog"]').count() > 0
    assert not modal_open, "Modal video player was triggered by checkbox click"

    checkbox_btn.click()
    page.wait_for_timeout(300)

    reverted_selected = "bg-primary" in (checkbox_btn.get_attribute("class") or "")
    assert reverted_selected == initially_selected, "Checkbox second click failed to revert selection state"
    assert len(playback_intercepted) == 0, f"Playback was unexpectedly triggered on second click: {playback_intercepted}"

    page.close()

def test_absence_of_hd_in_types_filters_and_rendering(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector('[role="tablist"][aria-label="Video category filters"]', timeout=10000)

    rendered_tabs = page.evaluate("""() => {
        const tablist = document.querySelector('[role="tablist"][aria-label="Video category filters"]');
        if (!tablist) return [];
        return Array.from(tablist.querySelectorAll('button')).map(b => b.innerText.trim());
    }""")

    assert "1080p & 4K" not in rendered_tabs, f"'1080p & 4K' tab rendered in category tabs: {rendered_tabs}"
    for tab in rendered_tabs:
        assert "hd" not in tab.lower(), f"Unexpected HD tab found: {tab}"
    assert "All Videos" in rendered_tabs
    assert "Movies" in rendered_tabs
    assert "Series / Episodes" in rendered_tabs

    cinema_view_path = FRONTEND_DIR / "src" / "views" / "CinemaView.tsx"
    code = cinema_view_path.read_text(encoding="utf-8")

    assert "<'all' | 'movies' | 'series' | 'hd'>" not in code
    assert "filter === 'hd'" not in code
    assert "filter === \"hd\"" not in code
    assert "'1080p & 4K'" not in code
    assert '"1080p & 4K"' not in code

    types_path = FRONTEND_DIR / "src" / "types" / "index.ts"
    types_code = types_path.read_text(encoding="utf-8")
    assert "'hd'" not in types_code
    assert '"hd"' not in types_code

    page.close()
