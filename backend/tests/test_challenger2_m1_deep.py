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

def test_layout_dock_proportions_and_no_overflow_1024_to_1920(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector("#cinema-files-catalog", timeout=15000)

    viewports = [1024, 1100, 1200, 1280, 1366, 1440, 1600, 1680, 1728, 1800, 1920]

    for width in viewports:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(250)

        data = page.evaluate("""() => {
            const docEl = document.documentElement;
            const body = document.body;
            const winWidth = window.innerWidth;
            const docScrollW = docEl.scrollWidth;
            const bodyScrollW = body.scrollWidth;

            const leftDock = document.querySelector('aside.slide-in-from-left-3');
            const rightDock = document.querySelector('aside.slide-in-from-right-3');
            const catalog = document.querySelector('#cinema-files-catalog');

            const leftRect = leftDock ? leftDock.getBoundingClientRect() : null;
            const rightRect = rightDock ? rightDock.getBoundingClientRect() : null;
            const catRect = catalog ? catalog.getBoundingClientRect() : null;

            const leftStyle = leftDock ? window.getComputedStyle(leftDock) : null;
            const rightStyle = rightDock ? window.getComputedStyle(rightDock) : null;

            return {
                winWidth,
                docScrollW,
                bodyScrollW,
                leftRect: leftRect ? { left: leftRect.left, right: leftRect.right, width: leftRect.width, top: leftRect.top, height: leftRect.height } : null,
                rightRect: rightRect ? { left: rightRect.left, right: rightRect.right, width: rightRect.width, top: rightRect.top, height: rightRect.height } : null,
                catRect: catRect ? { left: catRect.left, right: catRect.right, width: catRect.width, top: catRect.top, height: catRect.height } : null,
                leftPosition: leftStyle ? leftStyle.position : null,
                rightPosition: rightStyle ? rightStyle.position : null,
            };
        }""")

        assert data["docScrollW"] <= data["winWidth"] + 1, f"Document horizontal overflow at {width}px: scrollWidth={data['docScrollW']}, innerWidth={data['winWidth']}"
        assert data["bodyScrollW"] <= data["winWidth"] + 1, f"Body horizontal overflow at {width}px: scrollWidth={data['bodyScrollW']}, innerWidth={data['winWidth']}"

        assert data["leftRect"] is not None, f"Left channel dock not found at {width}px"
        assert data["rightRect"] is not None, f"Right continue-watching dock not found at {width}px"
        assert data["catRect"] is not None, f"Center catalog not found at {width}px"

        left_w = data["leftRect"]["width"]
        right_w = data["rightRect"]["width"]
        cat_w = data["catRect"]["width"]

        assert 185 <= left_w <= 265, f"Left dock width {left_w}px outside [190px, 260px] (+tolerance) at {width}px"
        assert 185 <= right_w <= 265, f"Right dock width {right_w}px outside [190px, 260px] (+tolerance) at {width}px"
        assert cat_w > 400, f"Center catalog width {cat_w}px squished below 400px at {width}px"

        assert data["leftPosition"] == "static", f"Left dock is not static layout at {width}px: {data['leftPosition']}"
        assert data["rightPosition"] == "static", f"Right dock is not static layout at {width}px: {data['rightPosition']}"

        assert data["leftRect"]["left"] >= 0, f"Left dock clipped on left: {data['leftRect']['left']} at {width}px"
        assert data["rightRect"]["right"] <= data["winWidth"] + 1, f"Right dock clipped on right: {data['rightRect']['right']} > {data['winWidth']} at {width}px"

        assert data["leftRect"]["right"] <= data["catRect"]["left"] + 2, f"Left dock overlaps center catalog at {width}px: left.right={data['leftRect']['right']}, cat.left={data['catRect']['left']}"
        assert data["catRect"]["right"] <= data["rightRect"]["left"] + 2, f"Center catalog overlaps right dock at {width}px: cat.right={data['catRect']['right']}, right.left={data['rightRect']['left']}"

    page.close()

def test_category_tab_centering_and_header_pill_symmetry(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector('[role="tablist"][aria-label="Video category filters"]', timeout=15000)

    test_widths = [768, 900, 1024, 1200, 1440, 1920]

    for width in test_widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(200)

        res = page.evaluate("""() => {
            const tablist = document.querySelector('[role="tablist"][aria-label="Video category filters"]');
            if (!tablist) return null;

            const tRect = tablist.getBoundingClientRect();
            const winW = window.innerWidth;
            const tablistCenter = tRect.left + tRect.width / 2;
            const winCenter = winW / 2;
            const diff = Math.abs(tablistCenter - winCenter);

            const row = tablist.closest('.border-t') || tablist.parentElement.parentElement;
            const rowRect = row ? row.getBoundingClientRect() : null;
            const rowCenter = rowRect ? (rowRect.left + rowRect.width / 2) : winCenter;
            const diffFromRow = Math.abs(tablistCenter - rowCenter);

            const channelPill = document.querySelector('button[title*="channels & chats"]');
            let pillDiff = null;
            if (channelPill) {
                const pRect = channelPill.getBoundingClientRect();
                const pCenter = pRect.left + pRect.width / 2;
                pillDiff = Math.abs(pCenter - winCenter);
            }

            return {
                winW,
                tablistCenter,
                winCenter,
                diff,
                diffFromRow,
                pillDiff,
                tRect: { left: tRect.left, right: tRect.right, width: tRect.width }
            };
        }""")

        assert res is not None, f"Category tablist missing at {width}px"
        assert res["tRect"]["left"] >= 0 and res["tRect"]["right"] <= width + 50, f"Category tabs clipped or overflowing at {width}px"

    page.close()

def test_selection_checkbox_click_strictly_toggles_and_never_plays(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector("#cinema-files-catalog", timeout=15000)

    card_selector = ".group.relative.rounded-2xl"
    page.wait_for_selector(card_selector, timeout=10000)

    cards = page.locator(card_selector)
    count = cards.count()
    assert count > 0, "No media cards found to test"

    play_requests = []
    def on_req(req):
        url = req.url
        if "/api/media/vlc/launch" in url or "/api/media/play" in url:
            play_requests.append(url)
        elif "/stream/" in url and not url.endswith("/thumb") and "/thumb" not in url:
            play_requests.append(url)

    page.on("request", on_req)

    for i in range(min(count, 3)):
        card = cards.nth(i)
        card.scroll_into_view_if_needed()
        card.hover()
        page.wait_for_timeout(150)

        checkbox = card.locator("button.backdrop-blur-md").first
        assert checkbox.is_visible(), f"Checkbox button on card {i} is not visible"

        initial_state = checkbox.get_attribute("aria-pressed") == "true" or "bg-primary" in (checkbox.get_attribute("class") or "")
        before_plays = len(play_requests)

        box = checkbox.bounding_box()
        assert box is not None, f"Checkbox bounding box is None on card {i}"
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(250)

        after_state = checkbox.get_attribute("aria-pressed") == "true" or "bg-primary" in (checkbox.get_attribute("class") or "")
        assert after_state != initial_state, f"Card {i} checkbox failed to toggle selection state on first click"
        assert len(play_requests) == before_plays, f"Card {i} checkbox click triggered playback request: {play_requests[before_plays:]}"

        modal_count = page.locator('[role="dialog"]').count()
        assert modal_count == 0, f"Card {i} checkbox click triggered video modal popup"

        box_recomputed = checkbox.bounding_box()
        assert box_recomputed is not None, f"Checkbox bounding box recomputation is None on card {i}"
        page.mouse.click(box_recomputed["x"] + box_recomputed["width"] / 2, box_recomputed["y"] + box_recomputed["height"] / 2)
        page.wait_for_timeout(250)

        reverted_state = checkbox.get_attribute("aria-pressed") == "true" or "bg-primary" in (checkbox.get_attribute("class") or "")
        assert reverted_state == initial_state, f"Card {i} checkbox failed to untoggle on second click"
        assert len(play_requests) == before_plays, f"Card {i} checkbox second click triggered playback request: {play_requests[before_plays:]}"

    page.close()

def test_absence_of_hd_across_frontend_source_and_dom(browser_context):
    page = browser_context.new_page()
    page.goto(BASE_URL)
    page.wait_for_selector('[role="tablist"][aria-label="Video category filters"]', timeout=15000)

    tabs = page.evaluate("""() => {
        const tablist = document.querySelector('[role="tablist"][aria-label="Video category filters"]');
        if (!tablist) return [];
        return Array.from(tablist.querySelectorAll('button')).map(b => ({
            text: b.innerText.trim(),
            role: b.getAttribute('role'),
            selected: b.getAttribute('aria-selected')
        }));
    }""")

    tab_labels = [t["text"] for t in tabs]
    assert "All Videos" in tab_labels, f"'All Videos' tab missing: {tab_labels}"
    assert "Movies" in tab_labels, f"'Movies' tab missing: {tab_labels}"
    assert "Series / Episodes" in tab_labels, f"'Series / Episodes' tab missing: {tab_labels}"
    assert len(tab_labels) == 3, f"Expected exactly 3 category tabs, but found {len(tab_labels)}: {tab_labels}"

    for label in tab_labels:
        assert "1080p" not in label, f"Lingering 1080p tab: {label}"
        assert "4k" not in label.lower(), f"Lingering 4k tab: {label}"
        assert label.lower() not in ["hd", "1080p & 4k"], f"Lingering HD tab: {label}"

    page.close()

    cinema_code = (FRONTEND_DIR / "src" / "views" / "CinemaView.tsx").read_text(encoding="utf-8")
    assert "<'all' | 'movies' | 'series' | 'hd'>" not in cinema_code
    assert "filter === 'hd'" not in cinema_code
    assert "filter === \"hd\"" not in cinema_code
    assert "'1080p & 4K'" not in cinema_code
    assert '"1080p & 4K"' not in cinema_code

    types_code = (FRONTEND_DIR / "src" / "types" / "index.ts").read_text(encoding="utf-8")
    assert "'hd'" not in types_code
    assert '"hd"' not in types_code
