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

    // 3. Scroll cinema view container or files catalog
    const cinemaContainer =
      document.getElementById('cinema-view-container') ||
      document.getElementById('cinema-files-catalog');
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
 * Scrolls the right-side content by a specific delta (in pixels).
 * Used when overscrolling in the chat or dialogs feed to scroll the right side files.
 */
export const scrollRightSide = (delta: number) => {
  if (typeof window === 'undefined' || typeof document === 'undefined') return;

  const mainEl = document.querySelector('main');
  const isMainScrollable =
    mainEl &&
    (window.getComputedStyle(mainEl).overflowY === 'auto' ||
      window.getComputedStyle(mainEl).overflowY === 'scroll') &&
    mainEl.scrollHeight > mainEl.clientHeight;

  if (isMainScrollable && mainEl) {
    mainEl.scrollTop += delta;
  } else {
    window.scrollBy({ top: delta, behavior: 'auto' });
  }
};
