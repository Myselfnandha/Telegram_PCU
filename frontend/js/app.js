/**
 * Application Core Controller.
 * Initializes modules, event listeners, drag & drop, and global app state.
 */

import { socketManager } from './socket.js';
import { chatPicker } from './chat-picker.js';
import { uploader } from './uploader.js';
import { renderQueue, loadHistory, showToast } from './ui.js';
import { networkWatchdog } from './network-watchdog.js';
import { themeManager } from './theme.js';

let activeCategoryFilter = 'all';

async function checkAuthStatus() {
  const badge = document.getElementById('authStatusBadge');
  const userText = document.getElementById('authUserName');
  if (!badge || !userText) return;

  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();

    if (data.authenticated) {
      badge.classList.add('authorized');
      const name = data.first_name || 'Authorized';
      const handle = data.username ? ` (@${data.username})` : '';
      userText.textContent = `${name}${handle}`;
    } else {
      badge.classList.remove('authorized');
      userText.textContent = 'Not Authorized (Run setup_auth.py)';
    }
  } catch (e) {
    badge.classList.remove('authorized');
    userText.textContent = 'Backend Offline';
  }
}

function setupDragAndDrop() {
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const folderInput = document.getElementById('folderInput');
  const btnBrowseFiles = document.getElementById('btnBrowseFiles');
  const btnBrowseFolder = document.getElementById('btnBrowseFolder');

  if (btnBrowseFiles && fileInput) {
    btnBrowseFiles.addEventListener('click', (e) => {
      e.stopPropagation();
      fileInput.click();
    });
  }

  if (btnBrowseFolder && folderInput) {
    btnBrowseFolder.addEventListener('click', (e) => {
      e.stopPropagation();
      folderInput.click();
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files.length > 0) {
        uploader.addFiles(e.target.files);
        fileInput.value = '';
      }
    });
  }

  if (folderInput) {
    folderInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files.length > 0) {
        uploader.addFiles(e.target.files);
        folderInput.value = '';
      }
    });
  }

  if (dropzone) {
    let dragCounter = 0;

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((eventName) => {
      dropzone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
    });

    dropzone.addEventListener('dragenter', () => {
      dragCounter++;
      dropzone.classList.add('drag-active');
    });

    dropzone.addEventListener('dragleave', () => {
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        dropzone.classList.remove('drag-active');
      }
    });

    dropzone.addEventListener('drop', (e) => {
      dragCounter = 0;
      dropzone.classList.remove('drag-active');
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
      if (confirm('Are you sure you want to clear all upload history?')) {
        await fetch('/api/history/clear', { method: 'DELETE' });
        loadHistory();
        showToast('History cleared', 'success');
      }
    },
    refreshChats: () => {
      chatPicker.fetchChats(true);
      showToast('Refreshing chat list...', 'info');
    }
  };
}

// Bootstrap Application
document.addEventListener('DOMContentLoaded', () => {
  console.log('Initializing Telegram Web Uploader Frontend...');

  setupGlobalHooks();
  setupDragAndDrop();

  // Initialize Theme Engine
  themeManager.init();

  // Initialize Socket.IO
  socketManager.init();

  // Initialize Network Watchdog & Auto-Recovery
  networkWatchdog.init();

  // Initialize Destination Chat Picker
  chatPicker.init((selectedChat) => {
    console.log('Selected destination chat:', selectedChat);
  });

  // Batch Upload Controls
  const btnPauseResumeAllUploads = document.getElementById('btnPauseResumeAllUploads');
  const btnStopAllUploads = document.getElementById('btnStopAllUploads');

  if (btnPauseResumeAllUploads) {
    btnPauseResumeAllUploads.addEventListener('click', () => {
      uploader.togglePauseResumeAll();
      const hasActive = uploader.queue.some((t) => t.status === 'uploading' || t.status === 'streaming' || t.status === 'queued');
      btnPauseResumeAllUploads.textContent = hasActive ? '⏸️ Pause All' : '▶️ Resume All';
    });
  }

  if (btnStopAllUploads) {
    btnStopAllUploads.addEventListener('click', () => {
      if (confirm('Are you sure you want to stop and cancel all active uploads?')) {
        uploader.stopAll();
        showToast('All uploads stopped and cleared', 'info');
      }
    });
  }

  // Connect Uploader with UI and Socket.IO
  uploader.onQueueChange((queue) => {
    renderQueue(queue);
    // Sync button text
    if (btnPauseResumeAllUploads) {
      const hasActive = queue.some((t) => t.status === 'uploading' || t.status === 'streaming' || t.status === 'queued');
      btnPauseResumeAllUploads.textContent = hasActive ? '⏸️ Pause All' : '▶️ Resume All';
    }
  });

  socketManager.onProgress((data) => {
    uploader.handleSocketProgress(data);
    if (data.status === 'completed') {
      loadHistory();
    }
  });

  socketManager.onQueueSnapshot((tasks) => {
    uploader.syncWithSnapshot(tasks);
  });

  // Rehydrate initial active tasks from backend immediately on page load
  fetch('/api/tasks')
    .then((res) => res.json())
    .then((tasks) => {
      if (Array.isArray(tasks)) {
        uploader.syncWithSnapshot(tasks);
      }
    })
    .catch((err) => console.debug('Could not pre-fetch tasks:', err));

  // Check Telegram auth status
  checkAuthStatus();

  // Load initial upload history
  loadHistory();

  // Periodic auth & history check every 30s
  setInterval(() => {
    checkAuthStatus();
    loadHistory();
  }, 30000);
});
