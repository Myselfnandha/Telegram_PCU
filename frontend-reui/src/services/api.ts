import type {
  AuthStatus,
  DeviceAuthStatus,
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
  private authErrorListeners = new Set<() => void>();

  constructor() {
    this.initUrlToken();
  }

  public onAuthRequired(fn: () => void) {
    this.authErrorListeners.add(fn);
    return () => this.authErrorListeners.delete(fn);
  }

  public getStoredToken(): string {
    try {
      const local = localStorage.getItem('tg_api_auth_token');
      if (local) return local.trim();
    } catch {}

    try {
      const session = sessionStorage.getItem('tg_api_auth_token');
      if (session) return session.trim();
    } catch {}

    // Fallback: parse client-accessible cookie if mobile storage partitioned
    try {
      if (typeof document !== 'undefined' && document.cookie) {
        const match = document.cookie.match(/(?:^|;\s*)tg_device_token=([^;]+)/);
        if (match && match[1]) return decodeURIComponent(match[1]).trim();
      }
    } catch {}

    return '';
  }

  public setStoredToken(token: string) {
    const clean = token.trim();
    try {
      if (clean) {
        localStorage.setItem('tg_api_auth_token', clean);
        sessionStorage.setItem('tg_api_auth_token', clean);
        if (typeof document !== 'undefined') {
          document.cookie = `tg_device_token=${encodeURIComponent(clean)}; path=/; max-age=31536000; SameSite=Lax`;
        }
      } else {
        localStorage.removeItem('tg_api_auth_token');
        sessionStorage.removeItem('tg_api_auth_token');
        if (typeof document !== 'undefined') {
          document.cookie = `tg_device_token=; path=/; max-age=0; SameSite=Lax`;
        }
      }
    } catch {}
  }

  public initUrlToken() {
    if (typeof window === 'undefined') return;
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const urlToken = urlParams.get('token') || urlParams.get('key');
      if (urlToken) {
        const cleanToken = urlToken.trim();
        this.setStoredToken(cleanToken);
        // Automatically establish authorized session cookie with backend
        this.deviceLogin(cleanToken).catch(() => {});
        urlParams.delete('token');
        urlParams.delete('key');
        const newSearch = urlParams.toString();
        const newUrl = window.location.pathname + (newSearch ? `?${newSearch}` : '') + window.location.hash;
        window.history.replaceState({}, '', newUrl);
      }
    } catch {}
  }

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const token = this.getStoredToken();
    const authHeaders: Record<string, string> = {};
    if (token) {
      authHeaders['Authorization'] = `Bearer ${token}`;
      authHeaders['X-API-Key'] = token;
    }

    const res = await fetch(endpoint, {
      ...options,
      credentials: 'include',
      headers: {
        'Accept': 'application/json',
        ...authHeaders,
        ...(options.headers || {}),
      },
    });

    if (res.status === 401 && !endpoint.includes('/api/auth/device/') && !endpoint.startsWith('/api/v3/')) {
      this.authErrorListeners.forEach((fn) => fn());
    }

    if (!res.ok) {
      let errorMsg = `HTTP Error ${res.status}`;
      try {
        const errorData = await res.json();
        if (Array.isArray(errorData.detail)) {
          errorMsg = errorData.detail
            .map((d: any) => {
              const field = d.loc ? d.loc[d.loc.length - 1] : '';
              return field ? `${field}: ${d.msg}` : d.msg;
            })
            .join('; ');
        } else if (typeof errorData.detail === 'string') {
          errorMsg = errorData.detail;
        } else if (errorData.message) {
          errorMsg = errorData.message;
        }
      } catch {
        // Fallback
      }
      throw new Error(errorMsg);
    }

    return res.json() as Promise<T>;
  }

  // Device Auth endpoints
  async getDeviceAuthStatus(): Promise<DeviceAuthStatus> {
    return this.request<DeviceAuthStatus>('/api/auth/device/status');
  }

  async deviceLogin(token: string): Promise<{ success: boolean; message: string; token?: string }> {
    const res = await this.request<{ success: boolean; message: string; token?: string }>('/api/auth/device/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (res.success && token) {
      this.setStoredToken(token);
    }
    return res;
  }

  async deviceLogout(): Promise<{ success: boolean; message: string }> {
    this.setStoredToken('');
    return this.request('/api/auth/device/logout', { method: 'POST' });
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

  async clearSnifferFeed(): Promise<{ success: boolean }> {
    return this.request<{ success: boolean }>('/api/sniffer/feed/clear', { method: 'POST' });
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

  async prewarmMedia(chatId: string | number, messageId: number): Promise<{ status: string }> {
    return this.request(`/api/media/prewarm/${encodeURIComponent(String(chatId))}/${messageId}`);
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

  // ==================== TG Power Suite v3.0 Multi-Tenant API ====================

  async v3Register(payload: {
    username: string;
    password?: string;
    device_name?: string;
    device_fingerprint?: string;
    bootstrap_token?: string;
  }): Promise<import('@/types').V3RegisterResponse> {
    const res = await this.request<import('@/types').V3RegisterResponse>('/api/v3/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res?.session_token) {
      this.setStoredToken(res.session_token);
    }
    return res;
  }

  async v3Login(payload: {
    username: string;
    password: string;
    device_name?: string;
    device_fingerprint?: string;
  }): Promise<import('@/types').V3LoginResponse> {
    const res = await this.request<import('@/types').V3LoginResponse>('/api/v3/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res?.session_token) {
      this.setStoredToken(res.session_token);
    }
    return res;
  }

  async v3Logout(): Promise<void> {
    try {
      await this.request('/api/v3/auth/logout', { method: 'POST' });
    } finally {
      this.setStoredToken('');
    }
  }

  async v3GetMe(): Promise<import('@/types').V3UserProfile> {
    return this.request<import('@/types').V3UserProfile>('/api/v3/auth/me');
  }

  async v3GetDevices(): Promise<import('@/types').V3DeviceInfo[]> {
    return this.request<import('@/types').V3DeviceInfo[]>('/api/v3/devices');
  }

  async v3ApproveDevice(deviceId: string, challengeCode?: string): Promise<{ message: string }> {
    return this.request(`/api/v3/devices/${encodeURIComponent(deviceId)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge_code: challengeCode }),
    });
  }

  async v3RevokeDevice(deviceId: string): Promise<{ message: string }> {
    return this.request(`/api/v3/devices/${encodeURIComponent(deviceId)}/revoke`, {
      method: 'POST',
    });
  }

  async v3StartLinkAttempt(role: 'primary' | 'turbo' = 'primary'): Promise<import('@/types').V3LinkAttempt> {
    return this.request<import('@/types').V3LinkAttempt>('/api/v3/telegram/link-attempts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    });
  }

  async v3GetLinkAttempt(attemptId: string): Promise<import('@/types').V3LinkAttempt> {
    return this.request<import('@/types').V3LinkAttempt>(`/api/v3/telegram/link-attempts/${encodeURIComponent(attemptId)}`);
  }

  async v3Submit2FA(attemptId: string, password: string): Promise<{ message: string }> {
    return this.request(`/api/v3/telegram/link-attempts/${encodeURIComponent(attemptId)}/2fa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
  }

  async v3CancelLinkAttempt(attemptId: string): Promise<{ message: string }> {
    return this.request(`/api/v3/telegram/link-attempts/${encodeURIComponent(attemptId)}/cancel`, {
      method: 'POST',
    });
  }

  async v3GetAccounts(): Promise<import('@/types').V3TelegramAccount[]> {
    return this.request<import('@/types').V3TelegramAccount[]>('/api/v3/telegram/accounts');
  }

  async v3UnlinkAccount(accountId: string): Promise<{ message: string }> {
    return this.request(`/api/v3/telegram/accounts/${encodeURIComponent(accountId)}`, {
      method: 'DELETE',
    });
  }

  async v3SetPrimaryAccount(accountId: string): Promise<{ message: string }> {
    return this.request(`/api/v3/telegram/accounts/${encodeURIComponent(accountId)}/set-primary`, {
      method: 'POST',
    });
  }

  async v3GetPendingDevices(): Promise<import('@/types').V3PendingDevice[]> {
    return this.request<import('@/types').V3PendingDevice[]>('/api/v3/devices/pending');
  }

  async v3GetTurboStatus(): Promise<import('@/types').V3TurboStatus> {
    return this.request<import('@/types').V3TurboStatus>('/api/v3/turbo/status');
  }

  async v3SetTurboMode(mode: string): Promise<{ status: string; mode: string }> {
    return this.request<{ status: string; mode: string }>('/api/v3/turbo/mode', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    });
  }

  async v3IssueGrant(chatId: string | number, messageId: number, operation: string = 'stream'): Promise<import('@/types').V3PlaybackGrant> {
    return this.request<import('@/types').V3PlaybackGrant>('/api/v3/media/grants', {
      method: 'POST',
      body: JSON.stringify({ chat_id: String(chatId), message_id: messageId, operation }),
    });
  }

  async v3RevokeGrant(grantId: string): Promise<{ status: string; grant_id: string }> {
    return this.request(`/api/v3/media/grants/${encodeURIComponent(grantId)}`, {
      method: 'DELETE',
    });
  }
}

export const api = new ApiService();
