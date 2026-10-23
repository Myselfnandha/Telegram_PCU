import { io, Socket } from 'socket.io-client';
import type { SnifferFeedItem, TelemetryStats } from '@/types';

type Listener<T> = (data: T) => void;

class SocketService {
  private socket: Socket | null = null;
  private connectionListeners = new Set<Listener<boolean>>();
  private telemetryListeners = new Set<Listener<TelemetryStats>>();
  private uploadProgressListeners = new Set<Listener<any>>();
  private queueSnapshotListeners = new Set<Listener<any[]>>();
  private snifferFeedListeners = new Set<Listener<SnifferFeedItem>>();
  private cinemaUpdateListeners = new Set<Listener<any>>();
  private floodWaitListeners = new Set<Listener<{ active: boolean; seconds: number; message?: string }>>();

  public isConnected = false;

  init() {
    if (this.socket) return;

    this.socket = io({
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      reconnectionAttempts: 30,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 20000,
    });

    this.socket.on('connect', () => {
      this.isConnected = true;
      this.connectionListeners.forEach((fn) => fn(true));
      this.socket?.emit('queue:get');
    });

    this.socket.on('disconnect', () => {
      this.isConnected = false;
      this.connectionListeners.forEach((fn) => fn(false));
    });

    this.socket.on('telemetry:stats', (data: TelemetryStats) => {
      this.telemetryListeners.forEach((fn) => fn(data));
    });

    this.socket.on('upload:progress', (data: any) => {
      this.uploadProgressListeners.forEach((fn) => fn(data));
    });

    this.socket.on('queue:snapshot', (tasks: any[]) => {
      this.queueSnapshotListeners.forEach((fn) => fn(tasks));
    });

    this.socket.on('sniffer:sniffer_feed', (item: SnifferFeedItem) => {
      this.snifferFeedListeners.forEach((fn) => fn(item));
    });

    this.socket.on('sniffer:feed_snapshot', (items: SnifferFeedItem[]) => {
      if (Array.isArray(items)) {
        items.forEach((item) => {
          this.snifferFeedListeners.forEach((fn) => fn(item));
        });
      }
    });

    this.socket.on('cinema:videos_updated', (data: any) => {
      this.cinemaUpdateListeners.forEach((fn) => fn(data));
    });

    this.socket.on('floodwait:status', (data: { active: boolean; seconds: number; message?: string }) => {
      this.floodWaitListeners.forEach((fn) => fn(data));
    });
  }

  onConnectionChange(fn: Listener<boolean>): () => void {
    this.connectionListeners.add(fn);
    fn(this.isConnected);
    return () => this.connectionListeners.delete(fn);
  }

  onTelemetry(fn: Listener<TelemetryStats>): () => void {
    this.telemetryListeners.add(fn);
    return () => this.telemetryListeners.delete(fn);
  }

  onUploadProgress(fn: Listener<any>): () => void {
    this.uploadProgressListeners.add(fn);
    return () => this.uploadProgressListeners.delete(fn);
  }

  onQueueSnapshot(fn: Listener<any[]>): () => void {
    this.queueSnapshotListeners.add(fn);
    return () => this.queueSnapshotListeners.delete(fn);
  }

  onSnifferFeed(fn: Listener<SnifferFeedItem>): () => void {
    this.snifferFeedListeners.add(fn);
    return () => this.snifferFeedListeners.delete(fn);
  }

  onCinemaUpdate(fn: Listener<any>): () => void {
    this.cinemaUpdateListeners.add(fn);
    return () => this.cinemaUpdateListeners.delete(fn);
  }

  onFloodWait(fn: Listener<{ active: boolean; seconds: number; message?: string }>): () => void {
    this.floodWaitListeners.add(fn);
    return () => this.floodWaitListeners.delete(fn);
  }

  requestQueueSnapshot() {
    this.socket?.emit('queue:get');
  }
}

export const socketService = new SocketService();
