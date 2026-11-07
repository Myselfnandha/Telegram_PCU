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

def test_dock_breakpoint_boundary_1023_vs_1024(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector("#cinema-files-catalog", timeout=15000)

    page.set_viewport_size({"width": 1023, "height": 900})
    page.wait_for_timeout(200)

    data_1023 = page.evaluate("""() => {
        const leftDock = document.querySelector('aside.slide-in-from-left-3');
        const docW = document.documentElement.scrollWidth;
        const winW = window.innerWidth;
        const pos = leftDock ? window.getComputedStyle(leftDock).position : null;
        return { docW, winW, pos, hasLeft: !!leftDock };
    }""")

    assert data_1023["docW"] <= data_1023["winW"] + 1
    assert data_1023["pos"] == "fixed"

    page.set_viewport_size({"width": 1024, "height": 900})
    page.wait_for_timeout(200)

    data_1024 = page.evaluate("""() => {
        const leftDock = document.querySelector('aside.slide-in-from-left-3');
        const rightDock = document.querySelector('aside.slide-in-from-right-3');
        const catalog = document.querySelector('#cinema-files-catalog');

        const docW = document.documentElement.scrollWidth;
        const winW = window.innerWidth;

        const leftPos = leftDock ? window.getComputedStyle(leftDock).position : null;
        const rightPos = rightDock ? window.getComputedStyle(rightDock).position : null;

        const lRect = leftDock ? leftDock.getBoundingClientRect() : null;
        const rRect = rightDock ? rightDock.getBoundingClientRect() : null;
        const cRect = catalog ? catalog.getBoundingClientRect() : null;

        return {
            docW,
            winW,
            leftPos,
            rightPos,
            lRect,
            rRect,
            cRect
        };
    }""")

    assert data_1024["docW"] <= data_1024["winW"] + 1
    assert data_1024["leftPos"] == "static"
    assert data_1024["rightPos"] == "static"
    assert 185 <= data_1024["lRect"]["width"] <= 265
    assert 185 <= data_1024["rRect"]["width"] <= 265
    assert data_1024["cRect"]["width"] >= 480
    assert data_1024["lRect"]["right"] <= data_1024["cRect"]["left"] + 2
    assert data_1024["cRect"]["right"] <= data_1024["rRect"]["left"] + 2

    page.close()

def test_dock_upper_bound_at_ultra_wide_viewports(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector("#cinema-files-catalog", timeout=15000)

    for width in [1920, 2560]:
        page.set_viewport_size({"width": width, "height": 1080})
        page.wait_for_timeout(200)

        widths = page.evaluate("""() => {
            const leftDock = document.querySelector('aside.slide-in-from-left-3');
            const rightDock = document.querySelector('aside.slide-in-from-right-3');
            const catalog = document.querySelector('#cinema-files-catalog');

            const lRect = leftDock ? leftDock.getBoundingClientRect() : null;
            const rRect = rightDock ? rightDock.getBoundingClientRect() : null;
            const cRect = catalog ? catalog.getBoundingClientRect() : null;

            return {
                leftWidth: lRect ? lRect.width : 0,
                rightWidth: rRect ? rRect.width : 0,
                catWidth: cRect ? cRect.width : 0,
                docWidth: document.documentElement.scrollWidth,
                winWidth: window.innerWidth
            };
        }""")

        assert widths["docWidth"] <= widths["winWidth"] + 1
        assert widths["leftWidth"] <= 265
        assert widths["rightWidth"] <= 265
        assert widths["catWidth"] > (width - 600)

    page.close()

def test_element_hit_testing_checkbox_above_hover_overlay(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector("#cinema-files-catalog", timeout=15000)

    cards = page.locator(".group.relative.rounded-2xl")
    assert cards.count() > 0

    first_card = cards.first
    first_card.scroll_into_view_if_needed()
    first_card.hover()
    page.wait_for_timeout(200)

    hit_test = page.evaluate("""() => {
        const card = document.querySelector('.group.relative.rounded-2xl');
        if (!card) return null;

        const checkboxBtn = card.querySelector('button.backdrop-blur-md');
        if (!checkboxBtn) return null;

        const rect = checkboxBtn.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;

        const topEl = document.elementFromPoint(centerX, centerY);
        const isSelfOrInside = topEl && (topEl === checkboxBtn || checkboxBtn.contains(topEl));

        const overlay = card.querySelector('.absolute.inset-0.bg-black\\\\/50') || card.querySelector('.group-hover\\\\:opacity-100');
        const overlayPointerEvents = overlay ? window.getComputedStyle(overlay).pointerEvents : null;
        const btnZ = window.getComputedStyle(checkboxBtn.parentElement).zIndex;

        return {
            isSelfOrInside,
            topTag: topEl ? topEl.tagName : null,
            topClass: topEl ? topEl.className : null,
            overlayPointerEvents,
            btnZ
        };
    }""")

    assert hit_test is not None
    assert hit_test["isSelfOrInside"], f"Hit test failed: element at checkbox center is {hit_test['topTag']}.{hit_test['topClass']}"
    assert hit_test["overlayPointerEvents"] == "none"
    assert int(hit_test["btnZ"]) >= 40

    page.close()

def test_movie_and_series_view_checkbox_event_isolation(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector('[role="tablist"][aria-label="Video category filters"]', timeout=15000)

    playback_intercepted = []
    def on_request(req):
        url = req.url
        if "/api/media/vlc/launch" in url or "/api/media/play" in url:
            playback_intercepted.append(url)
        elif "/stream/" in url and not url.endswith("/thumb") and "/thumb" not in url:
            playback_intercepted.append(url)

    page.on("request", on_request)

    movie_tab = page.locator('button[role="tab"]:has-text("Movies")')
    assert movie_tab.count() > 0
    movie_tab.click()
    page.wait_for_timeout(400)

    movie_checkboxes = page.locator('button[title="Select movie"]')
    if movie_checkboxes.count() > 0:
        box_btn = movie_checkboxes.first
        box_btn.scroll_into_view_if_needed()

        before_count = len(playback_intercepted)
        box_btn.click()
        page.wait_for_timeout(200)

        assert len(playback_intercepted) == before_count
        assert page.locator('[role="dialog"]').count() == 0

        box_btn.click()
        page.wait_for_timeout(200)
        assert len(playback_intercepted) == before_count

    series_tab = page.locator('button[role="tab"]:has-text("Series / Episodes")')
    assert series_tab.count() > 0
    series_tab.click()
    page.wait_for_timeout(400)

    series_checkboxes = page.locator('button[title="Select episode"]')
    if series_checkboxes.count() > 0:
        box_btn = series_checkboxes.first
        box_btn.scroll_into_view_if_needed()

        before_count = len(playback_intercepted)
        box_btn.click()
        page.wait_for_timeout(200)

        assert len(playback_intercepted) == before_count
        assert page.locator('[role="dialog"]').count() == 0

        box_btn.click()
        page.wait_for_timeout(200)
        assert len(playback_intercepted) == before_count

    page.close()

def test_category_tab_occlusion_of_navigation_controls_under_edge_widths(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector('[role="tablist"][aria-label="Video category filters"]', timeout=15000)

    occlusion_records = []

    for width in [768, 900, 1024, 1200]:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(250)

        info = page.evaluate("""() => {
            const tabs = document.querySelector('[role="tablist"][aria-label="Video category filters"]');
            const rightControls = document.querySelector('.ml-auto');
            if (!tabs || !rightControls) return null;

            const tRect = tabs.getBoundingClientRect();
            const rRect = rightControls.getBoundingClientRect();

            const isHorizontallyOverlapping = (tRect.right > rRect.left && tRect.left < rRect.right);
            const isVerticallyOverlapping = (tRect.bottom > rRect.top && tRect.top < rRect.bottom);

            const viewModeLarge = rightControls.querySelector('button[aria-label="Switch to large grid layout"]');
            let topElAtLarge = null;
            if (viewModeLarge) {
                const lr = viewModeLarge.getBoundingClientRect();
                const top = document.elementFromPoint(lr.left + lr.width / 2, lr.top + lr.height / 2);
                topElAtLarge = top ? { tag: top.tagName, text: top.innerText, ariaLabel: top.getAttribute('aria-label') } : null;
            }

            return {
                width: window.innerWidth,
                tRect: { left: tRect.left, right: tRect.right, top: tRect.top, bottom: tRect.bottom },
                rRect: { left: rRect.left, right: rRect.right, top: rRect.top, bottom: rRect.bottom },
                isHorizontallyOverlapping,
                isVerticallyOverlapping,
                topElAtLarge
            };
        }""")

        assert info is not None
        if info["isHorizontallyOverlapping"] and info["isVerticallyOverlapping"]:
            occlusion_records.append(info)

    assert len(occlusion_records) == 0, f"Detected UI occlusion of navigation controls by centered category tabs: {occlusion_records}"
    page.close()
