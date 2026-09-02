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

let _targetScheduleTaskId = null;

function setupGlobalHooks() {
  window._app = {
    pause: (id) => uploader.pause(id),
    resume: (id) => uploader.resume(id),
    cancel: (id) => uploader.cancel(id),
    remove: (id) => uploader.remove(id),
    startNow: (id) => uploader.startNow(id),
    schedule: (id, ts) => uploader.scheduleTask(id, ts),
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

  // Schedule Modal Hooks
  window._openScheduleModal = function (taskId) {
    _targetScheduleTaskId = taskId;
    const modal = document.getElementById('scheduleModal');
    if (modal) {
      modal.classList.add('open', 'active');
      const input = document.getElementById('scheduleCustomInput');
      if (input) {
        // Set default to 1 hour from now
        const defaultDate = new Date(Date.now() + 3600 * 1000);
        defaultDate.setMinutes(defaultDate.getMinutes() - defaultDate.getTimezoneOffset());
        input.value = defaultDate.toISOString().slice(0, 16);
      }
    }
  };

  window._closeScheduleModal = function () {
    _targetScheduleTaskId = null;
    const modal = document.getElementById('scheduleModal');
    if (modal) modal.classList.remove('open', 'active');
  };

  // Night Mode Modal Hooks
  window._openNightModal = function () {
    const modal = document.getElementById('nightModeModal');
    if (modal) {
      modal.classList.add('open', 'active');
      fetch('/api/settings/night_mode')
        .then((r) => r.json())
        .then((data) => {
          const check = document.getElementById('nightModeToggleCheck');
          const startIn = document.getElementById('nightStartTime');
          const endIn = document.getElementById('nightEndTime');
          if (check) check.checked = Boolean(data.enabled);
          if (startIn && data.start_time) startIn.value = data.start_time;
          if (endIn && data.end_time) endIn.value = data.end_time;
        })
        .catch(() => {});
    }
  };

  window._closeNightModal = function () {
    const modal = document.getElementById('nightModeModal');
    if (modal) modal.classList.remove('open', 'active');
  };

  // Custom Speed Modal Hooks
  window._openCustomSpeedModal = function () {
    const modal = document.getElementById('customSpeedModal');
    if (modal) {
      modal.classList.add('open', 'active');
      const input = document.getElementById('customSpeedInput');
      if (input) input.focus();
    }
  };

  window._closeCustomSpeedModal = function () {
    const modal = document.getElementById('customSpeedModal');
    if (modal) modal.classList.remove('open', 'active');
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
  const speedLimitSelect = document.getElementById('speedLimitSelect');
  const btnToggleNightQueue = document.getElementById('btnToggleNightQueue');
  const nightModeStateLabel = document.getElementById('nightModeStateLabel');

  // Real-time Speed Limiter Controller
  if (speedLimitSelect) {
    // Initial fetch of speed limit from server
    fetch('/api/settings/speed_limit')
      .then((r) => r.json())
      .then((data) => {
        if (data.limit_mb_s !== undefined) {
          const valStr = String(data.limit_mb_s);
          const matchOption = Array.from(speedLimitSelect.options).find((opt) => opt.value === valStr);
          if (matchOption) {
            speedLimitSelect.value = valStr;
          } else if (data.limit_mb_s > 0) {
            speedLimitSelect.value = 'custom';
            speedLimitSelect.options[speedLimitSelect.options.length - 1].text = `⚙️ ${data.limit_mb_s} MB/s`;
          }
        }
      })
      .catch(() => {});

    speedLimitSelect.addEventListener('change', () => {
      const val = speedLimitSelect.value;
      if (val === 'custom') {
        window._openCustomSpeedModal();
      } else {
        const mb = parseFloat(val) || 0;
        uploader.setSpeedLimit(mb);
        if (mb > 0) {
          showToast(`⚡ Upload speed capped at ${mb} MB/s`, 'info');
        } else {
          showToast('⚡ Upload speed set to Unlimited (Gigabit/Fiber)', 'success');
        }
      }
    });
  }

  const btnApplyCustomSpeed = document.getElementById('btnApplyCustomSpeed');
  if (btnApplyCustomSpeed) {
    btnApplyCustomSpeed.addEventListener('click', () => {
      const input = document.getElementById('customSpeedInput');
      const val = parseFloat(input?.value) || 0;
      uploader.setSpeedLimit(val);
      if (speedLimitSelect) {
        if (val > 0) {
          speedLimitSelect.value = 'custom';
          speedLimitSelect.options[speedLimitSelect.options.length - 1].text = `⚙️ ${val} MB/s`;
          showToast(`⚡ Custom speed limit applied: ${val} MB/s`, 'info');
        } else {
          speedLimitSelect.value = '0';
          showToast('⚡ Custom speed set to Unlimited', 'success');
        }
      }
      window._closeCustomSpeedModal();
    });
  }

  // Night Mode Controller
  function syncNightModeUi(data) {
    if (nightModeStateLabel) {
      nightModeStateLabel.textContent = data.enabled ? `${data.start_time}-${data.end_time}` : 'Off';
    }
    if (btnToggleNightQueue) {
      btnToggleNightQueue.classList.toggle('active', Boolean(data.enabled));
    }
  }

  fetch('/api/settings/night_mode')
    .then((r) => r.json())
    .then((data) => syncNightModeUi(data))
    .catch(() => {});

  if (btnToggleNightQueue) {
    btnToggleNightQueue.addEventListener('click', () => {
      window._openNightModal();
    });
  }

  const btnSaveNightMode = document.getElementById('btnSaveNightMode');
  if (btnSaveNightMode) {
    btnSaveNightMode.addEventListener('click', async () => {
      const check = document.getElementById('nightModeToggleCheck');
      const startIn = document.getElementById('nightStartTime');
      const endIn = document.getElementById('nightEndTime');
      const enabled = check ? check.checked : false;
      const start_time = startIn ? startIn.value : '01:00';
      const end_time = endIn ? endIn.value : '06:00';

      await uploader.setNightMode({ enabled, start_time, end_time });
      syncNightModeUi({ enabled, start_time, end_time });
      window._closeNightModal();
      showToast(enabled ? `🌙 Night Mode Active (${start_time} - ${end_time})` : '☀️ Night Mode Disabled', 'info');
    });
  }

  // Schedule Modal Presets & Confirm Handler
  let _selectedScheduleTimestamp = null;
  const presetButtons = document.querySelectorAll('#scheduleModal .btn-time-preset');
  presetButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      presetButtons.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const preset = btn.getAttribute('data-preset');
      const now = new Date();
      if (preset === '30m') {
        _selectedScheduleTimestamp = (Date.now() + 30 * 60 * 1000) / 1000;
      } else if (preset === '1h') {
        _selectedScheduleTimestamp = (Date.now() + 60 * 60 * 1000) / 1000;
      } else if (preset === '2h') {
        _selectedScheduleTimestamp = (Date.now() + 2 * 3600 * 1000) / 1000;
      } else if (preset === '4h') {
        _selectedScheduleTimestamp = (Date.now() + 4 * 3600 * 1000) / 1000;
      } else if (preset === 'tonight') {
        const target = new Date();
        if (target.getHours() >= 2) target.setDate(target.getDate() + 1);
        target.setHours(2, 0, 0, 0);
        _selectedScheduleTimestamp = target.getTime() / 1000;
      } else if (preset === 'morning') {
        const target = new Date();
        if (target.getHours() >= 8) target.setDate(target.getDate() + 1);
        target.setHours(8, 0, 0, 0);
        _selectedScheduleTimestamp = target.getTime() / 1000;
      }

      const input = document.getElementById('scheduleCustomInput');
      if (input && _selectedScheduleTimestamp) {
        const d = new Date(_selectedScheduleTimestamp * 1000);
        d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
        input.value = d.toISOString().slice(0, 16);
      }
    });
  });

  const btnConfirmSchedule = document.getElementById('btnConfirmSchedule');
  if (btnConfirmSchedule) {
    btnConfirmSchedule.addEventListener('click', () => {
      const input = document.getElementById('scheduleCustomInput');
      if (input && input.value) {
        const parsed = new Date(input.value).getTime() / 1000;
        if (parsed > Date.now() / 1000) {
          _selectedScheduleTimestamp = parsed;
        }
      }

      if (!_selectedScheduleTimestamp || _selectedScheduleTimestamp <= Date.now() / 1000) {
        showToast('Please select a valid future time', 'warning');
        return;
      }

      if (_targetScheduleTaskId) {
        uploader.scheduleTask(_targetScheduleTaskId, _selectedScheduleTimestamp);
        const dt = new Date(_selectedScheduleTimestamp * 1000);
        showToast(`⏰ Upload scheduled for ${dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`, 'success');
      }
      window._closeScheduleModal();
    });
  }

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
