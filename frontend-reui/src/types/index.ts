export type TabType = 'uploader' | 'sniffer' | 'history' | 'cinema' | 'settings';

export interface ChatItem {
  id: number | string;
  name: string;
  username?: string | null;
  type: 'saved_messages' | 'channel' | 'supergroup' | 'group' | 'user' | 'bot';
  unread_count?: number;
  pinned?: boolean;
  photo_url?: string | null;
  has_avatar?: boolean;
}

export interface ChatMessageMedia {
  type: 'video' | 'photo' | 'document';
  file_name?: string;
  file_size?: number;
  duration?: number | null;
  mime_type?: string;
  stream_url?: string;
  thumb_url?: string;
  download_url?: string;
}

export interface ChatMessage {
  id: number;
  date: string | null;
  text: string;
  out: boolean;
  sender_name: string;
  sender_id?: number | string | null;
  media?: ChatMessageMedia | null;
  views?: number | null;
  forwards?: number | null;
}

export interface ChatMessagesResponse {
  chat_id: string;
  count: number;
  messages: ChatMessage[];
}

export interface AuthStatus {
  authenticated: boolean;
  phone?: string | null;
  first_name?: string | null;
  username?: string | null;
  user_id?: number | null;
  dc_id?: number | null;
}

export interface TelemetryStats {
  cpu_percent?: number;
  ram_percent?: number;
  disk_free_gb?: number;
  disk_total_gb?: number;
  upload_speed_bytes?: number;
  download_speed_bytes?: number;
  active_transfers?: number;
  timestamp?: number;
  latency_ms?: number;
  connections_count?: number;
}

export interface UploadTask {
  id: string;
  file?: File;
  filename: string;
  customFilename?: string;
  fileSize: number;
  chatId: string | number;
  chatName: string;
  caption?: string;
  sendAs?: 'auto' | 'document' | 'media';
  status: 'queued' | 'scheduled' | 'streaming' | 'uploading' | 'splitting' | 'paused' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  uploadedBytes: number;
  speed: number;
  eta: number;
  currentPart: number;
  totalParts: number;
  error?: string | null;
  previewUrl?: string | null;
  transferredToServer?: boolean;
  isTransferring?: boolean;
  uploadedChunkIndex?: number;
  scheduled_at?: number | null;
  category?: 'video' | 'audio' | 'photo' | 'archive' | 'document';
}

export interface SnifferChannel {
  channel: string;
  name?: string;
  handle?: string;
  icon?: string;
}

export interface SnifferFeedItem {
  id: string;
  chat_id: string | number;
  chat_title?: string;
  message_id: number;
  filename: string;
  size?: number;
  size_formatted?: string;
  manager?: string;
  status: string;
  download_url?: string;
  stream_url?: string;
  date?: string;
  mime_type?: string;
}

export interface CinemaVideo {
  chat_id: string | number;
  message_id: number;
  filename: string;
  title?: string;
  size: number;
  file_size?: number;
  duration?: number;
  width?: number;
  height?: number;
  resolution?: string;
  series_name?: string;
  season?: number;
  episode?: number;
  thumb_url?: string;
  stream_url?: string;
  date?: string | number;
  mime_type?: string;
  has_subtitles?: boolean;
  playback_seconds?: number;
}

export interface ContinueWatchingItem {
  chat_id: string | number;
  message_id: number;
  filename: string;
  playback_seconds: number;
  total_seconds?: number;
  percent?: number;
  last_watched?: string;
  thumb_url?: string;
}

export interface UploadHistoryItem {
  id: string;
  filename: string;
  file_size: number;
  mime_type?: string | null;
  chat_id: string;
  chat_name?: string | null;
  message_id?: number | null;
  telegram_file_id?: string | null;
  caption?: string | null;
  send_as?: string | null;
  status: 'completed' | 'failed' | 'cancelled' | string;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
  parts_count: number;
}

export interface SystemNetworkInfo {
  local_ip?: string;
  port?: number;
  latency_ms?: number;
  gateway?: string;
  online?: boolean;
}

export type ThemeType = 'cyber-emerald' | 'dark' | 'light';
