/**
 * TG Power Suite Application Core Controller.
 * Initializes modules, event listeners, drag & drop, tab switching, and global state.
 */

import { socketManager } from './socket.js';
import { chatPicker } from './chat-picker.js';
import { uploader } from './uploader.js';
import { renderQueue, loadHistory, showToast, initHistoryControls } from './ui.js';
import { networkWatchdog } from './network-watchdog.js';
import { themeManager } from './theme.js';
import { tabController } from './tabs.js';
import { snifferUI } from './sniffer-ui.js';
import { settingsUI } from './settings-ui.js';
import { telemetryController } from './telemetry.js';
import { initCinema } from './cinema-ui.js';
import { authUI } from './auth-ui.js';

async function checkAuthStatus() {
  return authUI.checkStatus();
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

  if (dropzone && fileInput) {
    dropzone.addEventListener('click', (e) => {
      // If user clicked the folder button, let it handle its own click
      if (btnBrowseFolder && (btnBrowseFolder === e.target || btnBrowseFolder.contains(e.target))) {
        return;
      }
      fileInput.click();
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
      if (confirm('Are you sure you want to clear all history?')) {
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

function initApp() {
  console.log('Initializing TG Power Suite Frontend...');

  try {
    setupGlobalHooks();
  } catch (e) {
    console.error('Hooks setup error:', e);
  }

  try {
    setupDragAndDrop();
  } catch (e) {
    console.error('Drag & Drop setup error:', e);
  }

  // Initialize Tab Navigation
  try {
    tabController.init();
  } catch (e) {
    console.error('Tab controller error:', e);
  }

  // Initialize Theme Engine
  try {
    themeManager.init();
  } catch (e) {
    console.error('Theme manager error:', e);
  }

  // Initialize Socket.IO
  try {
    socketManager.init();
  } catch (e) {
    console.error('Socket manager error:', e);
  }

  // Initialize Network Watchdog
  try {
    networkWatchdog.init();
  } catch (e) {
    console.error('Watchdog error:', e);
  }

  // Initialize Chat Picker
  try {
    chatPicker.init((selectedChat) => {
      console.log('Selected destination chat:', selectedChat);
    });
  } catch (e) {
    console.error('Chat picker error:', e);
  }

  // Initialize Web Interactive Auth UI
  try {
    authUI.init();
  } catch (e) {
    console.error('Auth UI error:', e);
  }

  // Initialize Sniffer UI
  try {
    snifferUI.init(socketManager.socket, tabController);
  } catch (e) {
    console.error('Sniffer UI error:', e);
  }

  // Initialize Settings UI
  try {
    settingsUI.init(tabController);
  } catch (e) {
    console.error('Settings UI error:', e);
  }

  // Initialize Real-Time Telemetry & Throughput Sparkline
  try {
    telemetryController.init(socketManager.socket);
  } catch (e) {
    console.error('Telemetry Controller error:', e);
  }

  // Initialize History Search, Filter & CSV/JSON Export Controls
  try {
    initHistoryControls();
  } catch (e) {
    console.error('History Controls error:', e);
  }

  // Initialize Cinema & Video Streaming Tab
  try {
    initCinema();
  } catch (e) {
    console.error('Cinema Controller error:', e);
  }

  // Batch Upload Action Buttons
  const btnBatchPause = document.getElementById('btnBatchPause');
  const btnBatchResume = document.getElementById('btnBatchResume');
  const btnBatchClear = document.getElementById('btnBatchClear');
  const btnBatchCancel = document.getElementById('btnBatchCancel');

  if (btnBatchPause) {
    btnBatchPause.addEventListener('click', () => {
      uploader.pauseAll();
      showToast('All active uploads paused', 'info');
    });
  }

  if (btnBatchResume) {
    btnBatchResume.addEventListener('click', () => {
      uploader.resumeAll();
      showToast('Resuming uploads...', 'info');
    });
  }

  if (btnBatchClear) {
    btnBatchClear.addEventListener('click', () => {
      uploader.clearCompleted();
      showToast('Completed tasks cleared', 'info');
    });
  }

  if (btnBatchCancel) {
    btnBatchCancel.addEventListener('click', () => {
      if (confirm('Cancel and stop all active uploads in the queue?')) {
        uploader.cancelAll();
        showToast('All uploads cancelled', 'warning');
      }
    });
  }

  // Connect Uploader with UI and Socket.IO
  uploader.onQueueChange((queue) => {
    renderQueue(queue);
    
    // Sync tab badge
    const tabUploaderBadge = document.getElementById('tabUploaderBadge');
    if (tabUploaderBadge) {
      tabUploaderBadge.textContent = queue.length;
    }

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

  // Rehydrate initial active tasks from backend
  fetch('/api/tasks')
    .then((res) => res.json())
    .then((tasks) => {
      if (Array.isArray(tasks)) {
        uploader.syncWithSnapshot(tasks);
      }
    })
    .catch((err) => console.debug('Could not pre-fetch tasks:', err));

  // Check Telegram auth status immediately
  checkAuthStatus();

  // Load initial upload history
  loadHistory();

  // Periodic auth & history check every 30s
  setInterval(() => {
    checkAuthStatus();
    loadHistory();
  }, 30000);

  // Register PWA Service Worker (Instant Load & Desktop App Support)
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').then((reg) => {
        console.log('TG Power Suite PWA Service Worker active:', reg.scope);
      }).catch((err) => {
        console.debug('Service Worker notice:', err);
      });
    });
  }

  // PWA Desktop / Mobile Install Prompt
  let deferredInstallPrompt = null;
  const btnPwaInstall = document.getElementById('btnPwaInstall');

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (btnPwaInstall) {
      btnPwaInstall.style.display = 'inline-flex';
    }
  });

  if (btnPwaInstall) {
    btnPwaInstall.addEventListener('click', async () => {
      if (!deferredInstallPrompt) {
        showToast('App is already installed or your browser handles installation in the address bar (➕)', 'info');
        return;
      }
      deferredInstallPrompt.prompt();
      const { outcome } = await deferredInstallPrompt.userChoice;
      if (outcome === 'accepted') {
        btnPwaInstall.style.display = 'none';
        showToast('TG Power Suite installed to your desktop!', 'success');
      }
      deferredInstallPrompt = null;
    });
  }

  window.addEventListener('appinstalled', () => {
    if (btnPwaInstall) btnPwaInstall.style.display = 'none';
    showToast('Welcome to TG Power Suite Desktop App!', 'success');
  });
}

// Bootstrap Application reliably
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
