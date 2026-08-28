/**
 * Sniffer & FDM Proxy UI Controller
 * Manages Watched Channels Modal (Destination-picker style with Channels, Groups, Bots, and Contacts),
 * Auto-Dispatch Engine status, and Live Dispatched Stream.
 */

import { showToast, escapeHtml } from "./utils.js";

export class SnifferUI {
  constructor() {
    this.watchedChannelsList = null;
    this.watchedChannelsCount = null;
    this.activeWatchedCountLabel = null;
    this.btnOpenWatchModal = null;
    this.watchChannelModal = null;
    this.btnCloseWatchModal = null;
    this.watchChannelSearchInput = null;
    this.watchModalFilterTabs = null;
    this.watchChannelListContainer = null;
    this.btnRefreshWatchDialogs = null;
    this.btnToggleManualChannel = null;
    this.formAddChannel = null;
    this.inputChannelId = null;
    this.detectedManagersGrid = null;
    this.snifferFeedContainer = null;
    this.snifferFeedCount = null;
    this.btnRefreshSnifferFeed = null;
    this.snifferStatusBadge = null;
    this.btnGoToSettings = null;

    this.cachedChats = [];
    this.activeChannels = new Set();
    this.currentFilter = "all";
  }

  init(socket, tabController) {
    this.watchedChannelsList = document.getElementById("channelChipsList");
    this.watchedChannelsCount = document.getElementById("watchedChannelsCount");
    this.activeWatchedCountLabel = document.getElementById("activeWatchedCountLabel");
    this.btnOpenWatchModal = document.getElementById("btnOpenWatchModal");
    this.watchChannelModal = document.getElementById("watchChannelModal");
    this.btnCloseWatchModal = document.getElementById("btnCloseWatchModal");
    this.watchChannelSearchInput = document.getElementById("watchChannelSearchInput");
    this.watchModalFilterTabs = document.getElementById("watchModalFilterTabs");
    this.watchChannelListContainer = document.getElementById("watchChannelListContainer");
    this.btnRefreshWatchDialogs = document.getElementById("btnRefreshWatchDialogs");

    this.btnToggleManualChannel = document.getElementById("btnToggleManualChannel");
    this.formAddChannel = document.getElementById("formAddChannel");
    this.inputChannelId = document.getElementById("inputChannelId");
    this.detectedManagersGrid = document.getElementById("detectedManagersGrid");
    this.snifferFeedContainer = document.getElementById("snifferFeedContainer");
    this.snifferFeedCount = document.getElementById("snifferFeedCount");
    this.btnRefreshSnifferFeed = document.getElementById("btnRefreshSnifferFeed");
    this.snifferStatusBadge = document.getElementById("snifferStatusBadge");
    this.btnGoToSettings = document.getElementById("btnGoToSettings");

    // Modal open/close triggers
    if (this.btnOpenWatchModal) {
      this.btnOpenWatchModal.addEventListener("click", () => this.openModal());
    }

    if (this.btnCloseWatchModal) {
      this.btnCloseWatchModal.addEventListener("click", () => this.closeModal());
    }

    if (this.watchChannelModal) {
      this.watchChannelModal.addEventListener("click", (e) => {
        if (e.target === this.watchChannelModal) this.closeModal();
      });
    }

    if (this.watchChannelSearchInput) {
      this.watchChannelSearchInput.addEventListener("input", (e) => {
        this.renderModalList(e.target.value);
      });
    }

    // Filter tabs
    if (this.watchModalFilterTabs) {
      this.watchModalFilterTabs.querySelectorAll(".filter-chip").forEach(chip => {
        chip.addEventListener("click", () => {
          this.watchModalFilterTabs.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("active"));
          chip.classList.add("active");
          this.currentFilter = chip.dataset.filter || "all";
          this.renderModalList(this.watchChannelSearchInput ? this.watchChannelSearchInput.value : "");
        });
      });
    }

    if (this.btnRefreshWatchDialogs) {
      this.btnRefreshWatchDialogs.addEventListener("click", () => {
        this.fetchAccountChannels(true);
        showToast("Refreshing dialogs list...", "info");
      });
    }

