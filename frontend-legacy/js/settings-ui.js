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
        if (this.reloadNetwork) this.reloadNetwork();
      });
    }

    this.loadSettings();
    this.initNetworkSettings();
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

  async initNetworkSettings() {
    const lanIpBadge = document.getElementById("lanIpBadge");
    const netLanUrlInput = document.getElementById("netLanUrlInput");
    const netLanQrImg = document.getElementById("netLanQrImg");
    const btnCopyLanUrl = document.getElementById("btnCopyLanUrl");

    const cloudflareStatusPill = document.getElementById("cloudflareStatusPill");
    const netTunnelUrlInput = document.getElementById("netTunnelUrlInput");
    const btnCopyTunnelUrl = document.getElementById("btnCopyTunnelUrl");
    const btnToggleTunnel = document.getElementById("btnToggleTunnel");
    const tunnelErrorMsg = document.getElementById("tunnelErrorMsg");

    const updateNetworkUI = (netInfo) => {
      if (!netInfo) return;
      if (lanIpBadge) lanIpBadge.textContent = `LAN: ${netInfo.lan_ip || '127.0.0.1'}`;
      if (netLanUrlInput) netLanUrlInput.value = netInfo.default_url || `http://${netInfo.lan_ip}:8088`;
      if (netLanQrImg) netLanQrImg.src = `/api/system/qrcode?text=${encodeURIComponent(netInfo.default_url || `http://${netInfo.lan_ip}:8088`)}`;

      const tunnel = netInfo.tunnel || {};
      if (tunnel.status === "running" && tunnel.public_url) {
        if (cloudflareStatusPill) {
          cloudflareStatusPill.textContent = "Active (Online)";
          cloudflareStatusPill.style.background = "rgba(16, 185, 129, 0.15)";
          cloudflareStatusPill.style.color = "#10b981";
        }
        if (netTunnelUrlInput) netTunnelUrlInput.value = tunnel.public_url;
        if (btnCopyTunnelUrl) btnCopyTunnelUrl.disabled = false;
        if (btnToggleTunnel) {
          btnToggleTunnel.innerHTML = "<span>🛑 Stop Remote Tunnel</span>";
          btnToggleTunnel.style.background = "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)";
        }
        if (tunnelErrorMsg) tunnelErrorMsg.style.display = "none";
      } else {
        if (cloudflareStatusPill) {
          cloudflareStatusPill.textContent = "Stopped";
          cloudflareStatusPill.style.background = "rgba(148, 163, 184, 0.15)";
          cloudflareStatusPill.style.color = "#94a3b8";
        }
        if (netTunnelUrlInput) netTunnelUrlInput.value = "";
        if (btnCopyTunnelUrl) btnCopyTunnelUrl.disabled = true;
        if (btnToggleTunnel) {
          btnToggleTunnel.innerHTML = "<span>⚡ Start Remote Tunnel</span>";
          btnToggleTunnel.style.background = "linear-gradient(135deg, #0ea5e9 0%, #10b981 100%)";
        }
      }
    };

    const fetchNetwork = async () => {
      try {
        const res = await fetch("/api/system/network");
        if (res.ok) {
          const netInfo = await res.json();
          updateNetworkUI(netInfo);
        }
      } catch (e) {
        console.debug("Network info load error:", e);
      }
    };

    this.reloadNetwork = fetchNetwork;
    await fetchNetwork();

    if (btnCopyLanUrl && netLanUrlInput) {
      btnCopyLanUrl.addEventListener("click", () => {
        navigator.clipboard.writeText(netLanUrlInput.value).then(() => {
          showToast("📋 LAN Wi-Fi URL copied to clipboard!", "success");
        });
      });
    }

    if (btnCopyTunnelUrl && netTunnelUrlInput) {
      btnCopyTunnelUrl.addEventListener("click", () => {
        if (netTunnelUrlInput.value) {
          navigator.clipboard.writeText(netTunnelUrlInput.value).then(() => {
            showToast("📋 Remote Cloudflare Tunnel URL copied!", "success");
          });
        }
      });
    }

    if (btnToggleTunnel) {
      btnToggleTunnel.addEventListener("click", async () => {
        btnToggleTunnel.disabled = true;
        const currentText = btnToggleTunnel.textContent;
        const isRunning = currentText.includes("Stop");

        try {
          if (isRunning) {
            btnToggleTunnel.innerHTML = "<span>Stopping tunnel...</span>";
            await fetch("/api/system/tunnel/stop", { method: "POST" });
            showToast("Remote Cloudflare tunnel stopped", "info");
          } else {
            btnToggleTunnel.innerHTML = "<span>Connecting to Cloudflare...</span>";
            const startRes = await fetch("/api/system/tunnel/start", { method: "POST" });
            const startData = await startRes.json();
            if (startData.status === "running") {
              showToast(`🌐 Cloudflare Tunnel Active: ${startData.public_url}`, "success");
            } else if (startData.status === "error") {
              if (tunnelErrorMsg) {
                tunnelErrorMsg.textContent = startData.error || "Failed to start tunnel";
                tunnelErrorMsg.style.display = "block";
              }
              showToast(`Tunnel error: ${startData.error}`, "error");
            }
          }
          await fetchNetwork();
        } catch (err) {
          showToast(`Tunnel error: ${err.message}`, "error");
        } finally {
          btnToggleTunnel.disabled = false;
        }
      });
    }
  }
}

export const settingsUI = new SettingsUI();
