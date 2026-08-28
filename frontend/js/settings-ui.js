/**
 * Web Settings UI Controller
 * Manages configuration read & write to .env
 */

import { showToast } from "./utils.js";

export class SettingsUI {
  constructor() {
    this.form = null;
    this.btnSave = null;
    this.fields = {};
  }

  init(tabController) {
    this.form = document.getElementById("settingsForm");
    this.btnSave = document.getElementById("btnSaveSettings");
    this.fields = {
      PREFERRED_MANAGER: document.getElementById("setPreferredManager"),
      MIN_FILE_SIZE_MB: document.getElementById("setMinFileSize"),
      ALLOWED_EXT: document.getElementById("setAllowedExt"),
      KEYWORD_BLOCK: document.getElementById("setKeywordBlock"),
      KEYWORD_ALLOW: document.getElementById("setKeywordAllow"),
      ENABLE_NOTIFICATIONS: document.getElementById("setNotifications"),
      NOTIFICATION_MODE: document.getElementById("setNotifMode"),
      PROXY_SPEED_LIMIT_MB: document.getElementById("setProxySpeedLimit"),
      AUTO_CLEAR_DONE: document.getElementById("setAutoClearDone")
    };

    const btnTestNotification = document.getElementById("btnTestNotification");
    if (btnTestNotification) {
      btnTestNotification.addEventListener("click", async () => {
        if (!("Notification" in window)) {
          showToast("Desktop notifications not supported in this browser", "warning");
          return;
        }
        if (Notification.permission === "granted") {
          new Notification("🔔 TG Power Suite Alert", {
            body: "Desktop notification alerts are working perfectly!",
            icon: "/assets/favicon.ico"
          });
          showToast("Sample notification sent to desktop!", "success");
        } else {
          const perm = await Notification.requestPermission();
          if (perm === "granted") {
            new Notification("🔔 TG Power Suite Alert", {
              body: "Desktop notifications enabled!",
              icon: "/assets/favicon.ico"
            });
            showToast("Notifications enabled and test alert sent!", "success");
          } else {
            showToast("Notification permission was denied in browser", "warning");
          }
        }
      });
    }

    if (this.btnSave) {
      this.btnSave.addEventListener("click", () => this.saveSettings());
    }

    if (this.form) {
      this.form.addEventListener("submit", (e) => {
        e.preventDefault();
        this.saveSettings();
      });
    }

    if (tabController) {
      tabController.onTabSwitch("settings", () => {
        this.loadSettings();
      });
    }

    this.loadSettings();
  }

  async loadSettings() {
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const config = await res.json();
        Object.entries(this.fields).forEach(([key, element]) => {
          if (element && config[key] !== undefined) {
            element.value = config[key];
          }
        });
      }
    } catch (err) {
      console.debug("Could not load settings:", err);
    }
  }

  async saveSettings() {
    const payload = {};
    Object.entries(this.fields).forEach(([key, element]) => {
      if (element) {
        payload[key] = element.value.trim();
      }
    });

    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: payload })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast("Settings successfully saved to .env", "success");
      } else {
        showToast(`Failed to save settings: ${data.detail || "Unknown error"}`, "error");
      }
    } catch (err) {
      showToast(`Error saving settings: ${err.message}`, "error");
    }
  }
}

export const settingsUI = new SettingsUI();