    // Toggle manual input form
    if (this.btnToggleManualChannel && this.formAddChannel) {
      this.btnToggleManualChannel.addEventListener("click", () => {
        const isHidden = this.formAddChannel.style.display === "none";
        this.formAddChannel.style.display = isHidden ? "flex" : "none";
        this.btnToggleManualChannel.textContent = isHidden
          ? "▲ Hide manual custom input"
          : "✏️ Or enter custom Channel ID / @username";
        if (isHidden && this.inputChannelId) {
          this.inputChannelId.focus();
        }
      });
    }

    // Manual custom channel submit
    if (this.formAddChannel) {
      this.formAddChannel.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!this.inputChannelId) return;
        const val = this.inputChannelId.value.trim();
        if (val) {
          this.addChannel(val);
          this.inputChannelId.value = "";
        }
      });
    }

    if (this.btnRefreshSnifferFeed) {
      this.btnRefreshSnifferFeed.addEventListener("click", () => {
        this.fetchFeed();
        this.fetchStatus();
      });
    }

    if (this.btnGoToSettings && tabController) {
      this.btnGoToSettings.addEventListener("click", () => {
        tabController.switchTab("settings");
      });
    }

    // Socket.IO listeners
    if (socket) {
      socket.on("sniffer:status", (status) => {
        this.renderStatus(status);
      });

      socket.on("sniffer:feed_snapshot", (feed) => {
        this.renderFeed(feed);
      });

      socket.on("sniffer:sniffer_feed", (item) => {
        this.prependFeedItem(item);
      });
    }

    // Initial fetches
    this.fetchStatus();
    this.fetchFeed();
    this.fetchAccountChannels();

    window._snifferUI = this;
  }

  openModal() {
    if (!this.watchChannelModal) return;
    this.watchChannelModal.classList.add("open");
    if (this.watchChannelSearchInput) {
      this.watchChannelSearchInput.value = "";
      setTimeout(() => this.watchChannelSearchInput.focus(), 150);
    }
    this.renderModalList("");
  }

  closeModal() {
    if (!this.watchChannelModal) return;
    this.watchChannelModal.classList.remove("open");
  }

  async fetchAccountChannels(force = false) {
    try {
      const url = force ? "/api/chats?force_refresh=true" : "/api/chats";
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const chats = await res.json();
      // Exclude only Saved Messages (own cloud storage)
      this.cachedChats = (chats || []).filter(c => c.type !== "saved_messages");
      
      this.updateFilterBadgeCounts();
      this.renderModalList(this.watchChannelSearchInput ? this.watchChannelSearchInput.value : "");
    } catch (err) {
      console.debug("Could not load account dialogs:", err);
    }
  }

  updateFilterBadgeCounts() {
    if (!this.watchModalFilterTabs) return;
    const channels = this.cachedChats.filter(c => c.type === "channel").length;
    const groups = this.cachedChats.filter(c => c.type === "group" || c.type === "supergroup").length;
    const bots = this.cachedChats.filter(c => c.type === "bot").length;
    const users = this.cachedChats.filter(c => c.type === "user").length;
    const all = this.cachedChats.length;

    const chips = this.watchModalFilterTabs.querySelectorAll(".filter-chip");
    chips.forEach(chip => {
      const f = chip.dataset.filter;
      if (f === "all") chip.textContent = `All (${all})`;
      if (f === "channel") chip.textContent = `📢 Channels (${channels})`;
      if (f === "group") chip.textContent = `👥 Groups (${groups})`;
      if (f === "bot") chip.textContent = `🤖 Bots (${bots})`;
      if (f === "user") chip.textContent = `👤 Contacts (${users})`;
    });
  }

  renderModalList(searchQuery = "") {
    if (!this.watchChannelListContainer) return;

    const query = (searchQuery || "").toLowerCase().trim();
    let filtered = this.cachedChats;

    // Apply category filter
    if (this.currentFilter === "channel") {
      filtered = filtered.filter(c => c.type === "channel");
    } else if (this.currentFilter === "group") {
      filtered = filtered.filter(c => c.type === "group" || c.type === "supergroup");
    } else if (this.currentFilter === "bot") {
      filtered = filtered.filter(c => c.type === "bot");
    } else if (this.currentFilter === "user") {
      filtered = filtered.filter(c => c.type === "user");
    }

    // Apply text search
    if (query) {
      filtered = filtered.filter(c => {
        const name = (c.name || "").toLowerCase();
        const username = (c.username || "").toLowerCase();
        const id = String(c.id);
        return name.includes(query) || username.includes(query) || id.includes(query);
      });
    }

    if (filtered.length === 0) {
      this.watchChannelListContainer.innerHTML = `
        <div style="text-align: center; padding: 40px 16px; color: var(--text-muted);">
          <div style="font-size: 2rem; opacity: 0.5; margin-bottom: 8px;">🔍</div>
          <p style="font-size: 0.9rem;">No matching dialogs found.</p>
        </div>
      `;
      return;
    }

    // Cap display to top 100 items for instant DOM rendering
    const displayList = filtered.slice(0, 100);

    this.watchChannelListContainer.innerHTML = displayList.map(ch => {
      let icon = "📢";
      let typeLabel = "CHANNEL";
      let avatarBg = "linear-gradient(135deg, #00cec9, #6c5ce7)";

      if (ch.type === "supergroup" || ch.type === "group") {
        icon = "👥";
        typeLabel = "GROUP";
        avatarBg = "linear-gradient(135deg, #fd79a8, #6c5ce7)";
      } else if (ch.type === "bot") {
        icon = "🤖";
        typeLabel = "BOT";
        avatarBg = "linear-gradient(135deg, #00b894, #0984e3)";
      } else if (ch.type === "user") {
        icon = "👤";
        typeLabel = "CONTACT";
        avatarBg = "linear-gradient(135deg, #fdcb6e, #e17055)";
      }

      const identifier = ch.username ? `@${ch.username}` : (String(ch.id).startsWith("-") ? String(ch.id) : (ch.type === "channel" || ch.type === "supergroup" ? `-100${ch.id}` : String(ch.id)));
      
      // Check if currently watched
      let isWatched = false;
      for (const ac of this.activeChannels) {
        const strAc = String(ac).toLowerCase().replace(/^@/, '');
        const cleanId = String(identifier).toLowerCase().replace(/^@/, '');
        if (strAc === cleanId || String(ac) === String(ch.id) || String(ac) === `-100${ch.id}`) {
          isWatched = true;
          break;
        }
      }

      const handle = ch.username ? `@${ch.username}` : `ID: ${ch.id}`;

      return `
        <div class="chat-option-item ${isWatched ? 'selected' : ''}" data-identifier="${escapeHtml(identifier)}" style="display: flex; align-items: center; justify-content: space-between; padding: 12px 16px;">
          <div style="display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1;">
            <div class="chat-avatar" style="background: ${avatarBg}; width: 38px; height: 38px; font-size: 1.1rem;">
              ${icon}
            </div>
            <div class="chat-meta" style="min-width: 0;">
              <div class="chat-meta-name" title="${escapeHtml(ch.name)}">${escapeHtml(ch.name)}</div>
              <div class="chat-meta-sub" style="display: flex; align-items: center; gap: 8px;">
                <span>${escapeHtml(handle)}</span>
                <span class="chat-type-tag" style="font-size: 0.65rem; padding: 1px 6px;">${typeLabel}</span>
              </div>
            </div>
          </div>
          
          <button class="btn-toggle-watch ${isWatched ? 'btn-secondary' : 'btn-primary'}" 
                  data-identifier="${escapeHtml(identifier)}" 
                  data-watched="${isWatched ? 'true' : 'false'}"
                  type="button" 
                  style="padding: 6px 14px; font-size: 0.78rem; border-radius: var(--radius-full); white-space: nowrap; flex-shrink: 0;">
            ${isWatched ? '✓ Watched' : '+ Watch'}
          </button>
        </div>
      `;
    }).join("");

    // Attach 1-click add/remove handlers
    this.watchChannelListContainer.querySelectorAll(".btn-toggle-watch").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = btn.dataset.identifier;
        const isWatched = btn.dataset.watched === "true";

        if (isWatched) {
          await this.handleRemoveChannel(id);
          btn.dataset.watched = "false";
          btn.className = "btn-toggle-watch btn-primary";
          btn.textContent = "+ Watch";
          const row = btn.closest(".chat-option-item");
          if (row) row.classList.remove("selected");
        } else {
          await this.addChannel(id);
          btn.dataset.watched = "true";
          btn.className = "btn-toggle-watch btn-secondary";
          btn.textContent = "✓ Watched";
          const row = btn.closest(".chat-option-item");
          if (row) row.classList.add("selected");
        }
      });
    });
  }

  async fetchStatus() {
    try {
      const res = await fetch("/api/sniffer/status");
      if (res.ok) {
        const data = await res.json();
        this.renderStatus(data);
      }
    } catch (err) {
      console.debug("Could not fetch sniffer status:", err);
    }
  }

  async fetchFeed() {
    try {
      const res = await fetch("/api/sniffer/feed");
      if (res.ok) {
        const feed = await res.json();
        this.renderFeed(feed);
      }
    } catch (err) {
      console.debug("Could not fetch sniffer feed:", err);
    }
  }

  async addChannel(channelValue) {
    const val = String(channelValue).trim();
    if (!val) return;

    try {
      const res = await fetch("/api/sniffer/channels/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel: val })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(`Watched channel '${val}' added ✓`, "success");
        this.fetchStatus();
      } else {
        showToast(`Channel already watched or invalid`, "warning");
      }
    } catch (err) {
      showToast(`Error adding channel: ${err.message}`, "error");
    }
  }

  async handleRemoveChannel(channel) {
    try {
      const res = await fetch("/api/sniffer/channels/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(`Channel '${channel}' removed`, "info");
        this.fetchStatus();
      }
    } catch (err) {
      showToast(`Error removing channel: ${err.message}`, "error");
    }
  }

  _resolveChannelDisplay(channelIdentifier) {
    const raw = String(channelIdentifier).trim();
    const cleanHandle = raw.toLowerCase().replace(/^@/, '');
    const cleanId = raw.replace(/^-100/, '');

    // Search in cached chats
    const found = this.cachedChats.find(c => {
      const cUser = (c.username || '').toLowerCase();
      const cId = String(c.id).replace(/^-100/, '');
      return (cUser && cUser === cleanHandle) || cId === cleanId || String(c.id) === raw;
    });

    if (found) {
      let icon = "📢";
      if (found.type === "supergroup" || found.type === "group") icon = "👥";
      else if (found.type === "bot") icon = "🤖";
      else if (found.type === "user") icon = "👤";

      return {
        icon,
        name: found.name || raw,
        handle: found.username ? `@${found.username}` : `ID: ${found.id}`,
        raw
      };
    }

    // Default icon for custom/manual entries
    let icon = "📡";
    if (raw.toLowerCase().endsWith("bot")) icon = "🤖";
    else if (raw.startsWith("@")) icon = "📢";

    return {
      icon,
      name: raw,
      handle: raw,
      raw
    };
  }

  renderStatus(status) {
    if (!status) return;

    const channels = status.active_channels || [];
    this.activeChannels = new Set(channels);

    if (this.watchedChannelsCount) {
      this.watchedChannelsCount.textContent = channels.length;
    }

    if (this.activeWatchedCountLabel) {
      this.activeWatchedCountLabel.textContent = `${channels.length} Monitored`;
    }

    if (this.watchedChannelsList) {
      if (channels.length === 0) {
        this.watchedChannelsList.innerHTML = `<div class="empty-hint" style="color: var(--text-muted); font-size: 0.85rem;">No channels watched yet. Click "+ Pick Channels" above.</div>`;
      } else {
        this.watchedChannelsList.innerHTML = channels.map(ch => {
          const info = this._resolveChannelDisplay(ch);
          return `
            <div class="channel-chip" title="${escapeHtml(info.raw)} (${escapeHtml(info.handle)})">
              <span class="chip-icon">${info.icon}</span>
              <span class="chip-name">${escapeHtml(info.name)}</span>
              <button class="channel-chip-remove" title="Stop watching" data-channel="${escapeHtml(ch)}">&times;</button>
            </div>
          `;
        }).join("");

        this.watchedChannelsList.querySelectorAll(".channel-chip-remove").forEach(btn => {
          btn.addEventListener("click", (e) => {
            const ch = e.currentTarget.dataset.channel;
            if (ch) this.handleRemoveChannel(ch);
          });
        });
      }
    }

    // Update modal if open
    if (this.watchChannelModal && this.watchChannelModal.classList.contains("open")) {
      this.renderModalList(this.watchChannelSearchInput ? this.watchChannelSearchInput.value : "");
    }

    // Detected Managers
    if (this.detectedManagersGrid) {
      const managers = status.detected_managers || {};
      const entries = Object.entries(managers);
      if (entries.length === 0) {
        this.detectedManagersGrid.innerHTML = `
          <div style="font-size: 0.85rem; color: var(--text-muted); grid-column: 1 / -1;">
            No external download manager (FDM, aria2, NeatDM) found. Direct streaming mode active.
          </div>
        `;
      } else {
        this.detectedManagersGrid.innerHTML = entries.map(([id, path]) => `
          <div class="manager-card">
            <div class="mgr-title">${escapeHtml(id.toUpperCase())}</div>
            <div class="mgr-badge badge-status-online">Installed</div>
            <div style="font-size: 0.7rem; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              ${escapeHtml(path)}
            </div>
          </div>
        `).join("");
      }
    }
  }

  renderFeed(feed) {
    if (!this.snifferFeedContainer) return;
    const items = feed || [];
    if (this.snifferFeedCount) {
      this.snifferFeedCount.textContent = items.length;
    }

    if (items.length === 0) {
      this.snifferFeedContainer.innerHTML = `
        <div class="feed-empty-state">
          <div style="font-size: 2.5rem; margin-bottom: 12px; opacity: 0.5;">📡</div>
          <p style="font-weight: 500; font-size: 1.05rem; color: var(--text-main); margin-bottom: 6px;">Sniffer Waiting for Media</p>
          <p style="font-size: 0.85rem;">New media posted to watched channels will automatically appear here and trigger your download manager.</p>
        </div>
      `;
      return;
    }

    this.snifferFeedContainer.innerHTML = items.map(item => this._renderFeedCardHtml(item)).join("");
    this._attachFeedCardListeners();
  }

  prependFeedItem(item) {
    if (!this.snifferFeedContainer) return;
    const emptyState = this.snifferFeedContainer.querySelector(".feed-empty-state");
    if (emptyState) {
      this.snifferFeedContainer.innerHTML = "";
    }

    const temp = document.createElement("div");
    temp.innerHTML = this._renderFeedCardHtml(item);
    const card = temp.firstElementChild;
    this.snifferFeedContainer.prepend(card);

    if (this.snifferFeedCount) {
      const current = parseInt(this.snifferFeedCount.textContent || "0");
      this.snifferFeedCount.textContent = current + 1;
    }

    this._attachFeedCardListeners(card);
  }

  _renderFeedCardHtml(item) {
    return `
      <div class="feed-card" data-chat="${item.chat_id}" data-msg="${item.message_id}" data-url="${item.download_url}">
        <div class="feed-card-left">
          <div class="feed-filename" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</div>
          <div class="feed-meta">
            <span style="color: var(--accent-secondary); font-weight: 600;">${escapeHtml(item.size_formatted)}</span>
            <span style="background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px;">${escapeHtml(item.quality)}</span>
            <span>Manager: <b>${escapeHtml(item.manager)}</b></span>
            <span class="${item.status === 'dispatched' ? 'badge-status-online' : 'badge-status-offline'}">${escapeHtml(item.status.toUpperCase())}</span>
          </div>
        </div>
        <div class="feed-actions">
          <button class="btn-secondary btn-copy-link" style="padding: 6px 10px; font-size: 0.75rem;" type="button">
            📋 Copy Link
          </button>
          <button class="btn-primary btn-trigger-fdm" style="padding: 6px 12px; font-size: 0.75rem;" type="button">
            🚀 Send to FDM
          </button>
        </div>
      </div>
    `;
  }

  _attachFeedCardListeners(scope = this.snifferFeedContainer) {
    scope.querySelectorAll(".btn-copy-link").forEach(btn => {
      btn.onclick = (e) => {
        const card = e.target.closest(".feed-card");
        const url = card.dataset.url;
        if (url) {
          navigator.clipboard.writeText(url);
          showToast("Proxy download link copied!", "success");
        }
      };
    });

    scope.querySelectorAll(".btn-trigger-fdm").forEach(btn => {
      btn.onclick = async (e) => {
        const card = e.target.closest(".feed-card");
        const chatId = card.dataset.chat;
        const msgId = card.dataset.msg;
        try {
          const res = await fetch(`/api/proxy/trigger?chat_id=${chatId}&message_id=${msgId}`, { method: "POST" });
          const data = await res.json();
          if (data.success) {
            showToast(`Sent to ${data.manager.toUpperCase()}`, "success");
          } else {
            showToast("Failed to launch manager", "error");
          }
        } catch (err) {
          showToast(`Error: ${err.message}`, "error");
        }
      };
    });
  }
}

export const snifferUI = new SnifferUI();
