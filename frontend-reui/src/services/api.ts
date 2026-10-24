import type {
  AuthStatus,
  ChatItem,
  ChatMessagesResponse,
  CinemaVideo,
  ContinueWatchingItem,
  SnifferFeedItem,
  SystemNetworkInfo,
  TelemetryStats,
  UploadHistoryItem,
} from '@/types';

class ApiService {
  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const res = await fetch(endpoint, {
      ...options,
      headers: {
        'Accept': 'application/json',
        ...(options.headers || {}),
      },
    });

    if (!res.ok) {
      let errorMsg = `HTTP Error ${res.status}`;
      try {
        const errorData = await res.json();
        if (errorData.detail) errorMsg = errorData.detail;
        else if (errorData.message) errorMsg = errorData.message;
      } catch {
        // Fallback
      }
      throw new Error(errorMsg);
    }

    return res.json() as Promise<T>;
  }

  // Auth endpoints
  async getAuthStatus(): Promise<AuthStatus> {
    return this.request<AuthStatus>('/api/auth/status');
  }

  async sendCode(phone: string): Promise<{ phone_code_hash: string; message?: string }> {
    return this.request('/api/auth/send-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone }),
    });
  }

  async signIn(phone: string, code: string, phone_code_hash: string): Promise<AuthStatus> {
    return this.request('/api/auth/sign-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, code, phone_code_hash }),
    });
  }

  async verify2FA(password: string): Promise<AuthStatus> {
    return this.request('/api/auth/2fa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
  }

  async logout(): Promise<{ success: boolean }> {
    return this.request('/api/auth/logout', { method: 'POST' });
  }

  // Chats endpoints
  async getChats(forceRefresh = false): Promise<ChatItem[]> {
    return this.request<ChatItem[]>(`/api/chats${forceRefresh ? '?force_refresh=true' : ''}`);
  }

  async getChatMessages(chatId: string | number, limit = 35, offsetId = 0): Promise<ChatMessagesResponse> {
    return this.request<ChatMessagesResponse>(`/api/chats/${encodeURIComponent(chatId)}/messages?limit=${limit}&offset_id=${offsetId}`);
  }

  async sendChatMessage(chatId: string | number, text: string): Promise<{ success: boolean; message_id?: number }> {
    return this.request(`/api/chats/${encodeURIComponent(chatId)}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  }

  // Upload endpoints
  async uploadFileDirect(formData: FormData): Promise<any> {
    return this.request('/api/upload', {
      method: 'POST',
      body: formData,
    });
  }

  async completeChunkUpload(payload: {
    upload_id: string;
    filename: string;
    total_size: number;
    chat_id: string | number;
    caption?: string;
    send_as?: string;
    silent?: boolean;
    auto_split?: boolean;
  }): Promise<any> {
    return this.request('/api/upload/chunk/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  async getChunkUploadStatus(
    uploadId: string,
    filename: string,
    chunkSize: number = 10 * 1024 * 1024
  ): Promise<{
    exists: boolean;
    received_bytes: number;
    next_chunk_index: number;
    total_chunks_received: number;
  }> {
    return this.request(
      `/api/upload/chunk/status?upload_id=${encodeURIComponent(uploadId)}&filename=${encodeURIComponent(filename)}&chunk_size=${chunkSize}`
    );
  }

  async updatePlaybackProgress(payload: {
    chat_id: string | number;
    message_id: number;
    filename: string;
    position_seconds: number;
    duration_seconds: number;
    thumbnail_url?: string;
    last_chunk_index?: number;
    last_byte_offset?: number;
  }): Promise<any> {
    return this.request('/api/media/playback_progress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  async pauseTask(taskId: string): Promise<any> {
    return this.request(`/api/tasks/${encodeURIComponent(taskId)}/pause`, { method: 'POST' });
  }

  async resumeTask(taskId: string): Promise<any> {
    return this.request(`/api/tasks/${encodeURIComponent(taskId)}/resume`, { method: 'POST' });
  }

  async cancelTask(taskId: string): Promise<any> {
    return this.request(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
  }

  async pauseAllUploads(): Promise<any> {
    return this.request('/api/upload/batch/pause', { method: 'POST' });
  }

  async resumeAllUploads(): Promise<any> {
    return this.request('/api/upload/batch/resume', { method: 'POST' });
  }

  async cancelAllUploads(): Promise<any> {
    return this.request('/api/upload/batch/cancel', { method: 'POST' });
  }

  async clearCompletedUploads(): Promise<any> {
    return this.request('/api/upload/batch/clear', { method: 'POST' });
  }

  async setSpeedLimit(limitMbS: number): Promise<any> {
    return this.request('/api/settings/speed_limit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speed_limit: limitMbS }),
    });
  }

  // History endpoints
  async getHistory(): Promise<UploadHistoryItem[]> {
    return this.request<UploadHistoryItem[]>('/api/history');
  }

  async clearHistory(): Promise<{ deleted: number }> {
    return this.request<{ deleted: number }>('/api/history/clear', { method: 'DELETE' });
  }

  // Sniffer endpoints
  async getSnifferStatus(): Promise<{
    active: boolean;
    active_channels: string[];
    detected_managers: Record<string, string>;
  }> {
    return this.request('/api/sniffer/status');
  }

  async getSnifferFeed(): Promise<SnifferFeedItem[]> {
    return this.request<SnifferFeedItem[]>('/api/sniffer/feed');
  }

  async addWatchedChannel(channel: string): Promise<{ success: boolean }> {
    return this.request('/api/sniffer/channels/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel }),
    });
  }

  async removeWatchedChannel(channel: string): Promise<{ success: boolean }> {
    return this.request('/api/sniffer/channels/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel }),
    });
  }

  async triggerFdmDownload(downloadUrl: string): Promise<any> {
    return this.request('/api/proxy/trigger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: downloadUrl }),
    });
  }

  // Cinema endpoints
  async getCinemaVideos(chatId: string | number, forceRefresh = false): Promise<{ videos: CinemaVideo[] }> {
    const url = `/api/media/videos/${encodeURIComponent(String(chatId))}${forceRefresh ? '?force_refresh=true&limit=250' : '?limit=250'}`;
    const res = await this.request<{ videos: any[] }>(url);
    const normalized = (res.videos || []).map((v) => {
      const sizeVal = Number(v.size ?? v.file_size ?? 0);
      const widthVal = Number(v.width ?? 0);
      const heightVal = Number(v.height ?? 0);
      let resStr = v.resolution;
      if (!resStr && heightVal > 0) {
        if (heightVal >= 2160 || widthVal >= 3840) resStr = '4K';
        else if (heightVal >= 1080 || widthVal >= 1920) resStr = '1080p';
        else if (heightVal >= 720 || widthVal >= 1280) resStr = '720p';
        else if (heightVal >= 480) resStr = '480p';
        else resStr = `${heightVal}p`;
      }
      return {
        ...v,
        size: sizeVal,
        file_size: sizeVal,
        resolution: resStr,
      } as CinemaVideo;
    });
    return { ...res, videos: normalized };
  }

  async playInVlc(payload: {
    chat_id: string | number;
    message_id: number;
    filename: string;
    stream_url?: string;
    player?: string;
    resume?: boolean;
    start_time?: number;
    network_caching?: number;
    audio_lang?: string;
    sub_lang?: string;
  }): Promise<{ launched: boolean; playlist_url?: string; resume_seconds?: number; has_subtitles?: boolean; network_caching?: number }> {
    return this.request('/api/media/vlc/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  async playBatchVlc(
    chatId: string | number,
    messageIds: number[],
    options?: { network_caching?: number; audio_lang?: string; sub_lang?: string }
  ): Promise<any> {
    return this.request('/api/media/vlc/play_batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        message_ids: messageIds,
        items: messageIds.map((mid) => ({ chat_id: String(chatId), message_id: mid })),
        network_caching: options?.network_caching,
        audio_lang: options?.audio_lang,
        sub_lang: options?.sub_lang,
      }),
    });
  }

  async getVlcLiveStatus(): Promise<{
    active: boolean;
    state?: string;
    time?: number;
    length?: number;
    volume?: number;
    volume_percent?: number;
    fullscreen?: boolean;
    rate?: number;
    filename?: string;
    chat_id?: string;
    message_id?: number;
    has_batch?: boolean;
    episode_index?: number;
    total_episodes?: number;
  }> {
    return this.request('/api/media/vlc/live_status');
  }

  async sendVlcControl(command: string, val?: string | number): Promise<any> {
    return this.request('/api/media/vlc/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command, val }),
    });
  }

  async getMobilePlaybackInfo(chatId: string | number, messageId: number, filename?: string): Promise<any> {
    const q = filename ? `?filename=${encodeURIComponent(filename)}` : '';
    return this.request(`/api/media/mobile_info/${encodeURIComponent(String(chatId))}/${messageId}${q}`);
  }

  async getContinueWatching(): Promise<ContinueWatchingItem[]> {
    return this.request<ContinueWatchingItem[]>('/api/media/continue_watching');
  }

  async removeContinueWatching(chatId: string | number, messageId: number): Promise<any> {
    return this.request(`/api/media/continue_watching/${encodeURIComponent(String(chatId))}/${messageId}`, {
      method: 'DELETE',
    });
  }

  async clearMediaCache(): Promise<{ success?: boolean; cleared?: boolean }> {
    return this.request('/api/media/cache/clear', { method: 'POST' });
  }

  async getMediaCacheStatus(): Promise<{ file_count?: number; total_bytes?: number; total_mb?: number; max_mb?: number; size_mb?: number }> {
    return this.request('/api/media/cache/status');
  }

  async getOfflineSaverStatus(): Promise<{ enabled: boolean }> {
    return this.request('/api/settings/offline_saver');
  }

  async toggleOfflineSaver(enabled: boolean): Promise<{ enabled: boolean }> {
    return this.request('/api/settings/offline_saver', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
  }

  // System & Telemetry endpoints
  async getSystemStats(): Promise<TelemetryStats> {
    return this.request<TelemetryStats>('/api/system/stats');
  }

  async getSystemNetwork(): Promise<SystemNetworkInfo> {
    return this.request<SystemNetworkInfo>('/api/system/network');
  }
}

export const api = new ApiService();
