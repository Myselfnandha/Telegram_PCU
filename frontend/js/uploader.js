/**
 * Client Upload Queue and Transfer Manager.
 * Orchestrates multi-file queuing, zero-repeat XHR streaming,
 * and seamless coordination with WebSocket real-time progress.
 */

import { socketManager } from './socket.js';
import { createSafePreview, revokePreview } from './preview.js';
import { chatPicker } from './chat-picker.js';

class Uploader {
  constructor() {
    this.queue = [];
    this.activeTask = null;
    this.isProcessing = false;
    this.activeXhrs = new Map();
    this.onQueueChangeCallbacks = new Set();
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
        console.error('Queue change callback error:', e);
      }
    });
  }

  addFiles(fileList) {
    const selectedChat = chatPicker.getSelectedChat();
    const chatId = selectedChat ? selectedChat.id : 'me';
    const chatName = selectedChat ? selectedChat.name : 'Saved Messages (Personal Cloud)';

    Array.from(fileList).forEach((file) => {
      const id = 'task_' + Math.random().toString(36).substring(2, 10) + '_' + Date.now();
      const preview = createSafePreview(id, file);

      const task = {
        id,
        file,
        filename: file.name,
        customFilename: file.name,
        fileSize: file.size,
        chatId: chatId,
        chatName: chatName,
        caption: '',
        sendAs: 'auto', // auto | document | media
        status: 'queued', // queued | streaming | uploading | splitting | paused | completed | failed | cancelled
        progress: 0,
        uploadedBytes: 0,
        speed: 0,
        eta: 0,
        currentPart: 1,
        totalParts: 1,
        error: null,
        preview,
        transferredToServer: false, // Prevents duplicate XHR uploads
        isTransferring: false
      };

      this.queue.push(task);
    });

    this._notify();
    this.processNext();
  }

  updateTaskConfig(id, { customFilename, caption, sendAs }) {
    const task = this.queue.find((t) => t.id === id);
    if (task && task.status === 'queued' && !task.transferredToServer) {
      if (customFilename !== undefined) task.customFilename = customFilename;
      if (caption !== undefined) task.caption = caption;
      if (sendAs !== undefined) task.sendAs = sendAs;
      this._notify();
    }
  }

  async processNext() {
    if (this.isProcessing) return;

    // Find first queued task that has not been transferred to server yet
    const nextTask = this.queue.find(
      (t) => (t.status === 'queued' || t.status === 'streaming') && !t.transferredToServer && !t.isTransferring
    );

    if (!nextTask || !nextTask.file) return;

    this.isProcessing = true;
    this.activeTask = nextTask;
    nextTask.isTransferring = true;
    nextTask.status = 'streaming';
    nextTask.progress = 0;
    this._notify();

    const file = nextTask.file;
    const totalSize = file.size;
    const CHUNK_SIZE = 25 * 1024 * 1024; // 25 MB chunks (prevents /tmp RAM exhaustion on huge 10GB+ files)

    if (totalSize > CHUNK_SIZE) {
      await this._uploadFileInChunks(nextTask, CHUNK_SIZE);
    } else {
      await this._uploadFileDirect(nextTask);
    }
  }

  async _uploadFileInChunks(nextTask, chunkSize) {
    const file = nextTask.file;
    const totalSize = file.size;
    const totalChunks = Math.ceil(totalSize / chunkSize);
    let lastTime = performance.now();
    let lastLoaded = 0;

    const startChunk = nextTask.uploadedChunkIndex || 0;

    for (let chunkIdx = startChunk; chunkIdx < totalChunks; chunkIdx++) {
      if (nextTask.status === 'cancelled' || nextTask.status === 'paused') {
        nextTask.isTransferring = false;
        this.isProcessing = false;
        this.activeTask = null;
        return;
      }

      const start = chunkIdx * chunkSize;
      const end = Math.min(totalSize, start + chunkSize);
      const chunkBlob = file.slice(start, end);

      const formData = new FormData();
      formData.append('file', chunkBlob, nextTask.filename);
      formData.append('upload_id', nextTask.id);
      formData.append('chunk_index', chunkIdx);
      formData.append('total_chunks', totalChunks);
      formData.append('offset', start);
      formData.append('total_size', totalSize);
      formData.append('filename', nextTask.filename);

      let chunkSuccess = false;
      for (let attempt = 1; attempt <= 3; attempt++) {
        if (nextTask.status === 'cancelled' || nextTask.status === 'paused') {
          nextTask.isTransferring = false;
          this.isProcessing = false;
          this.activeTask = null;
          return;
        }

        try {
          await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            this.activeXhrs.set(nextTask.id, xhr);

            xhr.upload.onprogress = (e) => {
              if (e.lengthComputable && nextTask.status !== 'cancelled' && nextTask.status !== 'failed' && nextTask.status !== 'paused') {
                const totalLoaded = start + e.loaded;
                const now = performance.now();
                const elapsed = (now - lastTime) / 1000;
                const percent = Math.min(99.0, (totalLoaded / totalSize) * 100);
                nextTask.progress = Math.round(percent * 10) / 10;
                nextTask.uploadedBytes = totalLoaded;

                if (elapsed >= 0.25) {
                  const delta = totalLoaded - lastLoaded;
                  nextTask.speed = Math.max(0, delta / elapsed);
                  const remaining = totalSize - totalLoaded;
                  nextTask.eta = nextTask.speed > 0 ? remaining / nextTask.speed : 0;
                  lastTime = now;
                  lastLoaded = totalLoaded;
                }
                this._notify();
              }
            };

            xhr.onload = () => {
              this.activeXhrs.delete(nextTask.id);
              if (xhr.status >= 200 && xhr.status < 300) {
                nextTask.uploadedChunkIndex = chunkIdx + 1;
                resolve();
              } else {
                reject(new Error(`Chunk ${chunkIdx} failed with status ${xhr.status}`));
              }
            };

            xhr.onerror = () => {
              this.activeXhrs.delete(nextTask.id);
              reject(new Error(`Network connection error on chunk ${chunkIdx}`));
            };

            xhr.onabort = () => {
              this.activeXhrs.delete(nextTask.id);
              reject(new Error('aborted'));
            };

            xhr.open('POST', '/api/upload/chunk', true);
            xhr.send(formData);
          });
          chunkSuccess = true;
          break;
        } catch (err) {
          if (nextTask.status === 'cancelled' || nextTask.status === 'paused' || err.message === 'aborted') {
            nextTask.isTransferring = false;
            this.isProcessing = false;
            this.activeTask = null;
            return;
          }
          console.warn(`[ChunkUpload] Attempt ${attempt}/3 failed for chunk ${chunkIdx}:`, err);
          if (attempt === 3) {
            nextTask.status = 'failed';
            nextTask.error = err.message || 'Chunk transfer failed after 3 attempts';
            this._notify();
            this.isProcessing = false;
            this.activeTask = null;
            setTimeout(() => this.processNext(), 200);
            return;
          }
          await new Promise((r) => setTimeout(r, 1000));
        }
      }

      if (!chunkSuccess) return;
    }

    // All chunks transferred to NVMe disk! Finalize and enqueue on backend
    try {
      const completeResp = await fetch('/api/upload/chunk/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          upload_id: nextTask.id,
          chat_id: String(nextTask.chatId),
          chat_name: nextTask.chatName || '',
          caption: nextTask.caption || '',
          filename: nextTask.customFilename || nextTask.filename,
          send_as: nextTask.sendAs || 'auto',
          total_size: totalSize
        })
      });

      if (!completeResp.ok) {
        const errJson = await completeResp.json().catch(() => ({}));
        throw new Error(errJson.detail || `Complete error (${completeResp.status})`);
      }

      console.log(`[Upload] Chunked file transfer finalized for task ${nextTask.id}`);
      nextTask.transferredToServer = true;
      nextTask.isTransferring = false;
      if (nextTask.status === 'streaming') {
        nextTask.status = 'uploading';
        this._notify();
      }
    } catch (err) {
      nextTask.status = 'failed';
      nextTask.error = err.message || 'Failed to finalize chunked upload';
      this._notify();
    }

    this.isProcessing = false;
    this.activeTask = null;
    setTimeout(() => this.processNext(), 150);
  }

  async _uploadFileDirect(nextTask) {
    const formData = new FormData();
    formData.append('file', nextTask.file);
    formData.append('upload_id', nextTask.id);
    formData.append('chat_id', nextTask.chatId);
    formData.append('chat_name', nextTask.chatName);
    formData.append('caption', nextTask.caption || '');
    formData.append('filename', nextTask.customFilename || nextTask.filename);
    formData.append('send_as', nextTask.sendAs || 'auto');

    const xhr = new XMLHttpRequest();
    this.activeXhrs.set(nextTask.id, xhr);

    let lastTime = performance.now();
    let lastLoaded = 0;

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && nextTask.status !== 'cancelled' && nextTask.status !== 'failed') {
        const now = performance.now();
        const elapsed = (now - lastTime) / 1000;

        const percent = Math.min(99.0, (e.loaded / e.total) * 100);
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

        if (nextTask.status === 'queued' || nextTask.status === 'preparing') {
          nextTask.status = 'streaming';
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
        if (nextTask.status === 'streaming') {
          nextTask.status = 'uploading';
          this._notify();
        }
      } else {
        let errMessage = `Server error (${xhr.status})`;
        try {
          const errObj = JSON.parse(xhr.responseText);
          errMessage = errObj.detail || errMessage;
        } catch (_) {}
        nextTask.status = 'failed';
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
      if (nextTask.status !== 'cancelled') {
        nextTask.status = 'failed';
        nextTask.error = 'Network connection to backend failed';
        this._notify();
      }
      this.isProcessing = false;
      this.activeTask = null;
      setTimeout(() => this.processNext(), 300);
    };

    xhr.onabort = () => {
      this.activeXhrs.delete(nextTask.id);
      nextTask.isTransferring = false;
      nextTask.status = 'cancelled';
      this._notify();
      this.isProcessing = false;
      this.activeTask = null;
      setTimeout(() => this.processNext(), 150);
    };

    xhr.open('POST', '/api/upload', true);
    xhr.send(formData);
  }

  pause(id) {
    const task = this.queue.find((t) => t.id === id);
    if (task) {
      task.status = 'paused';
      task.isTransferring = false;
      const xhr = this.activeXhrs.get(id);
      if (xhr) {
        xhr.abort();
        this.activeXhrs.delete(id);
      }
      socketManager.pauseTask(id);
      fetch(`/api/tasks/${encodeURIComponent(id)}/pause`, { method: 'POST' }).catch(() => {});
      if (this.activeTask && this.activeTask.id === id) {
        this.isProcessing = false;
        this.activeTask = null;
      }
      this._notify();
    }
  }

  resume(id) {
    const task = this.queue.find((t) => t.id === id);
    if (task) {
      if (task.transferredToServer) {
        task.status = 'uploading';
        socketManager.resumeTask(id);
        fetch(`/api/tasks/${encodeURIComponent(id)}/resume`, { method: 'POST' }).catch(() => {});
      } else {
        task.status = 'queued';
        task.isTransferring = false;
        this.isProcessing = false;
        this.activeTask = null;
        setTimeout(() => this.processNext(), 50);
      }
      this._notify();
    }
  }

  cancel(id) {
    const task = this.queue.find((t) => t.id === id);
    if (task) {
      // If actively in XHR transfer, abort immediately
      const xhr = this.activeXhrs.get(id);
      if (xhr) {
        xhr.abort();
        this.activeXhrs.delete(id);
      }

      // Notify backend to cancel Telegram worker
      socketManager.cancelTask(id);
      fetch(`/api/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }).catch(() => {});

      task.status = 'cancelled';
      task.isTransferring = false;
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
      await fetch('/api/upload/batch/pause', { method: 'POST' });
    } catch (e) {
      console.warn('Could not pause all tasks:', e);
    }
    this.queue.forEach((task) => {
      if (['uploading', 'streaming', 'queued', 'splitting'].includes(task.status)) {
        task.status = 'paused';
        task.isTransferring = false;
        const xhr = this.activeXhrs.get(task.id);
        if (xhr) {
          xhr.abort();
          this.activeXhrs.delete(task.id);
        }
      }
    });
    this.isProcessing = false;
    this.activeTask = null;
    this._notify();
  }

  async resumeAll() {
    try {
      await fetch('/api/upload/batch/resume', { method: 'POST' });
    } catch (e) {
      console.warn('Could not resume all tasks:', e);
    }
    this.queue.forEach((task) => {
      if (task.status === 'paused') {
        task.status = task.transferredToServer ? 'uploading' : 'queued';
        task.isTransferring = false;
      }
    });
    this.isProcessing = false;
    this.activeTask = null;
    this._notify();
    setTimeout(() => this.processNext(), 50);
  }

  async cancelAll() {
    try {
      await fetch('/api/upload/batch/cancel', { method: 'POST' });
    } catch (e) {
      console.warn('Could not cancel all tasks:', e);
    }
    this.activeXhrs.forEach((xhr) => xhr.abort());
    this.activeXhrs.clear();
    this.queue.forEach((task) => {
      if (task.status !== 'completed') {
        task.status = 'cancelled';
        revokePreview(task.id);
      }
    });
    this.isProcessing = false;
    this._notify();
  }

  async clearCompleted() {
    try {
      await fetch('/api/upload/batch/clear', { method: 'POST' });
    } catch (e) {
      console.warn('Could not clear completed tasks on server:', e);
    }
    this.queue = this.queue.filter((t) => !['completed', 'cancelled', 'failed'].includes(t.status));
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
    
    // If task does not exist locally (e.g. after browser page reload), rehydrate it!
    if (!task && data.status !== 'completed' && data.status !== 'cancelled') {
      task = {
        id: data.id,
        file: null,
        filename: data.filename || 'Uploading File',
        customFilename: data.filename || 'Uploading File',
        fileSize: data.file_size || 0,
        chatId: data.chat_id || 'me',
        chatName: data.chat_name || 'Telegram Chat',
        caption: '',
        sendAs: 'auto',
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

      // Clean preview when complete to free RAM
      if (data.status === 'completed' || data.status === 'failed') {
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
        // Never revert back to queued or streaming if already processed
        if (srvTask.status !== 'queued' || !existing.transferredToServer) {
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
      } else if (srvTask.status !== 'completed' && srvTask.status !== 'cancelled') {
        // Rehydrate in-progress task into local queue on page reload
        const rehydratedTask = {
          id: srvTask.id,
          file: null,
          filename: srvTask.filename || 'Uploading File',
          customFilename: srvTask.filename || 'Uploading File',
          fileSize: srvTask.file_size || 0,
          chatId: srvTask.chat_id || 'me',
          chatName: srvTask.chat_name || 'Telegram Chat',
          caption: '',
          sendAs: 'auto',
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
}

export const uploader = new Uploader();
