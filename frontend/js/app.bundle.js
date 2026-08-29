(() => {
  // frontend/js/socket.js
  var SocketManager = class {
    constructor() {
      this.socket = null;
      this.progressListeners = /* @__PURE__ */ new Set();
      this.queueListeners = /* @__PURE__ */ new Set();
      this.connectionListeners = /* @__PURE__ */ new Set();
      this.isConnected = false;
    }
    init() {
      if (typeof io === "undefined") {
        console.error("Socket.IO client library is not loaded!");
        return;
      }
      this.socket = io({
        path: "/socket.io",
        transports: ["websocket", "polling"],
        reconnectionAttempts: 20,
        reconnectionDelay: 1e3,
        reconnectionDelayMax: 5e3,
        timeout: 2e4
      });
      this.socket.on("connect", () => {
        console.log("[WS] Connected to backend Socket.IO. ID:", this.socket.id);
        this.isConnected = true;
        this._notifyConnection(true);
        this.socket.emit("queue:get");
      });
      this.socket.on("disconnect", (reason) => {
        console.warn("[WS] Disconnected from backend Socket.IO. Reason:", reason);
        this.isConnected = false;
        this._notifyConnection(false);
      });
      this.socket.on("upload:progress", (data) => {
        this.progressListeners.forEach((fn) => {
          try {
            fn(data);
          } catch (e) {
            console.error("Error in progress listener:", e);
          }
        });
      });
      this.socket.on("queue:snapshot", (tasks) => {
        this.queueListeners.forEach((fn) => {
          try {
            fn(tasks);
          } catch (e) {
            console.error("Error in queue snapshot listener:", e);
          }
        });
      });
    }
    onProgress(callback) {
      this.progressListeners.add(callback);
      return () => this.progressListeners.delete(callback);
    }
    onQueueSnapshot(callback) {
      this.queueListeners.add(callback);
      return () => this.queueListeners.delete(callback);
    }
    onConnectionChange(callback) {
      this.connectionListeners.add(callback);
      callback(this.isConnected);
      return () => this.connectionListeners.delete(callback);
    }
    _notifyConnection(state) {
      this.connectionListeners.forEach((fn) => fn(state));
    }
    pauseTask(taskId) {
      if (this.socket && this.isConnected) {
        this.socket.emit("upload_pause", { id: taskId });
      }
    }
    resumeTask(taskId) {
      if (this.socket && this.isConnected) {
        this.socket.emit("upload_resume", { id: taskId });
      }
    }
    cancelTask(taskId) {
      if (this.socket && this.isConnected) {
        this.socket.emit("upload_cancel", { id: taskId });
      }
    }
  };
  var socketManager = new SocketManager();

  // frontend/js/utils.js
  function formatBytes(bytes, decimals = 2) {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
  }
  function formatSpeed(bytesPerSec) {
    if (!bytesPerSec || bytesPerSec <= 0) return "0 KB/s";
    return formatBytes(bytesPerSec, 1) + "/s";
  }
  function formatETA(seconds) {
    if (!seconds || seconds <= 0 || !isFinite(seconds)) return "--";
    const sec = Math.round(seconds);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    const remSec = sec % 60;
    if (min < 60) return `${min}m ${remSec}s`;
    const hrs = Math.floor(min / 60);
    const remMin = min % 60;
    return `${hrs}h ${remMin}m`;
  }
  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }
  function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    let icon = "\u2139\uFE0F";
    if (type === "success") icon = "\u2705";
    if (type === "error") icon = "\u274C";
    if (type === "warning") icon = "\u26A0\uFE0F";
    toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateX(100%)";
      toast.style.transition = "all 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 4e3);
  }
  function getFileCategory(file) {
    const type = (file.type || "").toLowerCase();
    const name = (file.name || "").toLowerCase();
    if (type.startsWith("image/")) return "photo";
    if (type.startsWith("video/")) return "video";
    if (type.startsWith("audio/")) return "audio";
    const ext = name.split(".").pop();
    if (["jpg", "jpeg", "png", "webp", "gif"].includes(ext)) return "photo";
    if (["mp4", "mkv", "mov", "avi", "webm", "flv"].includes(ext)) return "video";
    if (["mp3", "wav", "flac", "aac", "ogg", "m4a"].includes(ext)) return "audio";
    if (["zip", "rar", "7z", "tar", "gz", "iso"].includes(ext)) return "archive";
    if (["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt"].includes(ext)) return "document";
    return "document";
  }

  // frontend/js/chat-picker.js
  var ChatPicker = class {
    constructor() {
      this.modal = null;
      this.searchInput = null;
      this.listContainer = null;
      this.chats = [];
      this.selectedChat = {
        id: "me",
        name: "Saved Messages (Personal Cloud)",
        type: "saved_messages"
      };
      this.onSelectCallback = null;
      this.storageKey = "tg_selected_chat";
      this.isLoading = false;
    }
    init(onSelect) {
      this.onSelectCallback = onSelect;
      this.modal = document.getElementById("chatModal");
      this.searchInput = document.getElementById("chatSearchInput");
      this.listContainer = document.getElementById("chatListContainer");
      const openBtn = document.getElementById("btnChooseChat");
      const closeBtn = document.getElementById("btnCloseChatModal");
      if (openBtn) openBtn.addEventListener("click", () => this.open());
      if (closeBtn) closeBtn.addEventListener("click", () => this.close());
      if (this.modal) {
        this.modal.addEventListener("click", (e) => {
          if (e.target === this.modal) this.close();
        });
      }
      if (this.searchInput) {
        this.searchInput.addEventListener("input", (e) => {
          this.renderList(e.target.value);
        });
      }
      this.loadSavedSelection();
      setTimeout(() => this.fetchChats(), 100);
    }
    loadSavedSelection() {
      try {
        const saved = localStorage.getItem(this.storageKey);
        if (saved) {
          this.selectedChat = JSON.parse(saved);
        }
      } catch (e) {
        console.warn("Could not load saved chat from localStorage:", e);
      }
      this.updateTriggerUI(this.selectedChat);
      if (this.onSelectCallback) this.onSelectCallback(this.selectedChat);
    }
    saveSelection(chat) {
      this.selectedChat = chat;
      try {
        localStorage.setItem(this.storageKey, JSON.stringify(chat));
      } catch (e) {
      }
      this.updateTriggerUI(chat);
      if (this.onSelectCallback) this.onSelectCallback(chat);
    }
    updateTriggerUI(chat) {
      const nameEl = document.getElementById("currentChatName");
      const typeEl = document.getElementById("currentChatType");
      if (nameEl && chat) {
        nameEl.textContent = chat.name || "Saved Messages";
      }
      if (typeEl && chat) {
        const typeLabel = chat.type === "saved_messages" ? "CLOUD" : (chat.type || "CHAT").replace("_", " ").toUpperCase();
        typeEl.textContent = typeLabel;
      }
    }
    async fetchChats(force = false) {
      if (this.isLoading) return;
      this.isLoading = true;
      try {
        const url = force ? "/api/chats?force_refresh=true" : "/api/chats";
        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to fetch chats");
        this.chats = await res.json();
        if (this.selectedChat && this.selectedChat.type === "saved_messages" && this.chats.length > 0) {
          this.selectedChat = this.chats[0];
        }
        if (this.modal && this.modal.classList.contains("open")) {
          this.renderList(this.searchInput ? this.searchInput.value : "");
        }
      } catch (e) {
        console.error("Error loading chats:", e);
      } finally {
        this.isLoading = false;
      }
    }
    open() {
      if (!this.modal) return;
      this.modal.classList.add("open");
      if (this.searchInput) {
        this.searchInput.value = "";
        this.searchInput.focus();
      }
      if (this.chats.length === 0) {
        this.renderLoading();
        this.fetchChats();
      } else {
        this.renderList();
      }
    }
    renderLoading() {
      if (!this.listContainer) return;
      this.listContainer.innerHTML = `
      <div style="padding: 32px; text-align: center; color: var(--text-muted);">
        <div style="display: inline-block; width: 24px; height: 24px; border: 2px solid var(--accent-primary); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; margin-bottom: 12px;"></div>
        <p>Loading Telegram chats...</p>
      </div>
    `;
    }
    close() {
      if (!this.modal) return;
      this.modal.classList.remove("open");
    }
    renderList(filter = "") {
      if (!this.listContainer) return;
      const q = filter.trim().toLowerCase();
      const filtered = this.chats.filter((c) => {
        if (!q) return true;
        const nameMatch = c.name.toLowerCase().includes(q);
        const userMatch = c.username ? c.username.toLowerCase().includes(q) : false;
        return nameMatch || userMatch;
      });
      if (filtered.length === 0) {
        this.listContainer.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--text-muted);">
          No chats found matching "${escapeHtml(filter)}"
        </div>
      `;
        return;
      }
      this.listContainer.innerHTML = filtered.map((c) => {
        const isSelected = this.selectedChat && this.selectedChat.id === c.id;
        const initial = (c.name || "C").charAt(0).toUpperCase();
        const typeLabel = c.type === "saved_messages" ? "Cloud" : c.type.toUpperCase();
        return `
        <div class="chat-option-item ${isSelected ? "selected" : ""}" data-id="${c.id}">
          <div class="chat-avatar">${escapeHtml(initial)}</div>
          <div class="chat-meta">
            <div class="chat-meta-name">${escapeHtml(c.name)}</div>
            <div class="chat-meta-sub">
              ${c.username ? "@" + escapeHtml(c.username) + " \u2022 " : ""}
              <span class="chat-type-tag">${escapeHtml(typeLabel)}</span>
            </div>
          </div>
          ${isSelected ? '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#00cec9" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>' : ""}
        </div>
      `;
      }).join("");
      this.listContainer.querySelectorAll(".chat-option-item").forEach((el) => {
        el.addEventListener("click", () => {
          const rawId = el.getAttribute("data-id");
          const chat = this.chats.find((c) => String(c.id) === String(rawId));
          if (chat) {
            this.saveSelection(chat);
            this.close();
          }
        });
      });
    }
    getSelectedChat() {
      return this.selectedChat;
    }
  };
  var chatPicker = new ChatPicker();

  // frontend/js/preview.js
  var activePreviews = /* @__PURE__ */ new Map();
  function createSafePreview(fileId, file) {
    if (activePreviews.has(fileId)) {
      return activePreviews.get(fileId);
    }
    const category = getFileCategory(file);
    let previewUrl = null;
    let hasObjectUrl = false;
    if (category === "photo" || category === "video" && file.size < 100 * 1024 * 1024) {
      try {
        previewUrl = URL.createObjectURL(file);
        hasObjectUrl = true;
      } catch (e) {
        console.warn("Could not create Object URL preview:", e);
      }
    }
    const previewObj = {
      fileId,
      category,
      url: previewUrl,
      hasObjectUrl,
      revoke: () => {
        if (hasObjectUrl && previewUrl) {
          try {
            URL.revokeObjectURL(previewUrl);
          } catch (e) {
            console.warn("Error revoking Object URL:", e);
          }
          previewUrl = null;
          hasObjectUrl = false;
        }
        activePreviews.delete(fileId);
      }
    };
    activePreviews.set(fileId, previewObj);
    return previewObj;
  }
  function revokePreview(fileId) {
    const p = activePreviews.get(fileId);
    if (p) {
      p.revoke();
    }
  }

  // frontend/js/uploader.js
  var Uploader = class {
    constructor() {
      this.queue = [];
      this.activeTask = null;
      this.isProcessing = false;
      this.activeXhrs = /* @__PURE__ */ new Map();
      this.onQueueChangeCallbacks = /* @__PURE__ */ new Set();
    }
    onQueueChange(cb) {
      this.onQueueChangeCallbacks.add(cb);
      return () => this.onQueueChangeCallbacks.delete(cb);
    }
    _notify() {
      this.onQueueChangeCallbacks.forEach((cb) => {
        try {
          cb(this.queue);
        } catch (e) {
          console.error("Queue change callback error:", e);
        }
      });
    }
    addFiles(fileList) {
      const selectedChat = chatPicker.getSelectedChat();
      const chatId = selectedChat ? selectedChat.id : "me";
      const chatName = selectedChat ? selectedChat.name : "Saved Messages (Personal Cloud)";
      Array.from(fileList).forEach((file) => {
        const id = "task_" + Math.random().toString(36).substring(2, 10) + "_" + Date.now();
        const preview = createSafePreview(id, file);
        const task = {
          id,
          file,
          filename: file.name,
          customFilename: file.name,
          fileSize: file.size,
          chatId,
          chatName,
          caption: "",
          sendAs: "auto",
          // auto | document | media
          status: "queued",
          // queued | streaming | uploading | splitting | paused | completed | failed | cancelled
          progress: 0,
          uploadedBytes: 0,
          speed: 0,
          eta: 0,
          currentPart: 1,
          totalParts: 1,
          error: null,
          preview,
          transferredToServer: false,
          // Prevents duplicate XHR uploads
          isTransferring: false
        };
        this.queue.push(task);
      });
      this._notify();
      this.processNext();
    }
    updateTaskConfig(id, { customFilename, caption, sendAs }) {
      const task = this.queue.find((t) => t.id === id);
      if (task && task.status === "queued" && !task.transferredToServer) {
        if (customFilename !== void 0) task.customFilename = customFilename;
        if (caption !== void 0) task.caption = caption;
        if (sendAs !== void 0) task.sendAs = sendAs;
        this._notify();
      }
    }
    processNext() {
      if (this.isProcessing) return;
      const nextTask = this.queue.find(
        (t) => (t.status === "queued" || t.status === "streaming") && !t.transferredToServer && !t.isTransferring
      );
      if (!nextTask) return;
      this.isProcessing = true;
      this.activeTask = nextTask;
      nextTask.isTransferring = true;
      nextTask.status = "streaming";
      nextTask.progress = 0;
      this._notify();
      const formData = new FormData();
      formData.append("file", nextTask.file);
      formData.append("upload_id", nextTask.id);
      formData.append("chat_id", nextTask.chatId);
      formData.append("chat_name", nextTask.chatName);
      formData.append("caption", nextTask.caption || "");
      formData.append("filename", nextTask.customFilename || nextTask.filename);
      formData.append("send_as", nextTask.sendAs || "auto");
      const xhr = new XMLHttpRequest();
      this.activeXhrs.set(nextTask.id, xhr);
      let lastTime = performance.now();
      let lastLoaded = 0;
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && nextTask.status !== "cancelled" && nextTask.status !== "failed") {
          const now = performance.now();
          const elapsed = (now - lastTime) / 1e3;
          const percent = Math.min(99, e.loaded / e.total * 100);
          nextTask.progress = Math.round(percent * 10) / 10;
          nextTask.uploadedBytes = e.loaded;
          if (elapsed >= 0.25) {
            const delta = e.loaded - lastLoaded;
            nextTask.speed = Math.max(0, delta / elapsed);
            const remaining = e.total - e.loaded;
            nextTask.eta = nextTask.speed > 0 ? remaining / nextTask.speed : 0;
            lastTime = now;
            lastLoaded = e.loaded;
          }
          if (nextTask.status === "queued" || nextTask.status === "preparing") {
            nextTask.status = "streaming";
          }
          this._notify();
        }
      };
      xhr.onload = () => {
        this.activeXhrs.delete(nextTask.id);
        nextTask.isTransferring = false;
        if (xhr.status >= 200 && xhr.status < 300) {
          console.log(`[Upload] File stream accepted by backend for task ${nextTask.id}`);
          nextTask.transferredToServer = true;
          if (nextTask.status === "streaming") {
            nextTask.status = "uploading";
            this._notify();
          }
        } else {
          let errMessage = `Server error (${xhr.status})`;
          try {
            const errObj = JSON.parse(xhr.responseText);
            errMessage = errObj.detail || errMessage;
          } catch (_) {
          }
          nextTask.status = "failed";
          nextTask.error = errMessage;
          this._notify();
        }
        this.isProcessing = false;
        this.activeTask = null;
        setTimeout(() => this.processNext(), 150);
      };
      xhr.onerror = () => {
        this.activeXhrs.delete(nextTask.id);
        nextTask.isTransferring = false;
        if (nextTask.status !== "cancelled") {
          nextTask.status = "failed";
          nextTask.error = "Network connection to backend failed";
          this._notify();
        }
        this.isProcessing = false;
        this.activeTask = null;
        setTimeout(() => this.processNext(), 300);
      };
      xhr.onabort = () => {
        this.activeXhrs.delete(nextTask.id);
        nextTask.isTransferring = false;
        nextTask.status = "cancelled";
        this._notify();
        this.isProcessing = false;
        this.activeTask = null;
        setTimeout(() => this.processNext(), 150);
      };
      xhr.open("POST", "/api/upload", true);
      xhr.send(formData);
    }
    pause(id) {
      const task = this.queue.find((t) => t.id === id);
      if (task) {
        task.status = "paused";
        socketManager.pauseTask(id);
        this._notify();
      }
    }
    resume(id) {
      const task = this.queue.find((t) => t.id === id);
      if (task) {
        task.status = "uploading";
        socketManager.resumeTask(id);
        this._notify();
        this.processNext();
      }
    }
    cancel(id) {
      const task = this.queue.find((t) => t.id === id);
      if (task) {
        const xhr = this.activeXhrs.get(id);
        if (xhr) {
          xhr.abort();
          this.activeXhrs.delete(id);
        }
        socketManager.cancelTask(id);
        task.status = "cancelled";
        revokePreview(id);
        this._notify();
        if (this.activeTask && this.activeTask.id === id) {
          this.isProcessing = false;
          this.activeTask = null;
          setTimeout(() => this.processNext(), 150);
        }
      }
    }
    async pauseAll() {
      try {
        await fetch("/api/upload/batch/pause", { method: "POST" });
      } catch (e) {
        console.warn("Could not pause all tasks:", e);
      }
      this.queue.forEach((task) => {
        if (["uploading", "streaming", "queued", "splitting"].includes(task.status)) {
          task.status = "paused";
        }
      });
      this._notify();
    }
    async resumeAll() {
      try {
        await fetch("/api/upload/batch/resume", { method: "POST" });
      } catch (e) {
        console.warn("Could not resume all tasks:", e);
      }
      this.queue.forEach((task) => {
        if (task.status === "paused") {
          task.status = task.transferredToServer ? "uploading" : "queued";
        }
      });
      this._notify();
      this.processQueue();
    }
    async cancelAll() {
      try {
        await fetch("/api/upload/batch/cancel", { method: "POST" });
      } catch (e) {
        console.warn("Could not cancel all tasks:", e);
      }
      this.activeXhrs.forEach((xhr) => xhr.abort());
      this.activeXhrs.clear();
      this.queue.forEach((task) => {
        if (task.status !== "completed") {
          task.status = "cancelled";
          revokePreview(task.id);
        }
      });
      this.isProcessing = false;
      this._notify();
    }
    async clearCompleted() {
      try {
        await fetch("/api/upload/batch/clear", { method: "POST" });
      } catch (e) {
        console.warn("Could not clear completed tasks on server:", e);
      }
      this.queue = this.queue.filter((t) => !["completed", "cancelled", "failed"].includes(t.status));
      this._notify();
    }
    remove(id) {
      const idx = this.queue.findIndex((t) => t.id === id);
      if (idx !== -1) {
        this.cancel(id);
        revokePreview(id);
        this.queue.splice(idx, 1);
        this._notify();
      }
    }
    handleSocketProgress(data) {
      let task = this.queue.find((t) => t.id === data.id);
      if (!task && data.status !== "completed" && data.status !== "cancelled") {
        task = {
          id: data.id,
          file: null,
          filename: data.filename || "Uploading File",
          customFilename: data.filename || "Uploading File",
          fileSize: data.file_size || 0,
          chatId: data.chat_id || "me",
          chatName: data.chat_name || "Telegram Chat",
          caption: "",
          sendAs: "auto",
          status: data.status,
          progress: data.progress || 0,
          uploadedBytes: data.uploaded_bytes || 0,
          speed: data.speed || 0,
          eta: data.eta || 0,
          currentPart: data.current_part || 1,
          totalParts: data.total_parts || 1,
          error: data.error,
          preview: null,
          transferredToServer: true,
          isTransferring: false
        };
        this.queue.push(task);
      }
      if (task) {
        task.status = data.status;
        task.progress = data.progress;
        task.uploadedBytes = data.uploaded_bytes;
        task.speed = data.speed;
        task.eta = data.eta;
        task.currentPart = data.current_part || 1;
        task.totalParts = data.total_parts || 1;
        task.error = data.error;
        task.transferredToServer = true;
        if (data.status === "completed" || data.status === "failed") {
          revokePreview(task.id);
        }
        this._notify();
      }
    }
    syncWithSnapshot(tasks) {
      if (!Array.isArray(tasks)) return;
      tasks.forEach((srvTask) => {
        const existing = this.queue.find((t) => t.id === srvTask.id);
        if (existing) {
          if (srvTask.status !== "queued" || !existing.transferredToServer) {
            existing.status = srvTask.status;
          }
          existing.progress = srvTask.progress;
          existing.uploadedBytes = srvTask.uploaded_bytes;
          existing.speed = srvTask.speed;
          existing.eta = srvTask.eta;
          existing.currentPart = srvTask.current_part;
          existing.totalParts = srvTask.total_parts;
          existing.error = srvTask.error;
          existing.transferredToServer = true;
        } else if (srvTask.status !== "completed" && srvTask.status !== "cancelled") {
          const rehydratedTask = {
            id: srvTask.id,
            file: null,
            filename: srvTask.filename || "Uploading File",
            customFilename: srvTask.filename || "Uploading File",
            fileSize: srvTask.file_size || 0,
            chatId: srvTask.chat_id || "me",
            chatName: srvTask.chat_name || "Telegram Chat",
            caption: "",
            sendAs: "auto",
            status: srvTask.status,
            progress: srvTask.progress || 0,
            uploadedBytes: srvTask.uploaded_bytes || 0,
            speed: srvTask.speed || 0,
            eta: srvTask.eta || 0,
            currentPart: srvTask.current_part || 1,
            totalParts: srvTask.total_parts || 1,
            error: srvTask.error,
            preview: null,
            transferredToServer: true,
            isTransferring: false
          };
          this.queue.push(rehydratedTask);
        }
      });
      this._notify();
    }
  };
  var uploader = new Uploader();

  // frontend/js/ui.js
  function showToast2(message, type = "info") {
    const container = document.getElementById("toastContainer");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    let icon = "\u2139\uFE0F";
    if (type === "success") icon = "\u2705";
    if (type === "error") icon = "\u274C";
    if (type === "warning") icon = "\u26A0\uFE0F";
    toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateX(100%)";
      toast.style.transition = "all 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 4e3);
  }
  function buildStatusBadgeHtml(task) {
    const isSplitting = task.status === "splitting";
    const isPaused = task.status === "paused";
    const isCompleted = task.status === "completed";
    const isFailed = task.status === "failed";
    const isCancelled = task.status === "cancelled";
    if (isSplitting) {
      return `<span class="badge splitting" title="Auto-splitting large file for 2GB Telegram limit">\u2702\uFE0F SPLITTING (${task.progress ? task.progress.toFixed(0) : 0}%)</span>`;
    } else if (task.status === "streaming") {
      return `<span class="badge uploading" style="background: rgba(0, 206, 201, 0.18); color: var(--accent-secondary); border-color: rgba(0, 206, 201, 0.4);" title="Streaming to local engine buffer">STREAMING (${task.progress ? task.progress.toFixed(0) : 0}%)</span>`;
    } else if (task.status === "preparing") {
      return `<span class="badge uploading" title="Connecting to Telegram MTProto">PREPARING...</span>`;
    } else if (task.status === "uploading") {
      const partInfo = task.totalParts > 1 ? ` (${task.currentPart}/${task.totalParts})` : "";
      return `<span class="badge uploading" title="Uploading to Telegram MTProto">UPLOADING${partInfo}</span>`;
    } else if (isPaused) {
      return `<span class="badge" style="background: rgba(253, 203, 110, 0.2); color: var(--status-warning);">PAUSED</span>`;
    } else if (isCompleted) {
      return `<span class="badge completed">COMPLETED</span>`;
    } else if (isFailed) {
      const errTooltip = escapeHtml(task.error || "Upload error occurred");
      return `<span class="badge failed" title="${errTooltip}">FAILED</span>`;
    } else if (isCancelled) {
      return `<span class="badge" style="background: rgba(255,255,255,0.1); color: var(--text-dim);">CANCELLED</span>`;
    }
    return `<span class="badge" style="background: rgba(255,255,255,0.08); color: var(--text-muted);">QUEUED</span>`;
  }
  function buildStageLineHtml(task) {
    if (task.status === "splitting") {
      return `<div class="card-stage-line splitting">\u2702\uFE0F <strong>Stage 1 of 2:</strong> Splitting into 1.9GB sequence parts (${task.progress ? task.progress.toFixed(1) : 0}%) \u2022 <em>Preparing Telegram slices</em></div>`;
    } else if (task.totalParts > 1 && task.status === "uploading") {
      return `<div class="card-stage-line uploading">\u{1F680} <strong>Stage 2 of 2:</strong> Uploading Part ${task.currentPart} of ${task.totalParts} to Telegram Cloud</div>`;
    } else if (task.status === "streaming") {
      return `<div class="card-stage-line streaming">\u26A1 <strong>Buffering Stream:</strong> Streaming file to local engine (${task.progress ? task.progress.toFixed(1) : 0}%)</div>`;
    } else if (task.status === "uploading") {
      return `<div class="card-stage-line uploading">\u{1F680} <strong>Turbo MTProto Upload:</strong> Streaming to Telegram (6 Workers)</div>`;
    } else if (task.status === "completed") {
      return `<div class="card-stage-line completed">\u2705 <strong>Upload Finished:</strong> Delivered to ${escapeHtml(task.chatName || "Telegram")}</div>`;
    } else if (task.status === "paused") {
      return `<div class="card-stage-line">\u23F8\uFE0F <strong>Upload Paused:</strong> Resume anytime without losing progress</div>`;
    } else if (task.status === "failed") {
      return `<div class="card-stage-line" style="color: var(--status-danger);">\u274C <strong>Error:</strong> ${escapeHtml(task.error || "Failed to upload")}</div>`;
    }
    return `<div class="card-stage-line">\u23F3 <strong>Queued:</strong> Waiting for worker slot...</div>`;
  }
  function buildActionButtonsHtml(task) {
    const isUploading = task.status === "uploading" || task.status === "streaming" || task.status === "preparing" || task.status === "splitting";
    const isPaused = task.status === "paused";
    const isQueued = task.status === "queued";
    if (isUploading) {
      return `
      <button class="icon-btn" title="Pause Upload" onclick="window._app.pause('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>
      </button>
      <button class="icon-btn danger" title="Cancel Upload" onclick="window._app.cancel('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
    } else if (isPaused) {
      return `
      <button class="icon-btn" title="Resume Upload" onclick="window._app.resume('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
      </button>
      <button class="icon-btn danger" title="Cancel" onclick="window._app.cancel('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
    } else if (isQueued) {
      return `
      <button class="icon-btn danger" title="Remove from Queue" onclick="window._app.remove('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
      </button>
    `;
    } else {
      return `
      <button class="icon-btn" title="Dismiss" onclick="window._app.remove('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
    }
  }
  function renderQueue(queue) {
    const container = document.getElementById("queueListContainer");
    const countBadge = document.getElementById("queueCountBadge");
    const emptyState = document.getElementById("queueEmptyState");
    const workspace = document.getElementById("workspaceLayout");
    if (countBadge) {
      countBadge.textContent = queue.length;
      countBadge.classList.toggle("has-items", queue.length > 0);
    }
    if (workspace) {
      if (queue.length > 0) {
        workspace.classList.add("active-split");
      } else {
        workspace.classList.remove("active-split");
      }
    }
    if (!container) return;
    if (queue.length === 0) {
      if (emptyState) emptyState.style.display = "block";
      container.innerHTML = "";
      return;
    }
    if (emptyState) emptyState.style.display = "none";
    const existingCardIds = /* @__PURE__ */ new Set();
    queue.forEach((task) => {
      existingCardIds.add(task.id);
      let card = document.getElementById(`card_${task.id}`);
      const isSplitting = task.status === "splitting";
      const isUploading = task.status === "uploading" || task.status === "streaming" || task.status === "preparing";
      if (!card) {
        card = document.createElement("div");
        card.className = "queue-card";
        card.id = `card_${task.id}`;
        let thumbHtml = "";
        if (task.preview && task.preview.url) {
          thumbHtml = `<img src="${task.preview.url}" alt="Preview" />`;
        } else {
          thumbHtml = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
          </svg>
        `;
        }
        card.innerHTML = `
        <div class="card-thumbnail">${thumbHtml}</div>
        <div class="card-details">
          <div class="card-filename-row">
            <input 
              type="text" 
              class="editable-filename" 
              value="${escapeHtml(task.customFilename || task.filename)}" 
              title="Click to rename"
              ${task.status !== "queued" ? "readonly" : ""}
              onchange="window._app.updateFilename('${task.id}', this.value)"
            />
            <div class="badge-slot">${buildStatusBadgeHtml(task)}</div>
          </div>
          <div class="stage-slot">${buildStageLineHtml(task)}</div>
          <div class="card-meta-row">
            <span class="meta-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
              ${formatBytes(task.fileSize)}
            </span>
            <span class="meta-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
              ${escapeHtml(task.chatName || "Telegram Chat")}
            </span>
          </div>
          <div class="progress-container">
            <div class="progress-track">
              <div 
                class="progress-bar-fill ${isSplitting ? "splitting" : isUploading ? "uploading" : ""}" 
                style="width: ${Math.min(100, task.progress || 0)}%;"
              ></div>
            </div>
            <div class="progress-metrics-row">
              <div class="metrics-left"></div>
              <div class="metrics-right">${formatBytes(task.uploadedBytes || 0)} / ${formatBytes(task.fileSize)}</div>
            </div>
          </div>
        </div>
        <div class="card-actions">${buildActionButtonsHtml(task)}</div>
      `;
        container.appendChild(card);
      }
      card.className = `queue-card status-${task.status}`;
      const badgeSlot = card.querySelector(".badge-slot");
      if (badgeSlot) badgeSlot.innerHTML = buildStatusBadgeHtml(task);
      const stageSlot = card.querySelector(".stage-slot");
      if (stageSlot) stageSlot.innerHTML = buildStageLineHtml(task);
      const barFill = card.querySelector(".progress-bar-fill");
      if (barFill) {
        barFill.style.width = `${Math.min(100, task.progress || 0)}%`;
        barFill.className = `progress-bar-fill ${isSplitting ? "splitting" : isUploading ? "uploading" : ""}`;
      }
      const metricsLeft = card.querySelector(".metrics-left");
      if (metricsLeft) {
        if (isSplitting) {
          metricsLeft.innerHTML = `<span>\u2702\uFE0F Splitting: ${task.progress ? task.progress.toFixed(1) : 0}%</span><span>\u{1F4E6} 1.9GB Part Buffer</span>`;
        } else if (isUploading) {
          metricsLeft.innerHTML = `<span>${task.progress ? task.progress.toFixed(1) : 0}%</span><span>\u26A1 ${formatSpeed(task.speed)}</span><span>\u23F3 ETA: ${formatETA(task.eta)}</span>`;
        } else {
          metricsLeft.innerHTML = `<span>${task.progress ? task.progress.toFixed(1) : 0}%</span>`;
        }
      }
      const metricsRight = card.querySelector(".metrics-right");
      if (metricsRight) {
        metricsRight.textContent = `${formatBytes(task.uploadedBytes || 0)} / ${formatBytes(task.fileSize)}`;
      }
      const actionsSlot = card.querySelector(".card-actions");
      if (actionsSlot) {
        actionsSlot.innerHTML = buildActionButtonsHtml(task);
      }
    });
    Array.from(container.children).forEach((child) => {
      const id = child.id.replace("card_", "");
      if (!existingCardIds.has(id)) {
        child.remove();
      }
    });
  }
  var _cachedHistory = [];
  var _historyFilterStatus = "all";
  var _historySearchQuery = "";
  function initHistoryControls() {
    const searchInput = document.getElementById("historySearchInput");
    const statusFilter = document.getElementById("historyStatusFilter");
    const btnExportCSV = document.getElementById("btnExportCSV");
    const btnExportJSON = document.getElementById("btnExportJSON");
    const btnClearHistory = document.getElementById("btnClearHistoryBtn");
    const btnRefreshHistory = document.getElementById("btnRefreshHistory");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        _historySearchQuery = (e.target.value || "").toLowerCase().trim();
        renderFilteredHistory();
      });
    }
    if (statusFilter) {
      statusFilter.addEventListener("change", (e) => {
        _historyFilterStatus = e.target.value;
        renderFilteredHistory();
      });
    }
    if (btnExportCSV) {
      btnExportCSV.addEventListener("click", () => exportHistoryCSV());
    }
    if (btnExportJSON) {
      btnExportJSON.addEventListener("click", () => exportHistoryJSON());
    }
    if (btnClearHistory) {
      btnClearHistory.addEventListener("click", () => {
        if (window._app && window._app.clearHistory) {
          window._app.clearHistory();
        }
      });
    }
    if (btnRefreshHistory) {
      btnRefreshHistory.addEventListener("click", () => {
        loadHistory();
        showToast2("History refreshed", "info");
      });
    }
  }
  function renderFilteredHistory() {
    const fullContainer = document.getElementById("fullHistoryContainer");
    const badge = document.getElementById("historyTotalBadge");
    if (!fullContainer) return;
    let filtered = _cachedHistory;
    if (_historyFilterStatus !== "all") {
      filtered = filtered.filter((item) => item.status === _historyFilterStatus);
    }
    if (_historySearchQuery) {
      filtered = filtered.filter((item) => {
        const fn = (item.filename || "").toLowerCase();
        const cn = (item.chat_name || "").toLowerCase();
        const cid = String(item.chat_id || "").toLowerCase();
        const dt = (item.created_at || "").toLowerCase();
        return fn.includes(_historySearchQuery) || cn.includes(_historySearchQuery) || cid.includes(_historySearchQuery) || dt.includes(_historySearchQuery);
      });
    }
    if (badge) {
      badge.textContent = filtered.length;
    }
    if (filtered.length === 0) {
      fullContainer.innerHTML = `
      <div class="history-empty">
        <p>No matching transfers found.</p>
      </div>
    `;
      return;
    }
    fullContainer.innerHTML = filtered.map((item) => {
      const isOk = item.status === "completed";
      const isFail = item.status === "failed";
      const partsBadge = item.parts_count > 1 ? `<span class="history-parts-tag">\u{1F4E6} ${item.parts_count} Parts</span>` : "";
      let statusLabel = "\u26A1 UPLOADING";
      if (isOk) statusLabel = "\u2713 COMPLETED";
      else if (isFail) statusLabel = "\u2715 FAILED";
      return `
      <div class="history-item ${isOk ? "status-ok" : isFail ? "status-err" : "status-pending"}">
        <div class="history-item-top">
          <div class="history-file-info">
            <span class="history-file-icon">\u{1F4C4}</span>
            <span class="history-filename" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
          </div>
          <span class="badge ${isOk ? "completed" : isFail ? "failed" : "uploading"}">
            ${statusLabel}
          </span>
        </div>
        <div class="history-item-sub">
          <span class="history-sub-meta">\u{1F4BE} ${formatBytes(item.file_size)}</span>
          <span class="history-dot">\u2022</span>
          <span class="history-sub-meta" title="${escapeHtml(item.chat_name || item.chat_id)}">\u{1F4AC} ${escapeHtml(item.chat_name || item.chat_id)}</span>
          ${partsBadge ? `<span class="history-dot">\u2022</span>` + partsBadge : ""}
          <span class="history-dot">\u2022</span>
          <span class="history-date">${escapeHtml(item.created_at || "")}</span>
        </div>
      </div>
    `;
    }).join("");
  }
  function exportHistoryCSV() {
    if (_cachedHistory.length === 0) {
      showToast2("No history records to export", "warning");
      return;
    }
    const headers = ["ID", "Filename", "File Size (Bytes)", "Chat ID", "Chat Name", "Status", "Parts Count", "Created At"];
    const rows = _cachedHistory.map((item) => [
      `"${item.id || ""}"`,
      `"${(item.filename || "").replace(/"/g, '""')}"`,
      item.file_size || 0,
      `"${item.chat_id || ""}"`,
      `"${(item.chat_name || "").replace(/"/g, '""')}"`,
      `"${item.status || ""}"`,
      item.parts_count || 1,
      `"${item.created_at || ""}"`
    ]);
    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `tg_power_suite_history_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast2("Transfer history exported as CSV \u2713", "success");
  }
  function exportHistoryJSON() {
    if (_cachedHistory.length === 0) {
      showToast2("No history records to export", "warning");
      return;
    }
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(_cachedHistory, null, 2));
    const link = document.createElement("a");
    link.setAttribute("href", dataStr);
    link.setAttribute("download", `tg_power_suite_history_${Date.now()}.json`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast2("Transfer history exported as JSON \u2713", "success");
  }
  async function loadHistory() {
    const container = document.getElementById("historyListContainer");
    const fullContainer = document.getElementById("fullHistoryContainer");
    const badge = document.getElementById("historyTotalBadge");
    if (!container && !fullContainer) return;
    try {
      const res = await fetch("/api/history");
      if (!res.ok) return;
      const history2 = await res.json();
      _cachedHistory = history2 || [];
      if (badge) {
        badge.textContent = _cachedHistory.length;
      }
      renderFilteredHistory();
      if (container) {
        if (_cachedHistory.length === 0) {
          container.innerHTML = `<div class="history-empty"><p>No uploads recorded yet.</p></div>`;
        } else {
          container.innerHTML = _cachedHistory.slice(0, 5).map((item) => {
            const isOk = item.status === "completed";
            return `
            <div class="history-item ${isOk ? "status-ok" : "status-err"}">
              <div class="history-item-top">
                <div class="history-file-info">
                  <span class="history-file-icon">\u{1F4C4}</span>
                  <span class="history-filename" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
                </div>
                <span class="badge ${isOk ? "completed" : "failed"}">
                  ${isOk ? "\u2713 SENT" : "\u2715 FAILED"}
                </span>
              </div>
              <div class="history-item-sub">
                <span class="history-sub-meta">\u{1F4BE} ${formatBytes(item.file_size)}</span>
                <span class="history-dot">\u2022</span>
                <span class="history-date">${escapeHtml(item.created_at || "")}</span>
              </div>
            </div>
          `;
          }).join("");
        }
      }
    } catch (e) {
      console.error("Error loading history:", e);
    }
  }

  // frontend/js/network-watchdog.js
  var NetworkWatchdog = class {
    constructor() {
      this.isOnline = navigator.onLine !== false;
      this.wasInterrupted = false;
      this.interruptedTasks = /* @__PURE__ */ new Set();
      this.heartbeatTimer = null;
      this.bannerElem = null;
      this.consecutiveFailures = 0;
    }
    init() {
      this._createBanner();
      this._setupListeners();
      this._startHeartbeat();
      console.log("Network Watchdog & Auto-Recovery initialized.");
    }
    _createBanner() {
      let banner = document.getElementById("networkWatchdogBanner");
      if (!banner) {
        banner = document.createElement("div");
        banner.id = "networkWatchdogBanner";
        banner.className = "watchdog-banner hidden";
        document.body.prepend(banner);
      }
      this.bannerElem = banner;
    }
    _setupListeners() {
      window.addEventListener("online", () => {
        console.log("[NetworkWatchdog] Browser online event fired.");
        this._handleOnline();
      });
      window.addEventListener("offline", () => {
        console.warn("[NetworkWatchdog] Browser offline event fired.");
        this._handleOffline();
      });
    }
    _startHeartbeat() {
      if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = setInterval(async () => {
        try {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 3500);
          const res = await fetch("/api/auth/status", {
            method: "GET",
            signal: controller.signal,
            cache: "no-store"
          });
          clearTimeout(timeoutId);
          if (res.ok) {
            if (!this.isOnline) {
              this._handleOnline();
            }
            this.consecutiveFailures = 0;
          } else {
            this.consecutiveFailures++;
            if (this.consecutiveFailures >= 2 && this.isOnline) {
              this._handleOffline("Backend Server Unreachable");
            }
          }
        } catch (e) {
          this.consecutiveFailures++;
          if (this.consecutiveFailures >= 2 && this.isOnline) {
            this._handleOffline("Connection Interrupted");
          }
        }
      }, 4e3);
    }
    _handleOffline(reason = "Network Offline") {
      this.isOnline = false;
      this.wasInterrupted = true;
      uploader.queue.forEach((task) => {
        if (task.status === "uploading" || task.status === "streaming" || task.status === "splitting") {
          this.interruptedTasks.add(task.id);
          uploader.pause(task.id);
        }
      });
      this._showBanner(`\u26A0\uFE0F ${reason} \u2014 Pausing active transfers safely. Waiting for reconnection...`, "offline");
      showToast2("Network connection lost. Uploads paused safely.", "warning");
    }
    _handleOnline() {
      this.isOnline = true;
      this.consecutiveFailures = 0;
      if (this.wasInterrupted) {
        this._showBanner("\u26A1 Connection Restored \u2014 Auto-resuming upload queue...", "online");
        showToast2("Back online! Auto-resuming upload queue...", "success");
        socketManager.init();
        setTimeout(() => {
          if (this.interruptedTasks.size > 0) {
            this.interruptedTasks.forEach((taskId) => {
              uploader.resume(taskId);
            });
            this.interruptedTasks.clear();
          } else {
            uploader.processNext();
          }
          this.wasInterrupted = false;
          setTimeout(() => {
            this._hideBanner();
          }, 2500);
        }, 1e3);
      } else {
        this._hideBanner();
      }
    }
    _showBanner(text, type) {
      if (!this.bannerElem) return;
      this.bannerElem.textContent = text;
      this.bannerElem.className = `watchdog-banner visible ${type}`;
    }
    _hideBanner() {
      if (!this.bannerElem) return;
      this.bannerElem.className = "watchdog-banner hidden";
    }
  };
  var networkWatchdog = new NetworkWatchdog();

  // frontend/js/theme.js
  var THEMES = {
    "deep-space": {
      "--bg-primary": "#0f111a",
      "--bg-secondary": "#161926",
      "--bg-card": "rgba(26, 31, 46, 0.65)",
      "--bg-card-hover": "rgba(34, 40, 60, 0.85)",
      "--accent-primary": "#6c5ce7",
      "--accent-secondary": "#00cec9",
      "--accent-gradient": "linear-gradient(135deg, #6c5ce7 0%, #a29bfe 100%)",
      "--accent-gradient-hover": "linear-gradient(135deg, #5b4cc4 0%, #8c82f8 100%)",
      "--border-glass": "rgba(255, 255, 255, 0.08)"
    },
    "cyberpunk": {
      "--bg-primary": "#080811",
      "--bg-secondary": "#121124",
      "--bg-card": "rgba(20, 16, 38, 0.75)",
      "--bg-card-hover": "rgba(32, 24, 60, 0.9)",
      "--accent-primary": "#ff007f",
      "--accent-secondary": "#00f0ff",
      "--accent-gradient": "linear-gradient(135deg, #ff007f 0%, #00f0ff 100%)",
      "--accent-gradient-hover": "linear-gradient(135deg, #e60072 0%, #00d4e0 100%)",
      "--border-glass": "rgba(255, 0, 127, 0.2)"
    },
    "nord": {
      "--bg-primary": "#242933",
      "--bg-secondary": "#2e3440",
      "--bg-card": "rgba(46, 52, 64, 0.7)",
      "--bg-card-hover": "rgba(59, 66, 82, 0.85)",
      "--accent-primary": "#88c0d0",
      "--accent-secondary": "#81a1c1",
      "--accent-gradient": "linear-gradient(135deg, #88c0d0 0%, #5e81ac 100%)",
      "--accent-gradient-hover": "linear-gradient(135deg, #78b0c0 0%, #4e719c 100%)",
      "--border-glass": "rgba(255, 255, 255, 0.1)"
    },
    "oled": {
      "--bg-primary": "#000000",
      "--bg-secondary": "#080808",
      "--bg-card": "rgba(14, 14, 14, 0.85)",
      "--bg-card-hover": "rgba(22, 22, 22, 0.95)",
      "--accent-primary": "#10b981",
      "--accent-secondary": "#059669",
      "--accent-gradient": "linear-gradient(135deg, #10b981 0%, #059669 100%)",
      "--accent-gradient-hover": "linear-gradient(135deg, #0ea271 0%, #047857 100%)",
      "--border-glass": "rgba(255, 255, 255, 0.09)"
    },
    "sunset": {
      "--bg-primary": "#140e1b",
      "--bg-secondary": "#1e1428",
      "--bg-card": "rgba(35, 22, 48, 0.7)",
      "--bg-card-hover": "rgba(48, 30, 66, 0.85)",
      "--accent-primary": "#fd79a8",
      "--accent-secondary": "#e17055",
      "--accent-gradient": "linear-gradient(135deg, #fd79a8 0%, #e17055 100%)",
      "--accent-gradient-hover": "linear-gradient(135deg, #e86a98 0%, #cf634a 100%)",
      "--border-glass": "rgba(253, 121, 168, 0.18)"
    }
  };
  var ThemeManager = class {
    constructor() {
      this.currentTheme = localStorage.getItem("tg_theme_preset") || "deep-space";
      this.glassIntensity = parseInt(localStorage.getItem("tg_glass_intensity") || "16", 10);
    }
    init() {
      this.applyTheme(this.currentTheme);
      this.applyGlassIntensity(this.glassIntensity);
      this._setupUI();
    }
    applyTheme(themeKey) {
      if (!THEMES[themeKey]) themeKey = "deep-space";
      this.currentTheme = themeKey;
      localStorage.setItem("tg_theme_preset", themeKey);
      const root = document.documentElement;
      const colors = THEMES[themeKey];
      for (const [prop, val] of Object.entries(colors)) {
        root.style.setProperty(prop, val);
      }
      document.querySelectorAll(".preset-card").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.theme === themeKey);
      });
    }
    applyGlassIntensity(blurPx) {
      this.glassIntensity = blurPx;
      localStorage.setItem("tg_glass_intensity", blurPx);
      document.documentElement.style.setProperty("--glass-blur", `${blurPx}px`);
      const valElem = document.getElementById("glassIntensityValue");
      if (valElem) {
        valElem.textContent = `${blurPx}px`;
      }
      const slider = document.getElementById("glassIntensitySlider");
      if (slider) {
        slider.value = blurPx;
      }
    }
    _setupUI() {
      const btnOpen = document.getElementById("btnThemeCustomizer") || document.getElementById("btnThemeToggle") || document.querySelector(".btn-theme-customizer") || document.querySelector(".theme-toggle-btn");
      const backdrop = document.getElementById("themeModalBackdrop");
      const btnClose = document.getElementById("btnCloseThemeModal");
      const slider = document.getElementById("glassIntensitySlider");
      if (btnOpen && backdrop) {
        btnOpen.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          backdrop.classList.add("visible");
        });
      }
      document.querySelectorAll("#btnThemeCustomizer, #btnThemeToggle, .btn-theme-customizer, .theme-toggle-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (backdrop) backdrop.classList.add("visible");
        });
      });
      if (btnClose && backdrop) {
        btnClose.addEventListener("click", (e) => {
          e.preventDefault();
          backdrop.classList.remove("visible");
        });
      }
      if (backdrop) {
        backdrop.addEventListener("click", (e) => {
          if (e.target === backdrop) {
            backdrop.classList.remove("visible");
          }
        });
      }
      document.querySelectorAll(".preset-card").forEach((btn) => {
        btn.addEventListener("click", () => {
          const theme = btn.dataset.theme;
          this.applyTheme(theme);
        });
      });
      if (slider) {
        slider.value = this.glassIntensity;
        slider.addEventListener("input", (e) => {
          this.applyGlassIntensity(parseInt(e.target.value, 10));
        });
      }
    }
  };
  var themeManager = new ThemeManager();

  // frontend/js/tabs.js
  var TabController = class {
    constructor() {
      this.tabButtons = [];
      this.tabPanes = {};
      this.currentTab = "uploader";
      this.listeners = /* @__PURE__ */ new Map();
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
      this.tabButtons.forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          const tab = btn.dataset.tab;
          if (tab) {
            this.switchTab(tab);
          }
        });
      });
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
      } catch (e) {
      }
      const buttons = Array.from(document.querySelectorAll(".header-nav-tabs .tab-btn"));
      buttons.forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
      });
      document.querySelectorAll(".tab-pane").forEach((pane) => {
        pane.classList.remove("active");
      });
      targetPane.classList.add("active");
      const callbacks = this.listeners.get(tabName) || [];
      callbacks.forEach((cb) => {
        try {
          cb();
        } catch (err) {
          console.error("Tab callback error:", err);
        }
      });
    }
  };
  var tabController = new TabController();
  window._switchTab = (tabName) => tabController.switchTab(tabName);

  // frontend/js/sniffer-ui.js
  var SnifferUI = class {
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
      this.activeChannels = /* @__PURE__ */ new Set();
      this.currentFilter = "all";
    }
    init(socket, tabController2) {
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
      if (this.watchModalFilterTabs) {
        this.watchModalFilterTabs.querySelectorAll(".filter-chip").forEach((chip) => {
          chip.addEventListener("click", () => {
            this.watchModalFilterTabs.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("active"));
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
      if (this.btnToggleManualChannel && this.formAddChannel) {
        this.btnToggleManualChannel.addEventListener("click", () => {
          const isHidden = this.formAddChannel.style.display === "none";
          this.formAddChannel.style.display = isHidden ? "flex" : "none";
          this.btnToggleManualChannel.textContent = isHidden ? "\u25B2 Hide manual custom input" : "\u270F\uFE0F Or enter custom Channel ID / @username";
          if (isHidden && this.inputChannelId) {
            this.inputChannelId.focus();
          }
        });
      }
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
      if (this.btnGoToSettings && tabController2) {
        this.btnGoToSettings.addEventListener("click", () => {
          tabController2.switchTab("settings");
        });
      }
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
        this.cachedChats = (chats || []).filter((c) => c.type !== "saved_messages");
        this.updateFilterBadgeCounts();
        this.renderModalList(this.watchChannelSearchInput ? this.watchChannelSearchInput.value : "");
      } catch (err) {
        console.debug("Could not load account dialogs:", err);
      }
    }
    updateFilterBadgeCounts() {
      if (!this.watchModalFilterTabs) return;
      const channels = this.cachedChats.filter((c) => c.type === "channel").length;
      const groups = this.cachedChats.filter((c) => c.type === "group" || c.type === "supergroup").length;
      const bots = this.cachedChats.filter((c) => c.type === "bot").length;
      const users = this.cachedChats.filter((c) => c.type === "user").length;
      const all = this.cachedChats.length;
      const chips = this.watchModalFilterTabs.querySelectorAll(".filter-chip");
      chips.forEach((chip) => {
        const f = chip.dataset.filter;
        if (f === "all") chip.textContent = `All (${all})`;
        if (f === "channel") chip.textContent = `\u{1F4E2} Channels (${channels})`;
        if (f === "group") chip.textContent = `\u{1F465} Groups (${groups})`;
        if (f === "bot") chip.textContent = `\u{1F916} Bots (${bots})`;
        if (f === "user") chip.textContent = `\u{1F464} Contacts (${users})`;
      });
    }
    renderModalList(searchQuery = "") {
      if (!this.watchChannelListContainer) return;
      const query = (searchQuery || "").toLowerCase().trim();
      let filtered = this.cachedChats;
      if (this.currentFilter === "channel") {
        filtered = filtered.filter((c) => c.type === "channel");
      } else if (this.currentFilter === "group") {
        filtered = filtered.filter((c) => c.type === "group" || c.type === "supergroup");
      } else if (this.currentFilter === "bot") {
        filtered = filtered.filter((c) => c.type === "bot");
      } else if (this.currentFilter === "user") {
        filtered = filtered.filter((c) => c.type === "user");
      }
      if (query) {
        filtered = filtered.filter((c) => {
          const name = (c.name || "").toLowerCase();
          const username = (c.username || "").toLowerCase();
          const id = String(c.id);
          return name.includes(query) || username.includes(query) || id.includes(query);
        });
      }
      if (filtered.length === 0) {
        this.watchChannelListContainer.innerHTML = `
        <div style="text-align: center; padding: 40px 16px; color: var(--text-muted);">
          <div style="font-size: 2rem; opacity: 0.5; margin-bottom: 8px;">\u{1F50D}</div>
          <p style="font-size: 0.9rem;">No matching dialogs found.</p>
        </div>
      `;
        return;
      }
      const displayList = filtered.slice(0, 100);
      this.watchChannelListContainer.innerHTML = displayList.map((ch) => {
        let icon = "\u{1F4E2}";
        let typeLabel = "CHANNEL";
        let avatarBg = "linear-gradient(135deg, #00cec9, #6c5ce7)";
        if (ch.type === "supergroup" || ch.type === "group") {
          icon = "\u{1F465}";
          typeLabel = "GROUP";
          avatarBg = "linear-gradient(135deg, #fd79a8, #6c5ce7)";
        } else if (ch.type === "bot") {
          icon = "\u{1F916}";
          typeLabel = "BOT";
          avatarBg = "linear-gradient(135deg, #00b894, #0984e3)";
        } else if (ch.type === "user") {
          icon = "\u{1F464}";
          typeLabel = "CONTACT";
          avatarBg = "linear-gradient(135deg, #fdcb6e, #e17055)";
        }
        const identifier = ch.username ? `@${ch.username}` : String(ch.id).startsWith("-") ? String(ch.id) : ch.type === "channel" || ch.type === "supergroup" ? `-100${ch.id}` : String(ch.id);
        let isWatched = false;
        for (const ac of this.activeChannels) {
          const strAc = String(ac).toLowerCase().replace(/^@/, "");
          const cleanId = String(identifier).toLowerCase().replace(/^@/, "");
          if (strAc === cleanId || String(ac) === String(ch.id) || String(ac) === `-100${ch.id}`) {
            isWatched = true;
            break;
          }
        }
        const handle = ch.username ? `@${ch.username}` : `ID: ${ch.id}`;
        return `
        <div class="chat-option-item ${isWatched ? "selected" : ""}" data-identifier="${escapeHtml(identifier)}" style="display: flex; align-items: center; justify-content: space-between; padding: 12px 16px;">
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
          
          <button class="btn-toggle-watch ${isWatched ? "btn-secondary" : "btn-primary"}" 
                  data-identifier="${escapeHtml(identifier)}" 
                  data-watched="${isWatched ? "true" : "false"}"
                  type="button" 
                  style="padding: 6px 14px; font-size: 0.78rem; border-radius: var(--radius-full); white-space: nowrap; flex-shrink: 0;">
            ${isWatched ? "\u2713 Watched" : "+ Watch"}
          </button>
        </div>
      `;
      }).join("");
      this.watchChannelListContainer.querySelectorAll(".btn-toggle-watch").forEach((btn) => {
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
            btn.textContent = "\u2713 Watched";
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
          showToast(`Watched channel '${val}' added \u2713`, "success");
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
      const cleanHandle = raw.toLowerCase().replace(/^@/, "");
      const cleanId = raw.replace(/^-100/, "");
      const found = this.cachedChats.find((c) => {
        const cUser = (c.username || "").toLowerCase();
        const cId = String(c.id).replace(/^-100/, "");
        return cUser && cUser === cleanHandle || cId === cleanId || String(c.id) === raw;
      });
      if (found) {
        let icon2 = "\u{1F4E2}";
        if (found.type === "supergroup" || found.type === "group") icon2 = "\u{1F465}";
        else if (found.type === "bot") icon2 = "\u{1F916}";
        else if (found.type === "user") icon2 = "\u{1F464}";
        return {
          icon: icon2,
          name: found.name || raw,
          handle: found.username ? `@${found.username}` : `ID: ${found.id}`,
          raw
        };
      }
      let icon = "\u{1F4E1}";
      if (raw.toLowerCase().endsWith("bot")) icon = "\u{1F916}";
      else if (raw.startsWith("@")) icon = "\u{1F4E2}";
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
          this.watchedChannelsList.innerHTML = channels.map((ch) => {
            const info = this._resolveChannelDisplay(ch);
            return `
            <div class="channel-chip" title="${escapeHtml(info.raw)} (${escapeHtml(info.handle)})">
              <span class="chip-icon">${info.icon}</span>
              <span class="chip-name">${escapeHtml(info.name)}</span>
              <button class="channel-chip-remove" title="Stop watching" data-channel="${escapeHtml(ch)}">&times;</button>
            </div>
          `;
          }).join("");
          this.watchedChannelsList.querySelectorAll(".channel-chip-remove").forEach((btn) => {
            btn.addEventListener("click", (e) => {
              const ch = e.currentTarget.dataset.channel;
              if (ch) this.handleRemoveChannel(ch);
            });
          });
        }
      }
      if (this.watchChannelModal && this.watchChannelModal.classList.contains("open")) {
        this.renderModalList(this.watchChannelSearchInput ? this.watchChannelSearchInput.value : "");
      }
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
          <div style="font-size: 2.5rem; margin-bottom: 12px; opacity: 0.5;">\u{1F4E1}</div>
          <p style="font-weight: 500; font-size: 1.05rem; color: var(--text-main); margin-bottom: 6px;">Sniffer Waiting for Media</p>
          <p style="font-size: 0.85rem;">New media posted to watched channels will automatically appear here and trigger your download manager.</p>
        </div>
      `;
        return;
      }
      this.snifferFeedContainer.innerHTML = items.map((item) => this._renderFeedCardHtml(item)).join("");
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
            <span class="${item.status === "dispatched" ? "badge-status-online" : "badge-status-offline"}">${escapeHtml(item.status.toUpperCase())}</span>
          </div>
        </div>
        <div class="feed-actions">
          <button class="btn-secondary btn-copy-link" style="padding: 6px 10px; font-size: 0.75rem;" type="button">
            \u{1F4CB} Copy Link
          </button>
          <button class="btn-primary btn-trigger-fdm" style="padding: 6px 12px; font-size: 0.75rem;" type="button">
            \u{1F680} Send to FDM
          </button>
        </div>
      </div>
    `;
    }
    _attachFeedCardListeners(scope = this.snifferFeedContainer) {
      scope.querySelectorAll(".btn-copy-link").forEach((btn) => {
        btn.onclick = (e) => {
          const card = e.target.closest(".feed-card");
          const url = card.dataset.url;
          if (url) {
            navigator.clipboard.writeText(url);
            showToast("Proxy download link copied!", "success");
          }
        };
      });
      scope.querySelectorAll(".btn-trigger-fdm").forEach((btn) => {
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
  };
  var snifferUI = new SnifferUI();

  // frontend/js/settings-ui.js
  var SettingsUI = class {
    constructor() {
      this.form = null;
      this.btnSave = null;
      this.fields = {};
    }
    init(tabController2) {
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
            new Notification("\u{1F514} TG Power Suite Alert", {
              body: "Desktop notification alerts are working perfectly!",
              icon: "/assets/favicon.ico"
            });
            showToast("Sample notification sent to desktop!", "success");
          } else {
            const perm = await Notification.requestPermission();
            if (perm === "granted") {
              new Notification("\u{1F514} TG Power Suite Alert", {
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
      if (tabController2) {
        tabController2.onTabSwitch("settings", () => {
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
            if (element && config[key] !== void 0) {
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
  };
  var settingsUI = new SettingsUI();

  // frontend/js/telemetry.js
  var TelemetryController = class {
    constructor() {
      this.canvas = null;
      this.ctx = null;
      this.telemUploadSpeed = null;
      this.telemProxyStreams = null;
      this.telemRam = null;
      this.telemCpu = null;
      this.telemUptime = null;
      this.historyData = new Array(30).fill(0);
      this.maxSpeedSeen = 1024 * 1024;
      this.isRendering = false;
    }
    init(socket) {
      this.canvas = document.getElementById("telemetryCanvas");
      if (this.canvas) {
        this.ctx = this.canvas.getContext("2d");
      }
      this.telemUploadSpeed = document.getElementById("telemUploadSpeed");
      this.telemProxyStreams = document.getElementById("telemProxyStreams");
      this.telemRam = document.getElementById("telemRam");
      this.telemCpu = document.getElementById("telemCpu");
      this.telemUptime = document.getElementById("telemUptime");
      if (socket) {
        socket.on("telemetry:stats", (stats) => {
          this.updateStats(stats);
        });
      }
      this.fetchStats();
      setInterval(() => this.fetchStats(), 4e3);
      this.drawSparkline();
    }
    async fetchStats() {
      try {
        const res = await fetch("/api/system/stats");
        if (res.ok) {
          const stats = await res.json();
          this.updateStats(stats);
        }
      } catch (err) {
      }
    }
    updateStats(stats) {
      if (!stats) return;
      const upSpeedBps = stats.upload_speed_bps || 0;
      if (this.telemUploadSpeed) {
        this.telemUploadSpeed.textContent = upSpeedBps > 0 ? `${formatBytes(upSpeedBps)}/s` : "0.0 KB/s";
      }
      if (this.telemProxyStreams) {
        const count = stats.active_proxy_streams || 0;
        this.telemProxyStreams.textContent = `${count} Active`;
      }
      if (this.telemRam) {
        const appRam = stats.app_ram_mb !== void 0 ? stats.app_ram_mb : stats.ram_used_mb || 0;
        const sysPct = stats.sys_ram_percent || stats.ram_percent || 0;
        this.telemRam.textContent = `${appRam} MB`;
        this.telemRam.title = `App RSS: ${appRam} MB \u2022 Total System RAM: ${sysPct}%`;
      }
      if (this.telemCpu) {
        const appCpu = stats.app_cpu_percent !== void 0 ? stats.app_cpu_percent : stats.cpu_percent || 0;
        const sysCpu = stats.sys_cpu_percent !== void 0 ? stats.sys_cpu_percent : appCpu;
        this.telemCpu.textContent = `${appCpu}%`;
        this.telemCpu.title = `App CPU: ${appCpu}% \u2022 Total System CPU: ${sysCpu}%`;
      }
      if (this.telemUptime) {
        const sec = Math.floor(stats.uptime_seconds || 0);
        const hrs = Math.floor(sec / 3600);
        const mins = Math.floor(sec % 3600 / 60);
        const s = sec % 60;
        this.telemUptime.textContent = hrs > 0 ? `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${String(mins).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      }
      this.historyData.push(upSpeedBps);
      if (this.historyData.length > 30) {
        this.historyData.shift();
      }
      this.maxSpeedSeen = Math.max(1024 * 1024, ...this.historyData);
      this.drawSparkline();
    }
    drawSparkline() {
      if (!this.ctx || !this.canvas) return;
      const width = this.canvas.width;
      const height = this.canvas.height;
      const ctx = this.ctx;
      ctx.clearRect(0, 0, width, height);
      const data = this.historyData;
      const len = data.length;
      const max = this.maxSpeedSeen || 1;
      ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();
      const gradient = ctx.createLinearGradient(0, 0, 0, height);
      gradient.addColorStop(0, "rgba(0, 206, 201, 0.35)");
      gradient.addColorStop(1, "rgba(108, 92, 231, 0.02)");
      ctx.beginPath();
      const step = width / (len - 1);
      for (let i = 0; i < len; i++) {
        const x = i * step;
        const normalized = Math.min(1, data[i] / max);
        const y = height - normalized * (height - 6) - 3;
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          const prevX = (i - 1) * step;
          const prevY = height - Math.min(1, data[i - 1] / max) * (height - 6) - 3;
          const cpX = (prevX + x) / 2;
          ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
        }
      }
      ctx.lineTo(width, height);
      ctx.lineTo(0, height);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
      ctx.beginPath();
      for (let i = 0; i < len; i++) {
        const x = i * step;
        const normalized = Math.min(1, data[i] / max);
        const y = height - normalized * (height - 6) - 3;
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          const prevX = (i - 1) * step;
          const prevY = height - Math.min(1, data[i - 1] / max) * (height - 6) - 3;
          const cpX = (prevX + x) / 2;
          ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
        }
      }
      ctx.strokeStyle = "#00cec9";
      ctx.lineWidth = 1.75;
      ctx.stroke();
      const lastX = width;
      const lastY = height - Math.min(1, data[len - 1] / max) * (height - 6) - 3;
      ctx.beginPath();
      ctx.arc(lastX - 2, lastY, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = "#fff";
      ctx.fill();
      ctx.strokeStyle = "#00cec9";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  };
  var telemetryController = new TelemetryController();

  // frontend/js/cinema-ui.js
  var _cinemaVideos = [];
  var _currentPlayingIndex = -1;
  var _currentChatId = "me";
  async function initCinema() {
    const channelSelect = document.getElementById("cinemaChannelSelect");
    const videoSearch = document.getElementById("cinemaSearchInput");
    const btnRefresh = document.getElementById("btnCinemaRefresh");
    const videoPlayer = document.getElementById("cinemaVideoPlayer");
    const btnFdm = document.getElementById("btnCinemaFdm");
    const btnCopyStream = document.getElementById("btnCinemaCopyUrl");
    const btnTheaterMode = document.getElementById("btnCinemaTheater");
    const btnNextTrack = document.getElementById("btnCinemaNext");
    if (!channelSelect || !videoPlayer) return;
    await _loadCinemaChannels();
    channelSelect.addEventListener("change", (e) => {
      _currentChatId = e.target.value;
      loadCinemaVideos(_currentChatId);
    });
    if (btnRefresh) {
      btnRefresh.addEventListener("click", () => loadCinemaVideos(_currentChatId));
    }
    if (videoSearch) {
      videoSearch.addEventListener("input", (e) => {
        _filterCinemaGrid(e.target.value.toLowerCase().trim());
      });
    }
    const errorNotice = document.getElementById("cinemaPlayerErrorNotice");
    const btnFdmFallback = document.getElementById("btnCinemaFdmFallback");
    const btnCopyFallback = document.getElementById("btnCinemaCopyFallback");
    videoPlayer.addEventListener("error", () => {
      if (_currentPlayingIndex >= 0 && _cinemaVideos[_currentPlayingIndex]) {
        const v = _cinemaVideos[_currentPlayingIndex];
        const currentSrc = videoPlayer.getAttribute("src") || "";
        if (!currentSrc.includes("/stream/") && v.stream_transmux_url) {
          console.log("Native stream unsupported. Auto-switching to real-time transmux stream...");
          videoPlayer.src = v.stream_transmux_url;
          videoPlayer.load();
          videoPlayer.play().catch(() => {
          });
          return;
        }
      }
      if (errorNotice) errorNotice.style.display = "flex";
    });
    videoPlayer.addEventListener("loadeddata", () => {
      if (errorNotice) errorNotice.style.display = "none";
    });
    videoPlayer.addEventListener("playing", () => {
      if (errorNotice) errorNotice.style.display = "none";
    });
    if (btnFdmFallback) {
      btnFdmFallback.addEventListener("click", () => btnFdm?.click());
    }
    if (btnCopyFallback) {
      btnCopyFallback.addEventListener("click", () => btnCopyStream?.click());
    }
    if (btnNextTrack) {
      btnNextTrack.addEventListener("click", _playNextVideo);
    }
    videoPlayer.addEventListener("ended", _playNextVideo);
    if (btnFdm) {
      btnFdm.addEventListener("click", async () => {
        if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) return;
        const v = _cinemaVideos[_currentPlayingIndex];
        try {
          const resp = await fetch(`/api/proxy/trigger?chat_id=${encodeURIComponent(v.chat_id)}&message_id=${v.message_id}`, { method: "POST" });
          const data = await resp.json();
          if (data.success) {
            showToast2(`\u{1F680} Dispatched "${v.filename}" to ${data.manager.toUpperCase()}`, "success");
          } else {
            showToast2(`\u26A0\uFE0F Could not auto-launch manager. Copied stream link.`, "warning");
          }
        } catch (err) {
          showToast2(`Download trigger error: ${err.message}`, "error");
        }
      });
    }
    if (btnCopyStream) {
      btnCopyStream.addEventListener("click", () => {
        if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) return;
        const v = _cinemaVideos[_currentPlayingIndex];
        const fullUrl = `${window.location.origin}${v.stream_url}`;
        navigator.clipboard.writeText(fullUrl).then(() => {
          showToast2("\u{1F4CB} Stream URL copied to clipboard!", "success");
        });
      });
    }
    if (btnTheaterMode) {
      btnTheaterMode.addEventListener("click", () => {
        const cinemaContainer = document.querySelector(".cinema-workspace");
        if (cinemaContainer) {
          cinemaContainer.classList.toggle("theater-fullscreen");
          const isFullscreen = cinemaContainer.classList.contains("theater-fullscreen");
          btnTheaterMode.textContent = isFullscreen ? "\u{1F5D7} Standard View" : "\u{1F5A5}\uFE0F Theater Mode";
        }
      });
    }
    document.addEventListener("keydown", (e) => {
      const cinemaTab = document.getElementById("tabPaneCinema");
      if (!cinemaTab || !cinemaTab.classList.contains("active")) return;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
      if (e.code === "Space") {
        e.preventDefault();
        videoPlayer.paused ? videoPlayer.play() : videoPlayer.pause();
      } else if (e.code === "ArrowRight") {
        e.preventDefault();
        videoPlayer.currentTime = Math.min(videoPlayer.duration || 0, videoPlayer.currentTime + 5);
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        videoPlayer.currentTime = Math.max(0, videoPlayer.currentTime - 5);
      } else if (e.code === "KeyM") {
        e.preventDefault();
        videoPlayer.muted = !videoPlayer.muted;
      } else if (e.code === "KeyF") {
        e.preventDefault();
        if (document.fullscreenElement) {
          document.exitFullscreen();
        } else {
          videoPlayer.requestFullscreen?.();
        }
      }
    });
    loadCinemaVideos("me");
  }
  async function _loadCinemaChannels() {
    const select = document.getElementById("cinemaChannelSelect");
    if (!select) return;
    try {
      const resp = await fetch("/api/chats");
      if (!resp.ok) return;
      const chats = await resp.json();
      select.innerHTML = "";
      chats.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.id;
        let icon = "\u{1F4AC}";
        if (c.type === "saved_messages") icon = "\u2601\uFE0F";
        else if (c.type === "channel") icon = "\u{1F4E2}";
        else if (c.type === "supergroup" || c.type === "group") icon = "\u{1F465}";
        else if (c.type === "bot") icon = "\u{1F916}";
        opt.textContent = `${icon} ${c.name}`;
        select.appendChild(opt);
      });
    } catch (err) {
      console.debug("Error loading cinema channels:", err);
    }
  }
  async function loadCinemaVideos(chatId) {
    const grid = document.getElementById("cinemaVideoGrid");
    const countBadge = document.getElementById("cinemaVideoCount");
    if (!grid) return;
    grid.innerHTML = `
    <div class="cinema-loading">
      <div class="spinner" style="width: 28px; height: 28px; margin: 0 auto 12px auto;"></div>
      <span>Fetching video archive from Telegram Cloud...</span>
    </div>
  `;
    try {
      const resp = await fetch(`/api/media/videos/${encodeURIComponent(chatId)}?limit=60`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
      }
      const data = await resp.json();
      _cinemaVideos = data.videos || [];
      if (countBadge) {
        countBadge.textContent = `${_cinemaVideos.length} Videos`;
      }
      renderCinemaGrid(_cinemaVideos);
    } catch (err) {
      grid.innerHTML = `
      <div class="cinema-empty">
        <span style="font-size: 2.2rem; margin-bottom: 8px;">\u26A0\uFE0F</span>
        <span style="font-weight: 600; color: var(--text-main);">Could not load video archive</span>
        <span style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">${err.message}</span>
      </div>
    `;
    }
  }
  function renderCinemaGrid(videos) {
    const grid = document.getElementById("cinemaVideoGrid");
    if (!grid) return;
    if (videos.length === 0) {
      grid.innerHTML = `
      <div class="cinema-empty">
        <span style="font-size: 2.2rem; margin-bottom: 8px;">\u{1F3AC}</span>
        <span style="font-weight: 600; color: var(--text-main);">No video media found in this chat</span>
        <span style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">Upload or forward video files to watch them here instantly</span>
      </div>
    `;
      return;
    }
    grid.innerHTML = "";
    videos.forEach((v, idx) => {
      const card = document.createElement("div");
      card.className = `video-card glass-panel ${_currentPlayingIndex === idx ? "active" : ""}`;
      card.id = `videoCard_${idx}`;
      let resLabel = "";
      if (v.height >= 2160 || v.width >= 3840) resLabel = "4K UHD";
      else if (v.height >= 1080 || v.width >= 1920) resLabel = "1080p";
      else if (v.height >= 720 || v.width >= 1280) resLabel = "720p";
      else if (v.height > 0) resLabel = `${v.height}p`;
      const durationStr = v.duration > 0 ? _formatDuration(v.duration) : "";
      const thumbHtml = v.has_thumb ? `<img class="video-thumb-img" src="${v.thumb_url}" alt="" loading="lazy" onerror="this.style.display='none'; if (this.nextElementSibling) this.nextElementSibling.style.display='flex';">
         <div class="video-thumb-fallback" style="display: none;">\u{1F3AC}</div>` : `<div class="video-thumb-fallback">\u{1F3AC}</div>`;
      card.innerHTML = `
      <div class="video-thumb-container">
        ${thumbHtml}
        ${resLabel ? `<span class="video-res-pill">${resLabel}</span>` : ""}
        ${durationStr ? `<span class="video-duration-pill">${durationStr}</span>` : ""}
        <div class="video-play-overlay">\u25B6</div>
      </div>
      <div class="video-meta">
        <span class="video-card-title" title="${v.filename}">${v.filename}</span>
        <div class="video-card-sub">
          <span>${formatBytes(v.file_size)}</span>
          <span>\u2022</span>
          <span>${v.date ? new Date(v.date * 1e3).toLocaleDateString() : "Cloud"}</span>
        </div>
      </div>
    `;
      card.addEventListener("click", () => {
        selectCinemaVideo(idx, true);
      });
      grid.appendChild(card);
    });
  }
  function _filterCinemaGrid(query) {
    if (!query) {
      renderCinemaGrid(_cinemaVideos);
      return;
    }
    const filtered = _cinemaVideos.filter((v) => v.filename.toLowerCase().includes(query));
    renderCinemaGrid(filtered);
  }
  function selectCinemaVideo(idx, autoPlay = true) {
    if (idx < 0 || idx >= _cinemaVideos.length) return;
    _currentPlayingIndex = idx;
    const v = _cinemaVideos[idx];
    const videoPlayer = document.getElementById("cinemaVideoPlayer");
    const standbyNotice = document.getElementById("cinemaPlayerStandbyNotice");
    const errorNotice = document.getElementById("cinemaPlayerErrorNotice");
    const titleElem = document.getElementById("cinemaNowPlayingTitle");
    const resElem = document.getElementById("cinemaNowPlayingRes");
    const sizeElem = document.getElementById("cinemaNowPlayingSize");
    const durElem = document.getElementById("cinemaNowPlayingDur");
    if (standbyNotice) standbyNotice.style.display = "none";
    if (errorNotice) errorNotice.style.display = "none";
    if (videoPlayer) videoPlayer.style.display = "block";
    if (titleElem) titleElem.textContent = v.filename;
    if (resElem) {
      let resText = "HD Video";
      if (v.width && v.height) resText = `${v.width}x${v.height}`;
      resElem.textContent = resText;
    }
    if (sizeElem) sizeElem.textContent = formatBytes(v.file_size);
    if (durElem) durElem.textContent = v.duration > 0 ? _formatDuration(v.duration) : "--:--";
    document.querySelectorAll(".video-card").forEach((c) => c.classList.remove("active"));
    const activeCard = document.getElementById(`videoCard_${idx}`);
    if (activeCard) activeCard.classList.add("active");
    if (videoPlayer) {
      const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);
      const streamUrl = isMkv && v.stream_transmux_url ? v.stream_transmux_url : v.stream_url;
      videoPlayer.src = streamUrl;
      videoPlayer.load();
      if (autoPlay) {
        videoPlayer.play().catch((err) => {
          console.debug("Autoplay hindered:", err);
        });
      }
    }
  }
  function _playNextVideo() {
    if (_cinemaVideos.length === 0) return;
    const nextIdx = (_currentPlayingIndex + 1) % _cinemaVideos.length;
    selectCinemaVideo(nextIdx, true);
    showToast2(`\u23ED\uFE0F Now Playing: ${_cinemaVideos[nextIdx].filename}`, "info");
  }
  function _formatDuration(seconds) {
    if (!seconds) return "00:00";
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor(seconds % 3600 / 60);
    const secs = Math.floor(seconds % 60);
    if (hrs > 0) {
      return `${hrs}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
    }
    return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  }

  // frontend/js/app.js
  async function checkAuthStatus() {
    const badge = document.getElementById("authStatusBadge");
    const userText = document.getElementById("authUserName");
    if (!badge || !userText) return;
    try {
      const res = await fetch("/api/auth/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.authenticated) {
        badge.classList.add("authorized");
        const name = data.first_name || "Authorized";
        const handle = data.username ? ` (@${data.username})` : "";
        userText.textContent = `${name}${handle}`;
      } else {
        badge.classList.remove("authorized");
        userText.textContent = "Not Authorized (Run setup_auth.py)";
      }
    } catch (e) {
      console.debug("Auth check status notice:", e);
      badge.classList.remove("authorized");
      userText.textContent = "Backend Offline";
    }
  }
  function setupDragAndDrop() {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("fileInput");
    const folderInput = document.getElementById("folderInput");
    const btnBrowseFiles = document.getElementById("btnBrowseFiles");
    const btnBrowseFolder = document.getElementById("btnBrowseFolder");
    if (btnBrowseFiles && fileInput) {
      btnBrowseFiles.addEventListener("click", (e) => {
        e.stopPropagation();
        fileInput.click();
      });
    }
    if (btnBrowseFolder && folderInput) {
      btnBrowseFolder.addEventListener("click", (e) => {
        e.stopPropagation();
        folderInput.click();
      });
    }
    if (dropzone && fileInput) {
      dropzone.addEventListener("click", (e) => {
        if (btnBrowseFolder && (btnBrowseFolder === e.target || btnBrowseFolder.contains(e.target))) {
          return;
        }
        fileInput.click();
      });
    }
    if (fileInput) {
      fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
          uploader.addFiles(e.target.files);
          fileInput.value = "";
        }
      });
    }
    if (folderInput) {
      folderInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
          uploader.addFiles(e.target.files);
          folderInput.value = "";
        }
      });
    }
    if (dropzone) {
      let dragCounter = 0;
      ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (e) => {
          e.preventDefault();
          e.stopPropagation();
        });
      });
      dropzone.addEventListener("dragenter", () => {
        dragCounter++;
        dropzone.classList.add("drag-active");
      });
      dropzone.addEventListener("dragleave", () => {
        dragCounter--;
        if (dragCounter <= 0) {
          dragCounter = 0;
          dropzone.classList.remove("drag-active");
        }
      });
      dropzone.addEventListener("drop", (e) => {
        dragCounter = 0;
        dropzone.classList.remove("drag-active");
        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files.length > 0) {
          uploader.addFiles(dt.files);
        }
      });
    }
  }
  function setupGlobalHooks() {
    window._app = {
      pause: (id) => uploader.pause(id),
      resume: (id) => uploader.resume(id),
      cancel: (id) => uploader.cancel(id),
      remove: (id) => uploader.remove(id),
      updateFilename: (id, val) => uploader.updateTaskConfig(id, { customFilename: val }),
      updateCaption: (id, val) => uploader.updateTaskConfig(id, { caption: val }),
      updateSendAs: (id, val) => uploader.updateTaskConfig(id, { sendAs: val }),
      clearHistory: async () => {
        if (confirm("Are you sure you want to clear all history?")) {
          await fetch("/api/history/clear", { method: "DELETE" });
          loadHistory();
          showToast2("History cleared", "success");
        }
      },
      refreshChats: () => {
        chatPicker.fetchChats(true);
        showToast2("Refreshing chat list...", "info");
      }
    };
  }
  function initApp() {
    console.log("Initializing TG Power Suite Frontend...");
    try {
      setupGlobalHooks();
    } catch (e) {
      console.error("Hooks setup error:", e);
    }
    try {
      setupDragAndDrop();
    } catch (e) {
      console.error("Drag & Drop setup error:", e);
    }
    try {
      tabController.init();
    } catch (e) {
      console.error("Tab controller error:", e);
    }
    try {
      themeManager.init();
    } catch (e) {
      console.error("Theme manager error:", e);
    }
    try {
      socketManager.init();
    } catch (e) {
      console.error("Socket manager error:", e);
    }
    try {
      networkWatchdog.init();
    } catch (e) {
      console.error("Watchdog error:", e);
    }
    try {
      chatPicker.init((selectedChat) => {
        console.log("Selected destination chat:", selectedChat);
      });
    } catch (e) {
      console.error("Chat picker error:", e);
    }
    try {
      snifferUI.init(socketManager.socket, tabController);
    } catch (e) {
      console.error("Sniffer UI error:", e);
    }
    try {
      settingsUI.init(tabController);
    } catch (e) {
      console.error("Settings UI error:", e);
    }
    try {
      telemetryController.init(socketManager.socket);
    } catch (e) {
      console.error("Telemetry Controller error:", e);
    }
    try {
      initHistoryControls();
    } catch (e) {
      console.error("History Controls error:", e);
    }
    try {
      initCinema();
    } catch (e) {
      console.error("Cinema Controller error:", e);
    }
    const btnBatchPause = document.getElementById("btnBatchPause");
    const btnBatchResume = document.getElementById("btnBatchResume");
    const btnBatchClear = document.getElementById("btnBatchClear");
    const btnBatchCancel = document.getElementById("btnBatchCancel");
    if (btnBatchPause) {
      btnBatchPause.addEventListener("click", () => {
        uploader.pauseAll();
        showToast2("All active uploads paused", "info");
      });
    }
    if (btnBatchResume) {
      btnBatchResume.addEventListener("click", () => {
        uploader.resumeAll();
        showToast2("Resuming uploads...", "info");
      });
    }
    if (btnBatchClear) {
      btnBatchClear.addEventListener("click", () => {
        uploader.clearCompleted();
        showToast2("Completed tasks cleared", "info");
      });
    }
    if (btnBatchCancel) {
      btnBatchCancel.addEventListener("click", () => {
        if (confirm("Cancel and stop all active uploads in the queue?")) {
          uploader.cancelAll();
          showToast2("All uploads cancelled", "warning");
        }
      });
    }
    uploader.onQueueChange((queue) => {
      renderQueue(queue);
      const tabUploaderBadge = document.getElementById("tabUploaderBadge");
      if (tabUploaderBadge) {
        tabUploaderBadge.textContent = queue.length;
      }
      if (btnPauseResumeAllUploads) {
        const hasActive = queue.some((t) => t.status === "uploading" || t.status === "streaming" || t.status === "queued");
        btnPauseResumeAllUploads.textContent = hasActive ? "\u23F8\uFE0F Pause All" : "\u25B6\uFE0F Resume All";
      }
    });
    socketManager.onProgress((data) => {
      uploader.handleSocketProgress(data);
      if (data.status === "completed") {
        loadHistory();
      }
    });
    socketManager.onQueueSnapshot((tasks) => {
      uploader.syncWithSnapshot(tasks);
    });
    fetch("/api/tasks").then((res) => res.json()).then((tasks) => {
      if (Array.isArray(tasks)) {
        uploader.syncWithSnapshot(tasks);
      }
    }).catch((err) => console.debug("Could not pre-fetch tasks:", err));
    checkAuthStatus();
    loadHistory();
    setInterval(() => {
      checkAuthStatus();
      loadHistory();
    }, 3e4);
    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js").then((reg) => {
          console.log("TG Power Suite PWA Service Worker active:", reg.scope);
        }).catch((err) => {
          console.debug("Service Worker notice:", err);
        });
      });
    }
    let deferredInstallPrompt = null;
    const btnPwaInstall = document.getElementById("btnPwaInstall");
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      deferredInstallPrompt = e;
      if (btnPwaInstall) {
        btnPwaInstall.style.display = "inline-flex";
      }
    });
    if (btnPwaInstall) {
      btnPwaInstall.addEventListener("click", async () => {
        if (!deferredInstallPrompt) {
          showToast2("App is already installed or your browser handles installation in the address bar (\u2795)", "info");
          return;
        }
        deferredInstallPrompt.prompt();
        const { outcome } = await deferredInstallPrompt.userChoice;
        if (outcome === "accepted") {
          btnPwaInstall.style.display = "none";
          showToast2("TG Power Suite installed to your desktop!", "success");
        }
        deferredInstallPrompt = null;
      });
    }
    window.addEventListener("appinstalled", () => {
      if (btnPwaInstall) btnPwaInstall.style.display = "none";
      showToast2("Welcome to TG Power Suite Desktop App!", "success");
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initApp);
  } else {
    initApp();
  }
})();
