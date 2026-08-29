/**
 * Network Watchdog & Auto-Recovery Engine.
 * Actively monitors network connectivity and backend health.
 * Automatically pauses transfers during network blips and seamlessly auto-resumes when online.
 */

import { showToast } from './ui.js';
import { uploader } from './uploader.js';
import { socketManager } from './socket.js';

class NetworkWatchdog {
  constructor() {
    this.isOnline = navigator.onLine !== false;
    this.wasInterrupted = false;
    this.interruptedTasks = new Set();
    this.heartbeatTimer = null;
    this.bannerElem = null;
    this.consecutiveFailures = 0;
  }

  init() {
    this._createBanner();
    this._setupListeners();
    this._startHeartbeat();
    console.log('Network Watchdog & Auto-Recovery initialized.');
  }

  _createBanner() {
    let banner = document.getElementById('networkWatchdogBanner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'networkWatchdogBanner';
      banner.className = 'watchdog-banner hidden';
      banner.title = 'Click to test connection and resume immediately';
      banner.style.cursor = 'pointer';
      banner.addEventListener('click', () => {
        this._probeAndRecover();
      });
      document.body.prepend(banner);
    }
    this.bannerElem = banner;
  }

  _setupListeners() {
    window.addEventListener('online', () => {
      console.log('[NetworkWatchdog] Browser online event fired.');
      this._handleOnline();
    });

    window.addEventListener('offline', () => {
      console.warn('[NetworkWatchdog] Browser offline event fired.');
      this._handleOffline();
    });
  }

  async _probeAndRecover() {
    try {
      const res = await fetch('/api/auth/status', { cache: 'no-store' });
      if (res.ok) {
        this._handleOnline();
        showToast('Connection verified active!', 'success');
      }
    } catch {
      showToast('Backend still reconnecting...', 'info');
    }
  }

  _startHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(async () => {
      // If browser OS itself is offline, handle immediately
      if (navigator.onLine === false) {
        if (this.isOnline) this._handleOffline('Device Offline');
        return;
      }

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 6000);
        
        const res = await fetch('/api/auth/status', {
          method: 'GET',
          signal: controller.signal,
          cache: 'no-store'
        });
        clearTimeout(timeoutId);

        if (res.ok) {
          if (!this.isOnline) {
            this._handleOnline();
          }
          this.consecutiveFailures = 0;
        } else {
          this.consecutiveFailures++;
          if (this.consecutiveFailures >= 4 && this.isOnline) {
            this._handleOffline('Backend Server Unreachable');
          }
        }
      } catch (e) {
        this.consecutiveFailures++;
        if (this.consecutiveFailures >= 4 && this.isOnline) {
          this._handleOffline('Connection Interrupted');
        }
      }
    }, 6000);
  }

  _handleOffline(reason = 'Network Offline') {
    this.isOnline = false;
    this.wasInterrupted = true;

    // Identify and auto-pause currently uploading/streaming tasks
    uploader.queue.forEach((task) => {
      if (task.status === 'uploading' || task.status === 'streaming' || task.status === 'splitting') {
        this.interruptedTasks.add(task.id);
        uploader.pause(task.id);
      }
    });

    this._showBanner(`⚠️ ${reason} — Pausing active transfers safely. Waiting for reconnection...`, 'offline');
    showToast('Network connection lost. Uploads paused safely.', 'warning');
  }

  _handleOnline() {
    this.isOnline = true;
    this.consecutiveFailures = 0;

    if (this.wasInterrupted) {
      this._showBanner('⚡ Connection Restored — Auto-resuming upload queue...', 'online');
      showToast('Back online! Auto-resuming upload queue...', 'success');

      // Reconnect socket if disconnected
      socketManager.init();

      // Auto-resume all interrupted tasks after 1s stabilization delay
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
      }, 1000);
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
    this.bannerElem.className = 'watchdog-banner hidden';
  }
}

export const networkWatchdog = new NetworkWatchdog();
