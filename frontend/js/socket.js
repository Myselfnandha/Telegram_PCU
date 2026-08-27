/**
 * Socket.IO Real-time Connection Manager.
 * Handles auto-reconnect, event throttling, and state re-synchronization.
 */

class SocketManager {
  constructor() {
    this.socket = null;
    this.progressListeners = new Set();
    this.queueListeners = new Set();
    this.connectionListeners = new Set();
    this.isConnected = false;
  }

  init() {
    // Check if io client is available (loaded via CDN/bundle)
    if (typeof io === 'undefined') {
      console.error('Socket.IO client library is not loaded!');
      return;
    }

    // Connect to same host
    this.socket = io({
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      reconnectionAttempts: 20,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 20000
    });

    this.socket.on('connect', () => {
      console.log('[WS] Connected to backend Socket.IO. ID:', this.socket.id);
      this.isConnected = true;
      this._notifyConnection(true);
      // Request active queue snapshot immediately upon connection/reconnection
      this.socket.emit('queue:get');
    });

    this.socket.on('disconnect', (reason) => {
      console.warn('[WS] Disconnected from backend Socket.IO. Reason:', reason);
      this.isConnected = false;
      this._notifyConnection(false);
    });

    this.socket.on('upload:progress', (data) => {
      this.progressListeners.forEach((fn) => {
        try {
          fn(data);
        } catch (e) {
          console.error('Error in progress listener:', e);
        }
      });
    });

    this.socket.on('queue:snapshot', (tasks) => {
      this.queueListeners.forEach((fn) => {
        try {
          fn(tasks);
        } catch (e) {
          console.error('Error in queue snapshot listener:', e);
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
    // Initial call
    callback(this.isConnected);
    return () => this.connectionListeners.delete(callback);
  }

  _notifyConnection(state) {
    this.connectionListeners.forEach((fn) => fn(state));
  }

  pauseTask(taskId) {
    if (this.socket && this.isConnected) {
      this.socket.emit('upload_pause', { id: taskId });
    }
  }

  resumeTask(taskId) {
    if (this.socket && this.isConnected) {
      this.socket.emit('upload_resume', { id: taskId });
    }
  }

  cancelTask(taskId) {
    if (this.socket && this.isConnected) {
      this.socket.emit('upload_cancel', { id: taskId });
    }
  }
}

export const socketManager = new SocketManager();
