/**
 * Tab Navigation Controller
 * Manages view switching between Uploader, Sniffer & FDM Proxy, History, and Settings.
 */

export class TabController {
  constructor() {
    this.tabButtons = [];
    this.tabPanes = {};
    this.currentTab = "uploader";
    this.listeners = new Map();
  }

  init() {
    this.tabButtons = Array.from(document.querySelectorAll(".header-nav-tabs .tab-btn"));
    this.tabPanes = {
      uploader: document.getElementById("tabPaneUploader"),
      sniffer: document.getElementById("tabPaneSniffer"),
      history: document.getElementById("tabPaneHistory"),
      cinema: document.getElementById("tabPaneCinema"),
      settings: document.getElementById("tabPaneSettings")
    };

    this.tabButtons.forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const tab = btn.dataset.tab;
        if (tab) {
          this.switchTab(tab);
        }
      });
    });

    // Check URL hash or localStorage
    const hash = window.location.hash.replace("#", "").toLowerCase();
    if (hash && this.tabPanes[hash]) {
      this.switchTab(hash);
    } else {
      const saved = localStorage.getItem("tg_active_tab");
      if (saved && this.tabPanes[saved]) {
        this.switchTab(saved);
      } else {
        this.switchTab("uploader");
      }
    }

    // Expose global helper for direct HTML events
    window._switchTab = (tabName) => this.switchTab(tabName);
  }

  onTabSwitch(tabName, callback) {
    if (!this.listeners.has(tabName)) {
      this.listeners.set(tabName, []);
    }
    this.listeners.get(tabName).push(callback);
  }

  switchTab(tabName) {
    if (!tabName) return;

    // Resolve panes dynamically if needed
    if (!this.tabPanes || !this.tabPanes[tabName]) {
      this.tabPanes = {
        uploader: document.getElementById("tabPaneUploader"),
        sniffer: document.getElementById("tabPaneSniffer"),
        history: document.getElementById("tabPaneHistory"),
        settings: document.getElementById("tabPaneSettings")
      };
    }

    const targetPane = this.tabPanes[tabName];
    if (!targetPane) return;

    this.currentTab = tabName;
    try {
      localStorage.setItem("tg_active_tab", tabName);
      history.replaceState(null, "", `#${tabName}`);
    } catch (e) {}

    // Update buttons
    const buttons = Array.from(document.querySelectorAll(".header-nav-tabs .tab-btn"));
    buttons.forEach(btn => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    // Update panes
    document.querySelectorAll(".tab-pane").forEach(pane => {
      pane.classList.remove("active");
    });
    targetPane.classList.add("active");

    // Trigger callbacks
    const callbacks = this.listeners.get(tabName) || [];
    callbacks.forEach(cb => {
      try {
        cb();
      } catch (err) {
        console.error("Tab callback error:", err);
      }
    });
  }
}

export const tabController = new TabController();
window._switchTab = (tabName) => tabController.switchTab(tabName);
