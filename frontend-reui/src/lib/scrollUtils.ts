/**
 * Cross-browser scroll synchronization utilities for TG Power Suite.
 * Handles right-side files catalog auto-scrolling and overscroll forwarding.
 */

/**
 * Smoothly scrolls the right-side files catalog and workspace to top: 0.
 * Resets window, documentElement, body, and <main> container scroll positions.
 */
export const scrollRightSideToTop = (smooth = true) => {
  const behavior: ScrollBehavior = smooth ? 'smooth' : 'auto';

  // 1. Scroll window and documentElement
  if (typeof window !== 'undefined') {
    try {
      window.scrollTo({ top: 0, behavior });
    } catch {
      window.scrollTo(0, 0);
    }
  }

  if (typeof document !== 'undefined') {
    if (document.documentElement) {
      try {
        document.documentElement.scrollTo({ top: 0, behavior });
      } catch {
        document.documentElement.scrollTop = 0;
      }
    }
    if (document.body) {
      try {
        document.body.scrollTo({ top: 0, behavior });
      } catch {
        document.body.scrollTop = 0;
      }
    }

    // 2. Scroll <main> element if present
    const mainEl = document.querySelector('main');
    if (mainEl) {
      try {
        mainEl.scrollTo({ top: 0, behavior });
      } catch {
        mainEl.scrollTop = 0;
      }
    }

    // 3. Scroll cinema files catalog element to top
    const filesCatalog = document.getElementById('cinema-files-catalog');
    if (filesCatalog) {
      try {
        filesCatalog.scrollTo({ top: 0, behavior });
      } catch {
        filesCatalog.scrollTop = 0;
      }
    }

    const cinemaContainer = document.getElementById('cinema-view-container');
    if (cinemaContainer && cinemaContainer.scrollTop > 0) {
      try {
        cinemaContainer.scrollTo({ top: 0, behavior });
      } catch {
        cinemaContainer.scrollTop = 0;
      }
    }
  }
};

/**
 * Scrolls the right-side files catalog by a specific delta (in pixels).
 * Forwards overscroll wheel events from sidebar panels to the files catalog.
 */
export const scrollRightSide = (delta: number) => {
  if (typeof window === 'undefined' || typeof document === 'undefined') return;

  const filesCatalog = document.getElementById('cinema-files-catalog');
  if (filesCatalog && filesCatalog.scrollHeight > filesCatalog.clientHeight) {
    filesCatalog.scrollTop += delta;
    return;
  }

  const mainEl = document.querySelector('main');
  const isMainScrollable =
    mainEl &&
    (window.getComputedStyle(mainEl).overflowY === 'auto' ||
      window.getComputedStyle(mainEl).overflowY === 'scroll') &&
    mainEl.scrollHeight > mainEl.clientHeight;

  if (isMainScrollable && mainEl) {
    mainEl.scrollTop += delta;
  }
};
