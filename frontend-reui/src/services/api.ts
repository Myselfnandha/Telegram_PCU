import type {
  AuthStatus,
  SecondaryAuthStatus,
  QrStartResponse,
  QrStatusResponse,
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

  async startQrLogin(): Promise<QrStartResponse> {
    return this.request<QrStartResponse>('/api/auth/qr/start', { method: 'POST' });
  }

  async getQrStatus(): Promise<QrStatusResponse> {
    return this.request<QrStatusResponse>('/api/auth/qr/status');
  }

  async logout(): Promise<{ success: boolean }> {
    return this.request('/api/auth/logout', { method: 'POST' });
  }

  // Secondary Account (Multi-Account Turbo 2x Speed) endpoints
  async startSecondaryQrLogin(): Promise<QrStartResponse> {
    return this.request<QrStartResponse>('/api/auth/secondary/qr/start', { method: 'POST' });
  }

  async getSecondaryQrStatus(): Promise<QrStatusResponse> {
    return this.request<QrStatusResponse>('/api/auth/secondary/qr/status');
  }

  async getSecondaryAuthStatus(): Promise<SecondaryAuthStatus> {
    return this.request<SecondaryAuthStatus>('/api/auth/secondary/status');
  }

  async sendSecondaryCode(phone: string): Promise<{ phone_code_hash: string; message?: string }> {
    return this.request('/api/auth/secondary/send-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone }),
    });
  }

  async signInSecondary(phone: string, code: string, phone_code_hash: string): Promise<any> {
    return this.request('/api/auth/secondary/sign-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, code, phone_code_hash }),
    });
  }

  async verifySecondary2FA(password: string): Promise<any> {
    return this.request('/api/auth/secondary/2fa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
  }

  async importSecondarySessionString(sessionString: string): Promise<any> {
    return this.request('/api/auth/secondary/session-string', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_string: sessionString }),
    });
  }

  async logoutSecondary(): Promise<{ status: string }> {
    return this.request('/api/auth/secondary/logout', { method: 'POST' });
  }

  async toggleSecondaryTurbo(enabled: boolean): Promise<{ enabled: boolean }> {
    return this.request('/api/auth/secondary/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
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
      body: JSON.stringify({ limit_mb_s: limitMbS }),
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
      body: JSON.stringify({ url: downloadUrl, manager: 'fdm' }),
    });
  }

  async triggerFdm(chatId: string | number, messageId: number, filename?: string): Promise<{ success: boolean; manager?: string; url?: string }> {
    const encName = filename ? `/${encodeURIComponent(filename)}` : '';
    const dlUrl = `/dl/${encodeURIComponent(String(chatId))}/${messageId}${encName}`;
    return this.request('/api/proxy/trigger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        message_id: messageId,
        url: dlUrl,
        manager: 'fdm',
      }),
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

  async getMobilePlaybackInfo(chatId: string | number, messageId: number, filename?: string): Promise<{
    lan_ip?: string;
    lan_stream_url?: string;
    lan_qrcode_data_url?: string;
    lan_subtitles_url?: string;
    stream_url?: string;
    filename?: string;
    playback_token_expires_in_seconds?: number;
  }> {
    const q = filename ? `?filename=${encodeURIComponent(filename)}` : '';
    return this.request(`/api/media/mobile_info/${encodeURIComponent(String(chatId))}/${messageId}${q}`);
  }

  async launchVlc(chatId: string | number, messageId: number, filename?: string): Promise<{ success: boolean; pid?: number; message?: string }> {
    const q = filename ? `?filename=${encodeURIComponent(filename)}` : '';
    return this.request(`/api/media/vlc/launch/${encodeURIComponent(String(chatId))}/${messageId}${q}`, { method: 'POST' });
  }

  async launchVlcBatch(items: CinemaVideo[], title?: string): Promise<{ success: boolean; pid?: number; message?: string }> {
    return this.request('/api/media/vlc/launch_batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items, title: title || 'Season Binge' }),
    });
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
