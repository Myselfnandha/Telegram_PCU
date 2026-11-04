import React, { useEffect, useState, useMemo, useRef } from 'react';
import { useApp } from '@/context/AppContext';
import type { CinemaVideo, ContinueWatchingItem } from '@/types';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';
import { cleanFileName, formatBytes, formatDuration, parseMediaInfo } from '@/lib/formatters';
import { scrollRightSideToTop } from '@/lib/scrollUtils';
import { ChatPickerModal } from '@/components/ChatPickerModal';
import { DockedChannelPicker } from '@/components/DockedChannelPicker';
import { VideoPlayerModal } from '@/components/VideoPlayerModal';
import { MobileQrModal } from '@/components/MobileQrModal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  ArrowDownUp,
  Calendar,
  Check,
  CheckSquare,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clapperboard,
  Copy,
  Download,
  ExternalLink,
  Film,
  Grid3x3,
  HardDrive,
  LayoutGrid,
  List,
  MessageSquare,
  MoreVertical,
  Play,
  PlayCircle,
  QrCode,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Tv,
  X,
} from 'lucide-react';
import {
  draftWebSeriesBySeason,
  formatMediaDate,
  normalizeDateToSeconds,
  type WebSeriesSeason,
  type SeriesSortOption,
  type EpisodeSortOption,
} from '@/lib/seriesDraftEngine';
import {
  draftMoviesByFranchiseAndYear,
  type MovieCollectionDraft,
  type MovieSortOption,
} from '@/lib/movieDraftEngine';

/**
 * Differentiates videos by calendar date for timeline break lines in an infinite feed.
 */
function getDateGroupInfo(dateVal?: number | string): { key: string; label: string; subLabel?: string } {
  const sec = normalizeDateToSeconds(dateVal);
  if (!sec || sec <= 0) {
    return { key: 'undated', label: 'Archive / Undated', subLabel: 'Older uploads' };
  }

  const date = new Date(sec * 1000);
  const now = new Date();

  const isSameDay = (d1: Date, d2: Date) =>
    d1.getFullYear() === d2.getFullYear() &&
    d1.getMonth() === d2.getMonth() &&
    d1.getDate() === d2.getDate();

  const yesterday = new Date();
  yesterday.setDate(now.getDate() - 1);

  const dayKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

  if (isSameDay(date, now)) {
    return {
      key: dayKey,
      label: 'Today',
      subLabel: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
    };
  }
  if (isSameDay(date, yesterday)) {
    return {
      key: dayKey,
      label: 'Yesterday',
      subLabel: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
    };
  }

  const label = date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  });

  return {
    key: dayKey,
    label,
    subLabel: date.getFullYear() !== now.getFullYear() ? `${date.getFullYear()}` : undefined,
  };
}

/**
 * Generates an array of page numbers with ellipsis (-1) for responsive pagination.
 */
function generatePagePills(current: number, total: number): number[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  if (current <= 4) {
    return [1, 2, 3, 4, 5, -1, total];
  }
  if (current >= total - 3) {
    return [1, -1, total - 4, total - 3, total - 2, total - 1, total];
  }
  return [1, -1, current - 1, current, current + 1, -1, total];
}

export const CinemaView: React.FC = () => {
  const { selectedChat, setSelectedChat, addToast } = useApp();
  const [videos, setVideos] = useState<CinemaVideo[]>([]);
  const [continueWatching, setContinueWatching] = useState<ContinueWatchingItem[]>([]);
  const [offlineSaver, setOfflineSaver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'movies' | 'series' | 'hd'>('all');
  const [sortBy, setSortBy] = useState<'newest' | 'oldest' | 'size' | 'title'>('newest');
  const [viewMode, setViewMode] = useState<'large' | 'dense' | 'files'>(() => {
    const saved = localStorage.getItem('cinema_view_mode');
    if (saved === 'files') return 'files';
    if (saved === 'large') return 'large';
    return 'dense';
  });
  const [isContinueWatchingOpen, setContinueWatchingOpen] = useState(true);
  const [isChannelDockOpen, setChannelDockOpen] = useState<boolean>(() => {
    const saved = localStorage.getItem('cinema_channel_dock_open');
    return saved !== 'false';
  });
  const [isChatPickerOpen, setChatPickerOpen] = useState(false);
  const [previewVideo, setPreviewVideo] = useState<CinemaVideo | null>(null);
  const [qrModalVideo, setQrModalVideo] = useState<CinemaVideo | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Web Series Draft States
  const [seriesSortMode, setSeriesSortMode] = useState<SeriesSortOption>('date-desc');
  const [episodeSortMode, setEpisodeSortMode] = useState<EpisodeSortOption>('episode-asc');
  const [activeSeasons, setActiveSeasons] = useState<Record<string, number>>({});
  const [expandedSeries, setExpandedSeries] = useState<Record<string, boolean>>({});
  const [seriesViewStyle, setSeriesViewStyle] = useState<'split' | 'grouped' | 'flat'>('split');
  const [selectedShowId, setSelectedShowId] = useState<string>('');

  // Movie Draft States
  const [movieSortMode, setMovieSortMode] = useState<MovieSortOption>('date-desc');
  const [movieCollectionType, setMovieCollectionType] = useState<'all' | 'franchise' | 'year'>('all');
  const [movieViewStyle, setMovieViewStyle] = useState<'split' | 'bundled' | 'flat'>('split');
  const [selectedMovieCollectionId, setSelectedMovieCollectionId] = useState<string>('');
  const [expandedMovieCollections, setExpandedMovieCollections] = useState<Record<string, boolean>>({});

  // Centered Draggable Filter Navigation State
  const navDragRef = useRef<HTMLDivElement>(null);
  const [isNavDragging, setIsNavDragging] = useState(false);
  const navDragState = useRef({
    isDown: false,
    startX: 0,
    scrollLeft: 0,
    hasMoved: false,
  });

  // Client-side pagination to eliminate DOM bloat (e.g. 23,000 down to ~1,500 nodes)
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(() => {
    const saved = localStorage.getItem('cinema_page_size');
    return saved ? Number(saved) : 24;
  });

  const handleSetPageSize = (size: number) => {
    setPageSize(size);
    setCurrentPage(1);
    localStorage.setItem('cinema_page_size', String(size));
  };

  const handleSetViewMode = (mode: 'large' | 'dense' | 'files') => {
    setViewMode(mode);
    localStorage.setItem('cinema_view_mode', mode);
  };

  const loadVideos = async (forceRefresh = false) => {
    setLoading(true);
    try {
      const res = await api.getCinemaVideos(selectedChat.id, forceRefresh);
      setVideos(res.videos || []);
      scrollRightSideToTop(false);
    } catch (err: any) {
      console.debug('Failed to load videos:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadContinueWatching = async () => {
    try {
      const items = await api.getContinueWatching();
      setContinueWatching(items || []);
    } catch (e) {
      console.debug('Failed to load continue watching:', e);
    }
  };

  const loadOfflineSaver = async () => {
    try {
      const res = await api.getOfflineSaverStatus();
      setOfflineSaver(Boolean(res.enabled));
    } catch {}
  };

  const toggleOfflineSaver = async () => {
    const nextState = !offlineSaver;
    setOfflineSaver(nextState);
    try {
      await api.toggleOfflineSaver(nextState);
      addToast({
        type: 'info',
        title: 'Offline Auto-Saver',
        message: nextState
          ? '💾 Full videos will auto-save to ~/Videos/Telegram_Cinema'
          : 'Offline auto-saver disabled.',
      });
    } catch (err: any) {
      setOfflineSaver(!nextState);
      addToast({ type: 'error', title: 'Error', message: err.message });
    }
  };

  useEffect(() => {
    loadVideos(false);
    loadContinueWatching();
    loadOfflineSaver();
    setSelectedIds(new Set());
    scrollRightSideToTop(true);

    const unsubCinema = socketService.onCinemaUpdate((data) => {
      if (data && String(data.chat_id) === String(selectedChat.id) && Array.isArray(data.videos)) {
        setVideos(data.videos);
      }
    });

    return () => {
      unsubCinema();
    };
  }, [selectedChat.id]);

  const handleExportPlaylist = async () => {
    if (selectedIds.size === 0) return;
    const selectedVideos = filteredVideos.filter((v) => selectedIds.has(v.message_id));
    try {
      addToast({ type: 'info', title: 'Preparing VLC playlist', message: 'Creating short-lived, media-scoped stream links…' });
      const playbackInfo = await Promise.all(
        selectedVideos.map((video) => api.getMobilePlaybackInfo(video.chat_id, video.message_id, video.filename))
      );
      const lines = ['#EXTM3U'];
      const cachingVal = Number(localStorage.getItem('tg_network_caching') || 1500);
      const audioLang = localStorage.getItem('tg_audio_lang') || 'tam,eng,hin,tel,mal,kan,all';
      const subLang = localStorage.getItem('tg_sub_lang') || 'eng,tam,hin,all';

      selectedVideos.forEach((video, index) => {
        const info = playbackInfo[index];
        const streamUrl = info.lan_ip && info.lan_ip !== '127.0.0.1' ? info.lan_stream_url : info.stream_url;
        if (!streamUrl) return;
        lines.push(`#EXTINF:-1,${cleanFileName(video.filename)}`);
        lines.push(`#EXTVLCOPT:network-caching=${cachingVal}`);
        lines.push(`#EXTVLCOPT:file-caching=${cachingVal}`);
        lines.push('#EXTVLCOPT:http-reconnect');
        lines.push('#EXTVLCOPT:no-spdif');
        lines.push('#EXTVLCOPT:input-fast-seek');
        lines.push('#EXTVLCOPT:avcodec-hw=any');
        lines.push(`#EXTVLCOPT:sub-language=${subLang}`);
        lines.push(`#EXTVLCOPT:audio-language=${audioLang}`);
        if (info.lan_subtitles_url) {
          lines.push(`#EXTVLCOPT:input-slave=${info.lan_subtitles_url}`);
          lines.push(`#EXTVLCOPT:sub-file=${info.lan_subtitles_url}`);
        }
        lines.push(streamUrl);
      });
      const blob = new Blob([lines.join('\n')], { type: 'audio/x-mpegurl' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${selectedChat.name || 'Playlist'}.m3u`;
      a.click();
      URL.revokeObjectURL(url);
      addToast({
        type: 'success',
        title: 'Playlist Exported',
        message: `Created VLC playlist with ${selectedVideos.length} media-scoped links. Links expire after four hours.`,
      });
    } catch (error) {
      addToast({ type: 'error', title: 'Playlist export failed', message: error instanceof Error ? error.message : 'Could not prepare VLC stream links.' });
    }
  };

  const toggleSelectVideo = (messageId: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(messageId)) next.delete(messageId);
      else next.add(messageId);
      return next;
    });
  };

  const handleSelectAll = () => {
    const allIds = filteredVideos.map((v) => v.message_id);
    const allSelected = allIds.length > 0 && allIds.every((id) => selectedIds.has(id));

    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        allIds.forEach((id) => next.delete(id));
      } else {
        allIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const handleToggleSelectDateGroup = (groupVideos: CinemaVideo[]) => {
    const groupIds = groupVideos.map((v) => v.message_id);
    const allGroupSelected = groupIds.length > 0 && groupIds.every((id) => selectedIds.has(id));

    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allGroupSelected) {
        groupIds.forEach((id) => next.delete(id));
      } else {
        groupIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const handleSelectEntireArchive = () => {
    if (selectedIds.size === filteredVideos.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredVideos.map((v) => v.message_id)));
    }
  };

  const handleCopyStreamLink = (video: CinemaVideo) => {
    const url = `${window.location.origin}/stream/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`;
    navigator.clipboard.writeText(url);
    addToast({
      type: 'success',
      title: 'Stream URL Copied',
      message: 'Direct media stream URL copied to clipboard.',
    });
  };

  const handlePlayInVlc = async (video: CinemaVideo) => {
    try {
      if (previewVideo) {
        setPreviewVideo(null);
      }
      const result = await api.launchVlc(video.chat_id, video.message_id, video.filename);
      addToast({ type: 'success', title: 'VLC launched', message: result.message || `Playing ${video.filename} in VLC.` });
    } catch (error) {
      addToast({
        type: 'error',
        title: 'Could not launch VLC',
        message: error instanceof Error ? error.message : 'Check that VLC is installed on the TG Power Suite desktop.',
      });
    }
  };

  const handleTriggerFdm = async (video: CinemaVideo) => {
    try {
      const res = await api.triggerFdm(video.chat_id, video.message_id, video.filename);
      if (res && res.success) {
        addToast({
          type: 'success',
          title: 'Dispatched to FDM',
          message: `Sent "${cleanFileName(video.filename)}" to Free Download Manager!`,
        });
      } else {
        const fullUrl = `${window.location.origin}/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`;
        await navigator.clipboard.writeText(fullUrl);
        addToast({
          type: 'warning',
          title: 'FDM Auto-Launch Unavailable',
          message: 'Direct media download link copied to clipboard for FDM import.',
        });
      }
    } catch {
      const fullUrl = `${window.location.origin}/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`;
      await navigator.clipboard.writeText(fullUrl).catch(() => {});
      addToast({
        type: 'warning',
        title: 'FDM Auto-Launch Unavailable',
        message: 'Direct media download link copied to clipboard.',
      });
    }
  };

  const handleBatchFdm = async () => {
    const selectedVideos = filteredVideos.filter((v) => selectedIds.has(v.message_id));
    if (selectedVideos.length === 0) return;
    let dispatched = 0;
    for (const v of selectedVideos) {
      try {
        await api.triggerFdm(v.chat_id, v.message_id, v.filename);
        dispatched++;
      } catch {}
    }
    if (dispatched > 0) {
      addToast({
        type: 'success',
        title: 'Batch FDM Dispatch',
        message: `Dispatched ${dispatched} of ${selectedVideos.length} streams to Free Download Manager.`,
      });
    } else {
      addToast({
        type: 'warning',
        title: 'Batch FDM Notice',
        message: 'Could not trigger local FDM automatically. Consider exporting .M3U playlist.',
      });
    }
  };

  const handleRemoveContinueWatching = async (item: ContinueWatchingItem) => {
    try {
      await api.removeContinueWatching(item.chat_id, item.message_id);
      setContinueWatching((prev) =>
        prev.filter((c) => !(String(c.chat_id) === String(item.chat_id) && c.message_id === item.message_id))
      );
    } catch {}
  };

  // Web Series Season Binge Action
  const handleBingeSeason = async (showTitle: string, season: WebSeriesSeason) => {
    if (!season.episodes.length) return;
    try {
      addToast({
        type: 'info',
        title: `Launching ${showTitle}`,
        message: `Queuing ${season.episodes.length} episodes of ${season.seasonLabel} in VLC...`,
      });
      const videoItems = season.episodes.map((e) => e.video);
      const res = await api.launchVlcBatch(videoItems, `${showTitle} - ${season.seasonLabel}`);
      if (res.success) {
        addToast({
          type: 'success',
          title: 'VLC Season Binge Started',
          message: res.message || `Playing ${season.episodes.length} episodes in VLC Player!`,
        });
      }
    } catch (err: any) {
      addToast({
        type: 'error',
        title: 'Playback Error',
        message: err.message || 'Check that desktop VLC is running on TG Power Suite.',
      });
    }
  };

  // Web Series Season M3U Export Action
  const handleDownloadSeasonM3u = async (showTitle: string, season: WebSeriesSeason) => {
    if (!season.episodes.length) return;
    try {
      addToast({
        type: 'info',
        title: 'Preparing Season Playlist',
        message: `Creating media stream links for ${season.episodes.length} episodes...`,
      });
      const videoItems = season.episodes.map((e) => e.video);
      const playbackInfo = await Promise.all(
        videoItems.map((v) => api.getMobilePlaybackInfo(v.chat_id, v.message_id, v.filename))
      );
      const lines = ['#EXTM3U'];
      const cachingVal = Number(localStorage.getItem('tg_network_caching') || 1500);
      const audioLang = localStorage.getItem('tg_audio_lang') || 'tam,eng,hin,tel,mal,kan,all';
      const subLang = localStorage.getItem('tg_sub_lang') || 'eng,tam,hin,all';

      videoItems.forEach((v, idx) => {
        const info = playbackInfo[idx];
        const streamUrl = info.lan_ip && info.lan_ip !== '127.0.0.1' ? info.lan_stream_url : info.stream_url;
        if (!streamUrl) return;
        lines.push(`#EXTINF:-1,${cleanFileName(v.filename)}`);
        lines.push(`#EXTVLCOPT:network-caching=${cachingVal}`);
        lines.push(`#EXTVLCOPT:file-caching=${cachingVal}`);
        lines.push('#EXTVLCOPT:http-reconnect');
        lines.push('#EXTVLCOPT:no-spdif');
        lines.push('#EXTVLCOPT:input-fast-seek');
        lines.push('#EXTVLCOPT:avcodec-hw=any');
        lines.push(`#EXTVLCOPT:sub-language=${subLang}`);
        lines.push(`#EXTVLCOPT:audio-language=${audioLang}`);
        if (info.lan_subtitles_url) {
          lines.push(`#EXTVLCOPT:input-slave=${info.lan_subtitles_url}`);
          lines.push(`#EXTVLCOPT:sub-file=${info.lan_subtitles_url}`);
        }
        lines.push(streamUrl);
      });

      const blob = new Blob([lines.join('\n')], { type: 'audio/x-mpegurl' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${showTitle}_${season.seasonLabel}.m3u`;
      a.click();
      URL.revokeObjectURL(url);

      addToast({
        type: 'success',
        title: 'Season Playlist Downloaded',
        message: `Exported ${season.episodes.length} episodes to ${showTitle}_${season.seasonLabel}.m3u`,
      });
    } catch (err: any) {
      addToast({
        type: 'error',
        title: 'Playlist Export Failed',
        message: err.message || 'Could not prepare season playlist.',
      });
    }
  };

  // Toggle selection of all episodes in a season
  const handleSelectSeason = (season: WebSeriesSeason) => {
    const epIds = season.episodes.map((e) => e.video.message_id);
    const allSelected = epIds.length > 0 && epIds.every((id) => selectedIds.has(id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        epIds.forEach((id) => next.delete(id));
      } else {
        epIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const toggleExpandSeries = (showId: string) => {
    setExpandedSeries((prev) => ({
      ...prev,
      [showId]: prev[showId] === undefined ? false : !prev[showId],
    }));
  };

  // Movie Collection Binge Action
  const handleBingeMovieCollection = async (collection: MovieCollectionDraft) => {
    if (!collection.movies.length) return;
    try {
      addToast({
        type: 'info',
        title: `Launching ${collection.collectionTitle}`,
        message: `Queuing ${collection.movies.length} movies in VLC...`,
      });
      const videoItems = collection.movies.map((m) => m.video);
      const res = await api.launchVlcBatch(videoItems, collection.collectionTitle);
      if (res.success) {
        addToast({
          type: 'success',
          title: 'VLC Movie Binge Started',
          message: res.message || `Playing ${collection.movies.length} movies in VLC Player!`,
        });
      }
    } catch (err: any) {
      addToast({
        type: 'error',
        title: 'Playback Error',
        message: err.message || 'Check that desktop VLC is running on TG Power Suite.',
      });
    }
  };

  // Movie Collection M3U Export Action
  const handleDownloadMovieCollectionM3u = async (collection: MovieCollectionDraft) => {
    if (!collection.movies.length) return;
    try {
      addToast({
        type: 'info',
        title: 'Preparing Movie Playlist',
        message: `Creating media stream links for ${collection.movies.length} movies...`,
      });
      const videoItems = collection.movies.map((m) => m.video);
      const playbackInfo = await Promise.all(
        videoItems.map((v) => api.getMobilePlaybackInfo(v.chat_id, v.message_id, v.filename))
      );
      const lines = ['#EXTM3U'];
      const cachingVal = Number(localStorage.getItem('tg_network_caching') || 1500);
      const audioLang = localStorage.getItem('tg_audio_lang') || 'tam,eng,hin,tel,mal,kan,all';
      const subLang = localStorage.getItem('tg_sub_lang') || 'eng,tam,hin,all';

      videoItems.forEach((v, idx) => {
        const info = playbackInfo[idx];
        const streamUrl = info.lan_ip && info.lan_ip !== '127.0.0.1' ? info.lan_stream_url : info.stream_url;
        if (!streamUrl) return;
        lines.push(`#EXTINF:-1,${cleanFileName(v.filename)}`);
        lines.push(`#EXTVLCOPT:network-caching=${cachingVal}`);
        lines.push(`#EXTVLCOPT:file-caching=${cachingVal}`);
        lines.push('#EXTVLCOPT:http-reconnect');
        lines.push('#EXTVLCOPT:no-spdif');
        lines.push('#EXTVLCOPT:input-fast-seek');
        lines.push('#EXTVLCOPT:avcodec-hw=any');
        lines.push(`#EXTVLCOPT:sub-language=${subLang}`);
        lines.push(`#EXTVLCOPT:audio-language=${audioLang}`);
        if (info.lan_subtitles_url) {
          lines.push(`#EXTVLCOPT:input-slave=${info.lan_subtitles_url}`);
          lines.push(`#EXTVLCOPT:sub-file=${info.lan_subtitles_url}`);
        }
        lines.push(streamUrl);
      });

      const blob = new Blob([lines.join('\n')], { type: 'audio/x-mpegurl' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${collection.collectionTitle.replace(/\s+/g, '_')}.m3u`;
      a.click();
      URL.revokeObjectURL(url);

      addToast({
        type: 'success',
        title: 'Movie Playlist Downloaded',
        message: `Exported ${collection.movies.length} movies to ${collection.collectionTitle.replace(/\s+/g, '_')}.m3u`,
      });
    } catch (err: any) {
      addToast({
        type: 'error',
        title: 'Playlist Export Failed',
        message: err.message || 'Could not prepare movie playlist.',
      });
    }
  };

  // Toggle selection of all movies in a collection
  const handleSelectMovieCollection = (collection: MovieCollectionDraft) => {
    const movieIds = collection.movies.map((m) => m.video.message_id);
    const allSelected = movieIds.length > 0 && movieIds.every((id) => selectedIds.has(id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        movieIds.forEach((id) => next.delete(id));
      } else {
        movieIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const toggleExpandMovieCollection = (collectionId: string) => {
    setExpandedMovieCollections((prev) => ({
      ...prev,
      [collectionId]: prev[collectionId] === undefined ? false : !prev[collectionId],
    }));
  };

  // Draggable Filter Navigation Mouse Handlers
  const handleNavMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = navDragRef.current;
    if (!el) return;
    navDragState.current = {
      isDown: true,
      startX: e.pageX - el.offsetLeft,
      scrollLeft: el.scrollLeft,
      hasMoved: false,
    };
    setIsNavDragging(true);
  };

  const handleNavMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const state = navDragState.current;
    if (!state.isDown || !navDragRef.current) return;
    const x = e.pageX - navDragRef.current.offsetLeft;
    const walk = (x - state.startX) * 1.5;
    if (Math.abs(walk) > 4) {
      state.hasMoved = true;
    }
    navDragRef.current.scrollLeft = state.scrollLeft - walk;
  };

  const handleNavMouseUp = () => {
    navDragState.current.isDown = false;
    setIsNavDragging(false);
  };

  const handleNavMouseLeave = () => {
    navDragState.current.isDown = false;
    setIsNavDragging(false);
  };

  // Automatic Movie Drafting & Grouping by Franchise & Release Year
  const {
    franchises: movieFranchises,
    yearCollections: movieYearCollections,
  } = useMemo(() => {
    return draftMoviesByFranchiseAndYear(videos, {
      sortBy: movieSortMode,
      filterQuery: search,
    });
  }, [videos, movieSortMode, search]);

  const activeMovieCollections = useMemo(() => {
    if (movieCollectionType === 'franchise') return movieFranchises;
    if (movieCollectionType === 'year') return movieYearCollections;
    return [...movieFranchises, ...movieYearCollections];
  }, [movieFranchises, movieYearCollections, movieCollectionType]);

  const activeSelectedMovieCollection = useMemo(() => {
    if (!activeMovieCollections.length) return null;
    return activeMovieCollections.find((c) => c.id === selectedMovieCollectionId) || activeMovieCollections[0];
  }, [activeMovieCollections, selectedMovieCollectionId]);

  // Automatic Web Series Drafting & Grouping by Season
  const { drafts: webSeriesDrafts } = useMemo(() => {
    return draftWebSeriesBySeason(videos, {
      seriesSortBy: seriesSortMode,
      episodeSortBy: episodeSortMode,
      filterQuery: search,
    });
  }, [videos, seriesSortMode, episodeSortMode, search]);

  const activeSelectedShow = useMemo(() => {
    if (!webSeriesDrafts.length) return null;
    return webSeriesDrafts.find((d) => d.id === selectedShowId) || webSeriesDrafts[0];
  }, [webSeriesDrafts, selectedShowId]);

  // Sorting and Filtering with accurate Date support
  const filteredVideos = useMemo(() => {
    return videos
      .filter((v) => {
        const matchesSearch = v.filename.toLowerCase().includes(search.toLowerCase());
        if (!matchesSearch) return false;

        if (filter === 'series') return Boolean(v.is_series || v.series_name || v.season || v.episode);
        if (filter === 'movies') return !v.is_series && !v.series_name && !v.season && !v.episode;
        if (filter === 'hd') return v.resolution?.includes('1080') || v.resolution?.includes('4K') || v.resolution?.includes('2160');

        return true;
      })
      .sort((a, b) => {
        const timeA = normalizeDateToSeconds(a.date) || a.message_id;
        const timeB = normalizeDateToSeconds(b.date) || b.message_id;
        if (sortBy === 'newest') return timeB - timeA;
        if (sortBy === 'oldest') return timeA - timeB;
        if (sortBy === 'size') return (b.size || b.file_size || 0) - (a.size || a.file_size || 0);
        if (sortBy === 'title') return a.filename.localeCompare(b.filename);
        return 0;
      });
  }, [videos, search, filter, sortBy]);

  // Windowed pagination slice
  const totalPages = pageSize > 0 ? Math.ceil(filteredVideos.length / pageSize) || 1 : 1;
  const paginatedVideos = useMemo(() => {
    if (pageSize <= 0) return filteredVideos;
    const start = (currentPage - 1) * pageSize;
    return filteredVideos.slice(start, start + pageSize);
  }, [filteredVideos, currentPage, pageSize]);

  const handlePageChange = (newPage: number) => {
    const clamped = Math.max(1, Math.min(newPage, totalPages));
    setCurrentPage(clamped);
    const catalogEl = document.getElementById('cinema-files-catalog');
    if (catalogEl) {
      catalogEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  const handleSelectAllCurrentPage = () => {
    const pageIds = paginatedVideos.map((v) => v.message_id);
    const allSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  // Infinite timeline grouping differentiated by date with break lines
  const dateGroups = useMemo(() => {
    interface DateGroup {
      key: string;
      label: string;
      subLabel?: string;
      videos: CinemaVideo[];
      totalSize: number;
    }
    const groups: DateGroup[] = [];
    const map = new Map<string, DateGroup>();

    filteredVideos.forEach((video) => {
      const info = getDateGroupInfo(video.date);
      let g = map.get(info.key);
      if (!g) {
        g = {
          key: info.key,
          label: info.label,
          subLabel: info.subLabel,
          videos: [],
          totalSize: 0,
        };
        map.set(info.key, g);
        groups.push(g);
      }
      g.videos.push(video);
      g.totalSize += (video.size || video.file_size || 0);
    });

    return groups;
  }, [filteredVideos]);

  const hasContinueWatching = continueWatching.length > 0;
  const showContinueWatching = hasContinueWatching && isContinueWatchingOpen;

  const watchProgressMap = useMemo(() => {
    const map = new Map<string, ContinueWatchingItem>();
    continueWatching.forEach((item) => {
      map.set(`${item.chat_id}_${item.message_id}`, item);
    });
    return map;
  }, [continueWatching]);

  const handleStartPlay = (v: CinemaVideo) => {
    const prog = watchProgressMap.get(`${v.chat_id}_${v.message_id}`);
    const resumeSec = prog ? (prog.playback_seconds || prog.last_position_seconds || 0) : (v.playback_seconds || 0);
    setPreviewVideo({
      ...v,
      playback_seconds: resumeSec,
    });
  };

  return (
    <div id="cinema-view-container" className="w-full px-4 sm:px-6 lg:px-8 xl:px-10 py-6 space-y-6 animate-in fade-in duration-300">
      {/* Top Header & Archive Controls */}
      <div className="p-5 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-md shadow-sm space-y-4">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0 shadow-inner">
              <Clapperboard className="w-5 h-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-foreground flex items-center gap-2">
                <span>Cinema Video Catalog</span>
                <Badge variant="secondary" className="text-[10px]">
                  {videos.length} Videos
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                Browsing channel:{' '}
                <button
                  onClick={() => {
                    if (!isChannelDockOpen) {
                      setChannelDockOpen(true);
                      localStorage.setItem('cinema_channel_dock_open', 'true');
                    }
                  }}
                  className="font-medium text-foreground hover:underline cursor-pointer"
                >
                  {selectedChat.name}
                </button>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2.5 flex-wrap">
            {hasContinueWatching && (
              <Button
                variant={showContinueWatching ? 'secondary' : 'default'}
                size="sm"
                onClick={() => setContinueWatchingOpen(!isContinueWatchingOpen)}
                className="h-9 gap-1.5 cursor-pointer text-xs"
              >
                <PlayCircle className="w-3.5 h-3.5 text-amber-400" />
                <span>Resume ({continueWatching.length})</span>
              </Button>
            )}

            <Button
              variant={isChannelDockOpen ? 'secondary' : 'default'}
              size="sm"
              onClick={() => {
                const next = !isChannelDockOpen;
                setChannelDockOpen(next);
                localStorage.setItem('cinema_channel_dock_open', String(next));
              }}
              className="h-9 gap-1.5 cursor-pointer text-xs"
            >
              <MessageSquare className="w-3.5 h-3.5 text-primary" />
              <span>Channels {isChannelDockOpen ? '(Docked)' : ''}</span>
            </Button>

            <Button
              variant="secondary"
              size="sm"
              onClick={() => loadVideos(true)}
              disabled={loading}
              className="h-9 text-xs cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
              Sync Archive
            </Button>

            {/* Offline saver toggle */}
            <div className="flex items-center gap-2 px-3 py-1 rounded-lg border border-border bg-background text-xs">
              <HardDrive className="w-3.5 h-3.5 text-muted-foreground" />
              <span className="text-muted-foreground">Auto-Saver:</span>
              <Switch checked={offlineSaver} onCheckedChange={toggleOfflineSaver} aria-label="Toggle auto-offline video saver" />
            </div>
          </div>
        </div>

        {/* Centered Draggable Filter Navigation Bar */}
        <div className="flex items-center justify-center w-full pt-2.5 pb-1 border-t border-border/40">
          <div
            ref={navDragRef}
            onMouseDown={handleNavMouseDown}
            onMouseMove={handleNavMouseMove}
            onMouseUp={handleNavMouseUp}
            onMouseLeave={handleNavMouseLeave}
            role="tablist"
            aria-label="Video category filters"
            className={`inline-flex items-center gap-1.5 p-1 rounded-full bg-muted/40 border border-border/60 shadow-xs backdrop-blur-md overflow-x-auto scrollbar-none select-none max-w-full touch-pan-x transition-colors ${
              isNavDragging ? 'cursor-grabbing select-none' : 'cursor-grab'
            }`}
          >
            {(
              [
                { id: 'all', label: 'All Videos' },
                { id: 'movies', label: 'Movies' },
                { id: 'series', label: 'Series / Episodes' },
                { id: 'hd', label: '1080p & 4K' },
              ] as const
            ).map((tab) => {
              const isActive = filter === tab.id;
              return (
                <button
                  key={tab.id}
                  role="tab"
                  aria-selected={isActive}
                  onClick={(e) => {
                    if (navDragState.current.hasMoved) {
                      e.preventDefault();
                      return;
                    }
                    setFilter(tab.id);
                  }}
                  className={`text-xs px-4 py-1.5 rounded-full transition-all shrink-0 select-none ${
                    isActive
                      ? 'bg-foreground text-background font-bold shadow-sm'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/60 font-medium'
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Search, Sort, and View Mode Sub-Toolbar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-1">
          <div className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
            <span>
              {filter === 'movies'
                ? `${movieFranchises.length} Franchises • ${movieYearCollections.length} Year Archives`
                : filter === 'series'
                ? `${webSeriesDrafts.length} Web Series`
                : `${filteredVideos.length} Videos Available`}
            </span>
          </div>

          <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap">
            {/* View Mode Toggle: Large Grid vs Dense Grid vs Files Table */}
            <div role="group" aria-label="View display mode" className="flex items-center p-0.5 rounded-lg border border-border bg-background">
              <button
                onClick={() => handleSetViewMode('large')}
                aria-label="Switch to large grid layout"
                aria-pressed={viewMode === 'large'}
                className={`p-1.5 rounded-md transition-all cursor-pointer ${
                  viewMode === 'large'
                    ? 'bg-primary text-primary-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title="Large Poster Grid"
              >
                <LayoutGrid className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => handleSetViewMode('dense')}
                aria-label="Switch to dense grid layout"
                aria-pressed={viewMode === 'dense'}
                className={`p-1.5 rounded-md transition-all cursor-pointer ${
                  viewMode === 'dense'
                    ? 'bg-primary text-primary-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title="Dense Media Grid"
              >
                <Grid3x3 className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => handleSetViewMode('files')}
                aria-label="Switch to files table layout"
                aria-pressed={viewMode === 'files'}
                className={`p-1.5 rounded-md transition-all cursor-pointer ${
                  viewMode === 'files'
                    ? 'bg-primary text-primary-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title="Files Explorer Table"
              >
                <List className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Sort Dropdown */}
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border bg-background text-xs">
              <ArrowDownUp className="w-3.5 h-3.5 text-muted-foreground" />
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as any)}
                aria-label="Sort videos by"
                className="bg-transparent text-xs font-medium focus:outline-none cursor-pointer"
              >
                <option value="newest" className="bg-card text-foreground">Date: Newest First</option>
                <option value="oldest" className="bg-card text-foreground">Date: Oldest First</option>
                <option value="size" className="bg-card text-foreground">Largest Size</option>
                <option value="title" className="bg-card text-foreground">Title (A-Z)</option>
              </select>
            </div>

            {/* Search Input */}
            <div className="relative w-full sm:w-56">
              <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                placeholder="Search titles..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                aria-label="Search video archive titles"
                className="pl-8 h-8 text-xs"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Floating Batch Selection Toolbar */}
      {selectedIds.size > 0 && (
        <div className="sticky top-20 z-30 flex items-center justify-between p-3.5 rounded-2xl bg-card/95 border border-primary/50 shadow-2xl backdrop-blur-xl animate-in slide-in-from-top-2">
          <div className="flex items-center gap-2">
            <CheckSquare className="w-4 h-4 text-primary" />
            <span className="text-xs font-semibold text-foreground">
              {selectedIds.size} video(s) selected
            </span>
          </div>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={handleExportPlaylist}
              className="h-8 text-xs gap-1.5 cursor-pointer"
              title="Download .M3U playlist file for VLC or other external players"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Export .M3U</span>
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={handleBatchFdm}
              className="h-8 text-xs gap-1.5 cursor-pointer text-emerald-400 hover:text-emerald-300 border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-500/20"
              title="Send selected videos to Free Download Manager"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              <span>FDM Download</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSelectedIds(new Set())}
              className="h-8 text-xs cursor-pointer"
            >
              Clear Selection
            </Button>
          </div>
        </div>
      )}

      {/* Main Layout Area */}
      <div className="flex flex-col lg:flex-row gap-6 items-start w-full">
        {/* Left Side Docked Channel Picker */}
        {isChannelDockOpen && (
          <DockedChannelPicker
            selectedChatId={selectedChat.id}
            onSelect={setSelectedChat}
            onClose={() => {
              setChannelDockOpen(false);
              localStorage.setItem('cinema_channel_dock_open', 'false');
            }}
          />
        )}

        {/* Video Catalog (Expands to full remaining width) */}
        <div id="cinema-files-catalog" className="flex-1 min-w-0 w-full space-y-5">
          {/* Top Horizontal Continue Watching Shelf */}
          {showContinueWatching && (
            <div className="p-4 rounded-2xl border border-border/70 bg-card/75 backdrop-blur-xl shadow-sm space-y-3">
              <div className="flex items-center justify-between pb-2 border-b border-border/40">
                <div className="flex items-center gap-2">
                  <PlayCircle className="w-4 h-4 text-amber-400" />
                  <h3 className="text-xs font-semibold text-foreground">Continue Watching</h3>
                  <Badge variant="outline" className="text-[10px] h-4 px-1.5 border-amber-500/30 text-amber-400 bg-amber-500/10 font-mono">
                    {continueWatching.length}
                  </Badge>
                </div>
                <button
                  onClick={() => setContinueWatchingOpen(false)}
                  className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 py-0.5 rounded-md hover:bg-muted/50 transition-colors cursor-pointer"
                  title="Hide Continue Watching shelf"
                >
                  <X className="w-3.5 h-3.5" />
                  <span>Hide Shelf</span>
                </button>
              </div>

              {/* Horizontal Scroll Shelf */}
              <div className="flex items-center gap-3 overflow-x-auto pb-2 scrollbar-thin scroll-smooth">
                {continueWatching.map((item) => {
                  const media = parseMediaInfo(item.filename);
                  const seconds = item.playback_seconds || item.last_position_seconds || 0;
                  const total = item.total_seconds || item.duration_seconds || 0;
                  const pct = item.percent !== undefined 
                    ? item.percent 
                    : (item.progress_percent !== undefined 
                        ? Math.round(item.progress_percent) 
                        : (total > 0 ? Math.round((seconds / total) * 100) : 0));
                  const thumbUrl = item.thumb_url || item.thumbnail_url || `/stream/${encodeURIComponent(String(item.chat_id))}/${item.message_id}/thumb`;

                  const startItemPlay = () => {
                    setPreviewVideo({
                      chat_id: item.chat_id,
                      message_id: item.message_id,
                      filename: item.filename,
                      size: (item as any).file_size || 0,
                      duration: total,
                      thumb_url: thumbUrl,
                      playback_seconds: seconds,
                    });
                  };

                  return (
                    <div
                      key={`${item.chat_id}_${item.message_id}`}
                      className="w-72 sm:w-80 shrink-0 p-2 rounded-xl border border-border/60 bg-background/50 hover:bg-accent/40 hover:border-border transition-all flex items-center gap-2.5 group relative shadow-xs"
                    >
                      {/* Compact Poster with Play Overlay and Progress Bar */}
                      <div
                        onClick={startItemPlay}
                        className="relative w-24 h-15 shrink-0 rounded-lg overflow-hidden bg-zinc-950/90 border border-border/50 cursor-pointer shadow-sm group/thumb"
                        title="Resume playback"
                      >
                        <img
                          src={thumbUrl}
                          alt=""
                          loading="lazy"
                          className="w-full h-full object-cover group-hover/thumb:scale-105 transition-transform"
                          onError={(e) => {
                            (e.currentTarget as HTMLElement).style.display = 'none';
                          }}
                        />
                        <div className="absolute inset-0 bg-black/25 group-hover/thumb:bg-black/45 flex items-center justify-center transition-colors">
                          <Play className="w-3.5 h-3.5 text-white fill-current opacity-85 group-hover/thumb:opacity-100 group-hover/thumb:scale-110 transition-all drop-shadow" />
                        </div>
                        <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/70">
                          <div
                            className="bg-amber-400 h-full rounded-r transition-all"
                            style={{ width: `${Math.max(5, pct)}%` }}
                          />
                        </div>
                      </div>

                      {/* Info & Action */}
                      <div className="flex-1 min-w-0 flex flex-col justify-between h-full py-0.5">
                        <div>
                          <div
                            className="text-xs font-semibold text-foreground truncate cursor-pointer hover:text-primary transition-colors leading-tight"
                            title={item.filename}
                            onClick={startItemPlay}
                          >
                            {media.cleanTitle}
                          </div>
                          <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground font-mono mt-1">
                            <span>{formatDuration(seconds)}</span>
                            <span className="text-muted-foreground/40">•</span>
                            <span className="text-amber-400 font-semibold">{pct}%</span>
                          </div>
                        </div>

                        <div className="flex items-center gap-1.5 mt-1.5">
                          <Button
                            size="sm"
                            variant="default"
                            onClick={startItemPlay}
                            className="h-6 px-2 text-[10px] gap-1 cursor-pointer font-medium bg-primary text-primary-foreground hover:opacity-90"
                          >
                            <Play className="w-2.5 h-2.5 fill-current" />
                            <span>Resume</span>
                          </Button>
                          <button
                            onClick={() => handleRemoveContinueWatching(item)}
                            className="w-5 h-5 rounded-md text-muted-foreground/60 hover:text-destructive hover:bg-destructive/10 flex items-center justify-center transition-colors cursor-pointer ml-auto"
                            title="Dismiss from history"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Dedicated Movies Showcase (active when filter === 'movies') */}
          {filter === 'movies' && !loading && (
            <div className="space-y-4">
              {/* Movies Toolbar */}
              <div className="p-3.5 rounded-2xl border border-border/70 bg-card/70 backdrop-blur-md flex flex-col md:flex-row items-start md:items-center justify-between gap-3 shadow-xs">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-xl bg-primary/15 text-primary flex items-center justify-center shrink-0 shadow-inner">
                    <Film className="w-4 h-4" />
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-foreground flex items-center gap-2">
                      <span>Drafted Movie Collections</span>
                      <Badge variant="secondary" className="text-[10px] font-mono px-2 py-0">
                        {movieFranchises.length} Franchises • {movieYearCollections.length} Year Archives
                      </Badge>
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      Franchises, sagas, and chronological archives with 1-click VLC binge playback.
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2.5 flex-wrap">
                  {/* Collection Sub-Filter Tabs */}
                  <div className="flex items-center p-0.5 rounded-lg border border-border bg-background text-xs">
                    <button
                      onClick={() => setMovieCollectionType('all')}
                      className={`px-2 py-1 rounded-md transition-all cursor-pointer ${
                        movieCollectionType === 'all'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      All ({movieFranchises.length + movieYearCollections.length})
                    </button>
                    <button
                      onClick={() => setMovieCollectionType('franchise')}
                      className={`px-2 py-1 rounded-md transition-all cursor-pointer ${
                        movieCollectionType === 'franchise'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Franchises ({movieFranchises.length})
                    </button>
                    <button
                      onClick={() => setMovieCollectionType('year')}
                      className={`px-2 py-1 rounded-md transition-all cursor-pointer ${
                        movieCollectionType === 'year'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Years ({movieYearCollections.length})
                    </button>
                  </div>

                  {/* Movie Sort Mode */}
                  <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border bg-background text-xs">
                    <ArrowDownUp className="w-3.5 h-3.5 text-muted-foreground" />
                    <span className="text-muted-foreground font-medium">Sort:</span>
                    <select
                      value={movieSortMode}
                      onChange={(e) => setMovieSortMode(e.target.value as MovieSortOption)}
                      aria-label="Sort Movie Collections By"
                      className="bg-transparent text-xs font-medium focus:outline-none cursor-pointer"
                    >
                      <option value="date-desc" className="bg-card text-foreground">Date: Newest First</option>
                      <option value="date-asc" className="bg-card text-foreground">Date: Oldest First</option>
                      <option value="year-desc" className="bg-card text-foreground">Release Year: Newest First</option>
                      <option value="year-asc" className="bg-card text-foreground">Release Year: Oldest First</option>
                      <option value="title-asc" className="bg-card text-foreground">Title (A-Z)</option>
                      <option value="size-desc" className="bg-card text-foreground">Largest Size</option>
                    </select>
                  </div>

                  {/* View Style Switcher: Split vs Bundled vs Flat */}
                  <div className="flex items-center p-0.5 rounded-lg border border-border bg-background text-xs">
                    <button
                      onClick={() => setMovieViewStyle('split')}
                      className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                        movieViewStyle === 'split'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Split View
                    </button>
                    <button
                      onClick={() => setMovieViewStyle('bundled')}
                      className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                        movieViewStyle === 'bundled'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Bundled
                    </button>
                    <button
                      onClick={() => setMovieViewStyle('flat')}
                      className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                        movieViewStyle === 'flat'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Flat Movies
                    </button>
                  </div>
                </div>
              </div>

              {/* Split View for Movies */}
              {movieViewStyle === 'split' && (
                activeMovieCollections.length === 0 ? (
                  <div className="p-12 text-center rounded-2xl border border-dashed border-border/80 bg-card/20 flex flex-col items-center justify-center gap-2">
                    <Film className="w-10 h-10 text-muted-foreground/40 mb-1" />
                    <p className="text-sm font-semibold text-foreground">No Movie Collections Found</p>
                    <p className="text-xs text-muted-foreground max-w-sm">
                      No matching movie franchises or release year groupings found in this channel.
                    </p>
                  </div>
                ) : (
                  <div className="flex flex-col lg:flex-row gap-5 items-start w-full">
                    {/* Left Pane: Movie Collections List */}
                    <div className="w-full lg:w-72 shrink-0 space-y-2 p-3 rounded-2xl border border-border/70 bg-card/75 backdrop-blur-md shadow-xs">
                      <div className="flex items-center justify-between pb-2 border-b border-border/40 px-1">
                        <span className="text-xs font-semibold text-foreground">
                          {movieCollectionType === 'franchise'
                            ? `Franchises (${activeMovieCollections.length})`
                            : movieCollectionType === 'year'
                            ? `Years (${activeMovieCollections.length})`
                            : `Collections (${activeMovieCollections.length})`}
                        </span>
                        <span className="text-[10px] text-muted-foreground font-mono">Select Collection</span>
                      </div>
                      <div className="space-y-1.5 max-h-[640px] overflow-y-auto scrollbar-thin pr-1">
                        {activeMovieCollections.map((collection) => {
                          const isSelected = activeSelectedMovieCollection?.id === collection.id;
                          return (
                            <button
                              key={collection.id}
                              onClick={() => setSelectedMovieCollectionId(collection.id)}
                              className={`w-full text-left p-2.5 rounded-xl border transition-all cursor-pointer flex items-center gap-2.5 ${
                                isSelected
                                  ? 'border-primary bg-primary/10 ring-1 ring-primary/40 text-foreground'
                                  : 'border-border/60 hover:bg-muted/40 text-muted-foreground hover:text-foreground'
                              }`}
                            >
                              <div className="w-10 h-10 rounded-lg overflow-hidden bg-zinc-950 border border-border/60 shrink-0 flex items-center justify-center">
                                {collection.leadThumbUrl ? (
                                  <img src={collection.leadThumbUrl} alt="" className="w-full h-full object-cover" />
                                ) : (
                                  <Clapperboard className="w-5 h-5 text-primary/50" />
                                )}
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="text-xs font-semibold truncate text-foreground leading-snug">
                                  {collection.collectionTitle}
                                </div>
                                <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground mt-0.5 font-mono">
                                  <span>{collection.totalMovies} {collection.totalMovies === 1 ? 'Movie' : 'Movies'}</span>
                                  <span>•</span>
                                  <span>{formatBytes(collection.totalBytes)}</span>
                                  {collection.latestDateFormatted && (
                                    <>
                                      <span>•</span>
                                      <span className="text-primary truncate">{collection.latestDateFormatted}</span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Right Pane: Selected Collection's Movies Grid */}
                    {activeSelectedMovieCollection && (() => {
                      const allSelected =
                        activeSelectedMovieCollection.movies.length > 0 &&
                        activeSelectedMovieCollection.movies.every((m) => selectedIds.has(m.video.message_id));

                      return (
                        <div className="flex-1 min-w-0 w-full rounded-2xl border border-border/80 bg-card/85 backdrop-blur-md p-4 sm:p-5 shadow-sm space-y-4">
                          {/* Collection Header & Quick Actions */}
                          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-border/40">
                            <div className="flex items-center gap-3">
                              <div className="w-12 h-12 rounded-xl overflow-hidden bg-zinc-950 border border-border/60 shrink-0 flex items-center justify-center">
                                {activeSelectedMovieCollection.leadThumbUrl ? (
                                  <img src={activeSelectedMovieCollection.leadThumbUrl} alt="" className="w-full h-full object-cover" />
                                ) : (
                                  <Clapperboard className="w-6 h-6 text-primary" />
                                )}
                              </div>
                              <div>
                                <h3 className="text-sm sm:text-base font-bold text-foreground">
                                  {activeSelectedMovieCollection.collectionTitle}
                                </h3>
                                <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5 flex-wrap font-mono">
                                  <Badge variant="outline" className="text-[10px] h-4.5 px-1.5 border-primary/30 text-primary font-semibold">
                                    {activeSelectedMovieCollection.totalMovies} {activeSelectedMovieCollection.totalMovies === 1 ? 'Movie' : 'Movies'}
                                  </Badge>
                                  <span>{formatBytes(activeSelectedMovieCollection.totalBytes)}</span>
                                  {activeSelectedMovieCollection.latestYear && (
                                    <>
                                      <span>•</span>
                                      <span>
                                        {activeSelectedMovieCollection.earliestYear && activeSelectedMovieCollection.earliestYear !== activeSelectedMovieCollection.latestYear
                                          ? `${activeSelectedMovieCollection.earliestYear} – ${activeSelectedMovieCollection.latestYear}`
                                          : `${activeSelectedMovieCollection.latestYear}`}
                                      </span>
                                    </>
                                  )}
                                  {activeSelectedMovieCollection.latestDateFormatted && (
                                    <span className="flex items-center gap-1 text-primary">
                                      <Calendar className="w-3 h-3" />
                                      Latest: {activeSelectedMovieCollection.latestDateFormatted}
                                    </span>
                                  )}
                                </div>
                              </div>
                            </div>

                            {/* Action Buttons for Collection */}
                            <div className="flex items-center gap-2 flex-wrap">
                              <Button
                                size="sm"
                                variant="default"
                                onClick={() => handleBingeMovieCollection(activeSelectedMovieCollection)}
                                className="h-8 px-3 text-xs gap-1.5 font-medium bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white shadow-xs cursor-pointer"
                                title={`Play all ${activeSelectedMovieCollection.movies.length} movies in VLC`}
                              >
                                <Play className="w-3.5 h-3.5 fill-current" />
                                <span>Binge in VLC</span>
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => handleDownloadMovieCollectionM3u(activeSelectedMovieCollection)}
                                className="h-8 px-2.5 text-xs gap-1 cursor-pointer"
                                title="Download Collection M3U Playlist"
                              >
                                <Download className="w-3 h-3" />
                                <span>Playlist</span>
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => handleSelectMovieCollection(activeSelectedMovieCollection)}
                                className={`h-8 px-2 text-xs gap-1 cursor-pointer ${
                                  allSelected ? 'bg-primary/10 text-primary border-primary/40' : ''
                                }`}
                              >
                                <CheckSquare className="w-3 h-3" />
                                <span>{allSelected ? 'Deselect All' : 'Select All'}</span>
                              </Button>
                            </div>
                          </div>

                          {/* Movie Cards Grid */}
                          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-3.5 pt-1">
                            {activeSelectedMovieCollection.movies.map((item) => {
                              const movieVideo = item.video;
                              const movieThumb = movieVideo.thumb_url || `/stream/${encodeURIComponent(String(movieVideo.chat_id))}/${movieVideo.message_id}/thumb`;
                              const isMovieSelected = selectedIds.has(movieVideo.message_id);

                              return (
                                <div
                                  key={`${movieVideo.chat_id}_${movieVideo.message_id}`}
                                  className={`rounded-xl border bg-background/60 hover:bg-accent/40 backdrop-blur-xs p-2.5 transition-all flex flex-col justify-between shadow-xs group ${
                                    isMovieSelected ? 'border-primary ring-2 ring-primary/30' : 'border-border/60 hover:border-primary/40'
                                  }`}
                                >
                                  {/* Thumbnail Frame */}
                                  <div className="relative aspect-[16/10] rounded-lg overflow-hidden bg-zinc-950 flex items-center justify-center">
                                    <img
                                      src={movieThumb}
                                      alt={item.cleanTitle}
                                      loading="lazy"
                                      className="w-full h-full object-cover transition-transform group-hover:scale-105 duration-300"
                                      onError={(e) => {
                                        (e.currentTarget as HTMLElement).style.display = 'none';
                                      }}
                                    />
                                    <div className="absolute inset-0 flex items-center justify-center bg-zinc-900 pointer-events-none -z-0">
                                      <Film className="w-6 h-6 text-zinc-600" />
                                    </div>
                                    <div className="absolute top-1.5 left-1.5 z-10 flex items-center gap-1 flex-wrap">
                                      {item.partNumber && (
                                        <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-primary text-primary-foreground shadow-xs">
                                          Part {item.partNumber}
                                        </span>
                                      )}
                                      {item.year && (
                                        <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-black/75 text-white backdrop-blur-md">
                                          {item.year}
                                        </span>
                                      )}
                                      {item.resolution && (
                                        <span className="px-1 py-0.5 rounded text-[8px] font-mono font-semibold bg-primary/20 text-primary border border-primary/30">
                                          {item.resolution}
                                        </span>
                                      )}
                                    </div>
                                    <div className="absolute top-1.5 right-1.5 z-10">
                                      <button
                                        onClick={() => toggleSelectVideo(movieVideo.message_id)}
                                        className={`w-5 h-5 rounded flex items-center justify-center transition-all cursor-pointer ${
                                          isMovieSelected ? 'bg-primary text-primary-foreground shadow' : 'bg-black/60 text-white/70 hover:text-white'
                                        }`}
                                        title="Select movie"
                                      >
                                        {isMovieSelected && <Check className="w-3 h-3 stroke-[3]" />}
                                      </button>
                                    </div>
                                    <div className="absolute bottom-1.5 right-1.5 z-10">
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-black/75 text-white backdrop-blur-md">
                                        {item.durationFormatted}
                                      </span>
                                    </div>
                                    <div
                                      onClick={() => handleStartPlay(movieVideo)}
                                      className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center cursor-pointer z-20"
                                      title="Stream Movie"
                                    >
                                      <div className="w-9 h-9 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-lg group-hover:scale-110 transition-transform">
                                        <Play className="w-4 h-4 fill-current ml-0.5" />
                                      </div>
                                    </div>
                                  </div>

                                  {/* Movie Meta */}
                                  <div className="pt-2 space-y-1.5 flex-1 flex flex-col justify-between">
                                    <div>
                                      <div
                                        onClick={() => handleStartPlay(movieVideo)}
                                        className="text-xs font-semibold text-foreground line-clamp-1 hover:text-primary transition-colors cursor-pointer"
                                        title={item.cleanTitle}
                                      >
                                        {item.cleanTitle}
                                      </div>
                                      <div className="flex items-center justify-between text-[10px] text-muted-foreground font-mono mt-1">
                                        <span>{item.sizeFormatted}</span>
                                        {item.formattedDate && (
                                          <span className="flex items-center gap-1 text-primary">
                                            <Calendar className="w-2.5 h-2.5" />
                                            {item.formattedDate}
                                          </span>
                                        )}
                                      </div>
                                    </div>

                                    {/* Actions */}
                                    <div className="flex items-center gap-1.5 pt-1.5 border-t border-border/40">
                                      <Button
                                        size="sm"
                                        variant="default"
                                        onClick={() => handleStartPlay(movieVideo)}
                                        className="h-6 flex-1 text-[10px] px-2 gap-1 cursor-pointer bg-primary text-primary-foreground hover:opacity-90 font-medium"
                                      >
                                        <Play className="w-2.5 h-2.5 fill-current" />
                                        <span>Play</span>
                                      </Button>
                                      <Button
                                        size="sm"
                                        variant="secondary"
                                        onClick={() => void handlePlayInVlc(movieVideo)}
                                        className="h-6 px-2 text-[10px] gap-1 cursor-pointer text-orange-400 hover:text-orange-300"
                                        title="Play in VLC"
                                      >
                                        <span>VLC</span>
                                      </Button>
                                      <button
                                        onClick={() => handleCopyStreamLink(movieVideo)}
                                        className="w-6 h-6 rounded text-muted-foreground hover:text-foreground flex items-center justify-center hover:bg-muted/60 transition-colors cursor-pointer"
                                        title="Copy link"
                                      >
                                        <Copy className="w-3 h-3" />
                                      </button>
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      );
                    })()}
                  </div>
                )
              )}

              {/* Bundled View for Movies */}
              {movieViewStyle === 'bundled' && (
                activeMovieCollections.length === 0 ? (
                  <div className="p-12 text-center rounded-2xl border border-dashed border-border/80 bg-card/20 flex flex-col items-center justify-center gap-2">
                    <Film className="w-10 h-10 text-muted-foreground/40 mb-1" />
                    <p className="text-sm font-semibold text-foreground">No Movie Collections Found</p>
                    <p className="text-xs text-muted-foreground max-w-sm">
                      No matching movie franchises or release year groupings found in this channel.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {activeMovieCollections.map((collection) => {
                      const isExpanded = expandedMovieCollections[collection.id] !== false;
                      const allSelected =
                        collection.movies.length > 0 &&
                        collection.movies.every((m) => selectedIds.has(m.video.message_id));

                      return (
                        <div
                          key={collection.id}
                          className="rounded-2xl border border-border/80 bg-card/85 backdrop-blur-md overflow-hidden shadow-sm hover:border-primary/40 transition-all p-4 space-y-3.5"
                        >
                          {/* Collection Header */}
                          <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="relative w-14 h-14 rounded-xl overflow-hidden bg-zinc-950 border border-border/60 shrink-0 shadow-sm flex items-center justify-center">
                                {collection.leadThumbUrl ? (
                                  <img
                                    src={collection.leadThumbUrl}
                                    alt={collection.collectionTitle}
                                    loading="lazy"
                                    className="w-full h-full object-cover"
                                    onError={(e) => {
                                      (e.currentTarget as HTMLElement).style.display = 'none';
                                    }}
                                  />
                                ) : (
                                  <Clapperboard className="w-6 h-6 text-primary/40" />
                                )}
                              </div>

                              <div className="min-w-0">
                                <h4 className="text-sm font-semibold text-foreground truncate" title={collection.collectionTitle}>
                                  {collection.collectionTitle}
                                </h4>
                                <div className="flex items-center gap-1.5 mt-1 text-[11px] text-muted-foreground flex-wrap">
                                  <Badge variant="outline" className="text-[10px] h-4.5 px-1.5 border-primary/30 text-primary font-semibold">
                                    {collection.totalMovies} {collection.totalMovies === 1 ? 'Movie' : 'Movies'}
                                  </Badge>
                                  <span>•</span>
                                  <span className="font-mono">{formatBytes(collection.totalBytes)}</span>
                                  {collection.latestDateFormatted && (
                                    <>
                                      <span>•</span>
                                      <span className="flex items-center gap-1 font-mono text-[10px] text-primary">
                                        <Calendar className="w-2.5 h-2.5" />
                                        Latest: {collection.latestDateFormatted}
                                      </span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </div>

                            {/* Action Buttons */}
                            <div className="flex items-center gap-2 flex-wrap">
                              <Button
                                size="sm"
                                variant="default"
                                onClick={() => handleBingeMovieCollection(collection)}
                                className="h-8 px-3 text-xs gap-1.5 font-medium bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white shadow-xs cursor-pointer"
                                title={`Play all ${collection.movies.length} movies in VLC`}
                              >
                                <Play className="w-3 h-3 fill-current" />
                                <span>Binge in VLC</span>
                              </Button>

                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => handleDownloadMovieCollectionM3u(collection)}
                                className="h-8 px-2.5 text-xs gap-1 cursor-pointer"
                                title="Download Collection M3U playlist file"
                              >
                                <Download className="w-3 h-3" />
                                <span>Playlist</span>
                              </Button>

                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => handleSelectMovieCollection(collection)}
                                className={`h-8 px-2 text-xs gap-1 cursor-pointer ${
                                  allSelected ? 'bg-primary/10 text-primary border-primary/40' : ''
                                }`}
                              >
                                <CheckSquare className="w-3 h-3" />
                                <span>{allSelected ? 'Deselect All' : 'Select All'}</span>
                              </Button>

                              <button
                                onClick={() => toggleExpandMovieCollection(collection.id)}
                                className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 py-1 rounded-md hover:bg-muted/50 transition-colors cursor-pointer"
                                title="Toggle movie collection drawer"
                              >
                                {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                                <span>{isExpanded ? 'Hide' : 'Movies'}</span>
                              </button>
                            </div>
                          </div>

                          {/* Collapsible Movies Grid */}
                          {isExpanded && (
                            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3 pt-2 border-t border-border/40">
                              {collection.movies.map((item) => {
                                const movieVideo = item.video;
                                const movieThumb = movieVideo.thumb_url || `/stream/${encodeURIComponent(String(movieVideo.chat_id))}/${movieVideo.message_id}/thumb`;
                                const isMovieSelected = selectedIds.has(movieVideo.message_id);

                                return (
                                  <div
                                    key={`${movieVideo.chat_id}_${movieVideo.message_id}`}
                                    className={`rounded-xl border bg-background/50 hover:bg-accent/40 backdrop-blur-xs p-2.5 transition-all flex flex-col justify-between shadow-xs group ${
                                      isMovieSelected ? 'border-primary ring-2 ring-primary/30' : 'border-border/60 hover:border-primary/40'
                                    }`}
                                  >
                                    <div className="relative aspect-[16/10] rounded-lg overflow-hidden bg-zinc-950 flex items-center justify-center">
                                      <img
                                        src={movieThumb}
                                        alt={item.cleanTitle}
                                        loading="lazy"
                                        className="w-full h-full object-cover transition-transform group-hover:scale-105 duration-300"
                                        onError={(e) => {
                                          (e.currentTarget as HTMLElement).style.display = 'none';
                                        }}
                                      />
                                      <div className="absolute inset-0 flex items-center justify-center bg-zinc-900 pointer-events-none -z-0">
                                        <Film className="w-6 h-6 text-zinc-600" />
                                      </div>
                                      <div className="absolute top-1.5 left-1.5 z-10 flex items-center gap-1">
                                        {item.partNumber && (
                                          <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-primary text-primary-foreground shadow-xs">
                                            Part {item.partNumber}
                                          </span>
                                        )}
                                        {item.year && (
                                          <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-black/75 text-white backdrop-blur-md">
                                            {item.year}
                                          </span>
                                        )}
                                      </div>
                                      <div className="absolute top-1.5 right-1.5 z-10">
                                        <button
                                          onClick={() => toggleSelectVideo(movieVideo.message_id)}
                                          className={`w-5 h-5 rounded flex items-center justify-center transition-all cursor-pointer ${
                                            isMovieSelected ? 'bg-primary text-primary-foreground shadow' : 'bg-black/60 text-white/70 hover:text-white'
                                          }`}
                                          title="Select movie"
                                        >
                                          {isMovieSelected && <Check className="w-3 h-3 stroke-[3]" />}
                                        </button>
                                      </div>
                                      <div className="absolute bottom-1.5 right-1.5 z-10">
                                        <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-black/75 text-white backdrop-blur-md">
                                          {item.durationFormatted}
                                        </span>
                                      </div>
                                      <div
                                        onClick={() => handleStartPlay(movieVideo)}
                                        className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center cursor-pointer z-20"
                                        title="Stream Movie"
                                      >
                                        <div className="w-9 h-9 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-lg group-hover:scale-110 transition-transform">
                                          <Play className="w-4 h-4 fill-current ml-0.5" />
                                        </div>
                                      </div>
                                    </div>

                                    <div className="pt-2 space-y-1.5 flex-1 flex flex-col justify-between">
                                      <div>
                                        <div
                                          onClick={() => handleStartPlay(movieVideo)}
                                          className="text-xs font-semibold text-foreground line-clamp-1 hover:text-primary transition-colors cursor-pointer"
                                          title={item.cleanTitle}
                                        >
                                          {item.cleanTitle}
                                        </div>
                                        <div className="flex items-center justify-between text-[10px] text-muted-foreground font-mono mt-1">
                                          <span>{item.sizeFormatted}</span>
                                          {item.formattedDate && (
                                            <span className="flex items-center gap-1">
                                              <Calendar className="w-2.5 h-2.5 text-muted-foreground/60" />
                                              {item.formattedDate}
                                            </span>
                                          )}
                                        </div>
                                      </div>

                                      <div className="flex items-center gap-1.5 pt-1.5 border-t border-border/40">
                                        <Button
                                          size="sm"
                                          variant="default"
                                          onClick={() => handleStartPlay(movieVideo)}
                                          className="h-6 flex-1 text-[10px] px-2 gap-1 cursor-pointer bg-primary text-primary-foreground hover:opacity-90 font-medium"
                                        >
                                          <Play className="w-2.5 h-2.5 fill-current" />
                                          <span>Play</span>
                                        </Button>
                                        <Button
                                          size="sm"
                                          variant="secondary"
                                          onClick={() => void handlePlayInVlc(movieVideo)}
                                          className="h-6 px-2 text-[10px] gap-1 cursor-pointer text-orange-400 hover:text-orange-300"
                                          title="Play in desktop VLC"
                                        >
                                          <span>VLC</span>
                                        </Button>
                                        <button
                                          onClick={() => handleCopyStreamLink(movieVideo)}
                                          className="w-6 h-6 rounded text-muted-foreground hover:text-foreground flex items-center justify-center hover:bg-muted/60 transition-colors cursor-pointer"
                                          title="Copy direct stream link"
                                        >
                                          <Copy className="w-3 h-3" />
                                        </button>
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )
              )}
            </div>
          )}

          {/* Dedicated Web Series by Season Showcase (active when filter === 'series') */}
          {filter === 'series' && !loading && (
            <div className="space-y-4">
              {/* Web Series Toolbar */}
              <div className="p-3.5 rounded-2xl border border-border/70 bg-card/70 backdrop-blur-md flex flex-col md:flex-row items-start md:items-center justify-between gap-3 shadow-xs">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-xl bg-primary/15 text-primary flex items-center justify-center shrink-0 shadow-inner">
                    <Tv className="w-4 h-4" />
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-foreground flex items-center gap-2">
                      <span>Drafted Web Series</span>
                      <Badge variant="secondary" className="text-[10px] font-mono px-2 py-0">
                        {webSeriesDrafts.length} Shows Found
                      </Badge>
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      Organized by seasons with date-based sorting and continuous playback.
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2.5 flex-wrap">
                  {/* Series Sort Order */}
                  <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border bg-background text-xs">
                    <ArrowDownUp className="w-3.5 h-3.5 text-muted-foreground" />
                    <span className="text-muted-foreground font-medium">Show:</span>
                    <select
                      value={seriesSortMode}
                      onChange={(e) => setSeriesSortMode(e.target.value as SeriesSortOption)}
                      aria-label="Sort Web Series By"
                      className="bg-transparent text-xs font-medium focus:outline-none cursor-pointer"
                    >
                      <option value="date-desc" className="bg-card text-foreground">Date: Newest First</option>
                      <option value="date-asc" className="bg-card text-foreground">Date: Oldest First</option>
                      <option value="title-asc" className="bg-card text-foreground">Title (A-Z)</option>
                      <option value="episodes-desc" className="bg-card text-foreground">Most Episodes</option>
                    </select>
                  </div>

                  {/* Episode Sort Order within Seasons */}
                  <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border bg-background text-xs">
                    <SlidersHorizontal className="w-3.5 h-3.5 text-muted-foreground" />
                    <span className="text-muted-foreground font-medium">Episodes:</span>
                    <select
                      value={episodeSortMode}
                      onChange={(e) => setEpisodeSortMode(e.target.value as EpisodeSortOption)}
                      aria-label="Sort Episodes By"
                      className="bg-transparent text-xs font-medium focus:outline-none cursor-pointer"
                    >
                      <option value="episode-asc" className="bg-card text-foreground">Episode 1 → N</option>
                      <option value="episode-desc" className="bg-card text-foreground">Episode N → 1</option>
                      <option value="date-desc" className="bg-card text-foreground">Date: Newest First</option>
                      <option value="date-asc" className="bg-card text-foreground">Date: Oldest First</option>
                    </select>
                  </div>

                  {/* View Style Toggle: Split vs Grouped vs Flat */}
                  <div className="flex items-center p-0.5 rounded-lg border border-border bg-background text-xs">
                    <button
                      onClick={() => setSeriesViewStyle('split')}
                      className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                        seriesViewStyle === 'split'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Split View
                    </button>
                    <button
                      onClick={() => setSeriesViewStyle('grouped')}
                      className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                        seriesViewStyle === 'grouped'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Stacked Bundles
                    </button>
                    <button
                      onClick={() => setSeriesViewStyle('flat')}
                      className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                        seriesViewStyle === 'flat'
                          ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Flat Episodes
                    </button>
                  </div>
                </div>
              </div>

              {/* Split View (Shows List on Left, Seasons & Episodes on Right) */}
              {seriesViewStyle === 'split' && (
                webSeriesDrafts.length === 0 ? (
                  <div className="p-12 text-center rounded-2xl border border-dashed border-border/80 bg-card/20 flex flex-col items-center justify-center gap-2">
                    <Tv className="w-10 h-10 text-muted-foreground/40 mb-1" />
                    <p className="text-sm font-semibold text-foreground">No Web Series Detected</p>
                    <p className="text-xs text-muted-foreground max-w-sm">
                      No multi-episode web series naming patterns (e.g. S01E01, Season 1 Ep 2) were found in this channel.
                    </p>
                  </div>
                ) : (
                  <div className="flex flex-col lg:flex-row gap-5 items-start w-full">
                    {/* Left Pane: Shows List */}
                    <div className="w-full lg:w-72 shrink-0 space-y-2 p-3 rounded-2xl border border-border/70 bg-card/75 backdrop-blur-md shadow-xs">
                      <div className="flex items-center justify-between pb-2 border-b border-border/40 px-1">
                        <span className="text-xs font-semibold text-foreground">Web Series ({webSeriesDrafts.length})</span>
                        <span className="text-[10px] text-muted-foreground font-mono">Select a Show</span>
                      </div>
                      <div className="space-y-1.5 max-h-[640px] overflow-y-auto scrollbar-thin pr-1">
                        {webSeriesDrafts.map((draft) => {
                          const isSelected = activeSelectedShow?.id === draft.id;
                          return (
                            <button
                              key={draft.id}
                              onClick={() => setSelectedShowId(draft.id)}
                              className={`w-full text-left p-2.5 rounded-xl border transition-all cursor-pointer flex items-center gap-2.5 ${
                                isSelected
                                  ? 'border-primary bg-primary/10 ring-1 ring-primary/40 text-foreground'
                                  : 'border-border/60 hover:bg-muted/40 text-muted-foreground hover:text-foreground'
                              }`}
                            >
                              <div className="w-10 h-10 rounded-lg overflow-hidden bg-zinc-950 border border-border/60 shrink-0 flex items-center justify-center">
                                {draft.leadThumbUrl ? (
                                  <img src={draft.leadThumbUrl} alt="" className="w-full h-full object-cover" />
                                ) : (
                                  <Clapperboard className="w-5 h-5 text-primary/50" />
                                )}
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="text-xs font-semibold truncate text-foreground leading-snug">
                                  {draft.seriesTitle}
                                </div>
                                <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground mt-0.5 font-mono">
                                  <span>{draft.totalSeasons}S</span>
                                  <span>•</span>
                                  <span>{draft.totalEpisodes}EPs</span>
                                  {draft.latestDateFormatted && (
                                    <>
                                      <span>•</span>
                                      <span className="text-primary truncate">{draft.latestDateFormatted}</span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Right Pane: Selected Show's Seasons & Episodes */}
                    {activeSelectedShow && (() => {
                      const currentSeasonNum = activeSeasons[activeSelectedShow.id] || activeSelectedShow.seasons[0]?.seasonNumber || 1;
                      const currentSeason = activeSelectedShow.seasons.find((s) => s.seasonNumber === currentSeasonNum) || activeSelectedShow.seasons[0];
                      const allSeasonSelected = currentSeason?.episodes.length > 0 && currentSeason.episodes.every((e) => selectedIds.has(e.video.message_id));

                      return (
                        <div className="flex-1 min-w-0 w-full rounded-2xl border border-border/80 bg-card/85 backdrop-blur-md p-4 sm:p-5 shadow-sm space-y-4">
                          {/* Show Header & Quick Actions */}
                          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-border/40">
                            <div className="flex items-center gap-3">
                              <div className="w-12 h-12 rounded-xl overflow-hidden bg-zinc-950 border border-border/60 shrink-0 flex items-center justify-center">
                                {activeSelectedShow.leadThumbUrl ? (
                                  <img src={activeSelectedShow.leadThumbUrl} alt="" className="w-full h-full object-cover" />
                                ) : (
                                  <Tv className="w-6 h-6 text-primary" />
                                )}
                              </div>
                              <div>
                                <h3 className="text-sm sm:text-base font-bold text-foreground">
                                  {activeSelectedShow.seriesTitle}
                                </h3>
                                <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5 flex-wrap font-mono">
                                  <Badge variant="outline" className="text-[10px] h-4.5 px-1.5 border-primary/30 text-primary font-semibold">
                                    {activeSelectedShow.totalSeasons} Seasons
                                  </Badge>
                                  <span>{activeSelectedShow.totalEpisodes} Total Episodes</span>
                                  <span>•</span>
                                  <span>{formatBytes(activeSelectedShow.totalBytes)}</span>
                                  {activeSelectedShow.latestDateFormatted && (
                                    <span className="flex items-center gap-1 text-primary">
                                      <Calendar className="w-3 h-3" />
                                      Latest: {activeSelectedShow.latestDateFormatted}
                                    </span>
                                  )}
                                </div>
                              </div>
                            </div>

                            {/* Action Buttons for Active Season */}
                            {currentSeason && (
                              <div className="flex items-center gap-2 flex-wrap">
                                <Button
                                  size="sm"
                                  variant="default"
                                  onClick={() => handleBingeSeason(activeSelectedShow.seriesTitle, currentSeason)}
                                  className="h-8 px-3 text-xs gap-1.5 font-medium bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white shadow-xs cursor-pointer"
                                  title={`Play all ${currentSeason.episodes.length} episodes of ${currentSeason.seasonLabel} in VLC`}
                                >
                                  <Play className="w-3.5 h-3.5 fill-current" />
                                  <span>Binge {currentSeason.seasonLabel}</span>
                                </Button>
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  onClick={() => handleDownloadSeasonM3u(activeSelectedShow.seriesTitle, currentSeason)}
                                  className="h-8 px-2.5 text-xs gap-1 cursor-pointer"
                                  title="Download Season M3U Playlist"
                                >
                                  <Download className="w-3 h-3" />
                                  <span>Playlist</span>
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => handleSelectSeason(currentSeason)}
                                  className={`h-8 px-2 text-xs gap-1 cursor-pointer ${
                                    allSeasonSelected ? 'bg-primary/10 text-primary border-primary/40' : ''
                                  }`}
                                >
                                  <CheckSquare className="w-3 h-3" />
                                  <span>{allSeasonSelected ? 'Deselect Season' : 'Select Season'}</span>
                                </Button>
                              </div>
                            )}
                          </div>

                          {/* Season Selector Tabs */}
                          {activeSelectedShow.seasons.length > 0 && (
                            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
                              {activeSelectedShow.seasons.map((season) => {
                                const isActive = season.seasonNumber === currentSeasonNum;
                                return (
                                  <button
                                    key={season.seasonNumber}
                                    onClick={() => setActiveSeasons((prev) => ({ ...prev, [activeSelectedShow.id]: season.seasonNumber }))}
                                    className={`text-xs px-3 py-1.5 rounded-lg transition-all cursor-pointer flex items-center gap-1.5 font-medium ${
                                      isActive
                                        ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                                        : 'bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground'
                                    }`}
                                  >
                                    <span>{season.seasonLabel}</span>
                                    <span className={`text-[10px] font-mono px-1 py-0.2 rounded ${isActive ? 'bg-black/25 text-white' : 'bg-black/10 text-muted-foreground'}`}>
                                      {season.totalEpisodes} EPs
                                    </span>
                                    <span className="text-[10px] opacity-75 font-mono">
                                      {formatBytes(season.totalBytes)}
                                    </span>
                                  </button>
                                );
                              })}
                            </div>
                          )}

                          {/* Episodes Grid in Right Pane */}
                          {currentSeason && (
                            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-3.5 pt-1">
                              {currentSeason.episodes.map((ep) => {
                                const epVideo = ep.video;
                                const epThumb = epVideo.thumb_url || `/stream/${encodeURIComponent(String(epVideo.chat_id))}/${epVideo.message_id}/thumb`;
                                const isEpSelected = selectedIds.has(epVideo.message_id);

                                return (
                                  <div
                                    key={`${epVideo.chat_id}_${epVideo.message_id}`}
                                    className={`rounded-xl border bg-background/60 hover:bg-accent/40 backdrop-blur-xs p-2.5 transition-all flex flex-col justify-between shadow-xs group ${
                                      isEpSelected ? 'border-primary ring-2 ring-primary/30' : 'border-border/60 hover:border-primary/40'
                                    }`}
                                  >
                                    {/* Thumbnail Frame */}
                                    <div className="relative aspect-[16/10] rounded-lg overflow-hidden bg-zinc-950 flex items-center justify-center">
                                      <img
                                        src={epThumb}
                                        alt={ep.displayTitle}
                                        loading="lazy"
                                        className="w-full h-full object-cover transition-transform group-hover:scale-105 duration-300"
                                        onError={(e) => {
                                          (e.currentTarget as HTMLElement).style.display = 'none';
                                        }}
                                      />
                                      <div className="absolute inset-0 flex items-center justify-center bg-zinc-900 pointer-events-none -z-0">
                                        <Film className="w-6 h-6 text-zinc-600" />
                                      </div>
                                      <div className="absolute top-1.5 left-1.5 z-10 flex items-center gap-1">
                                        <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-primary text-primary-foreground shadow-xs">
                                          {ep.episodeLabel}
                                        </span>
                                      </div>
                                      <div className="absolute top-1.5 right-1.5 z-10">
                                        <button
                                          onClick={() => toggleSelectVideo(epVideo.message_id)}
                                          className={`w-5 h-5 rounded flex items-center justify-center transition-all cursor-pointer ${
                                            isEpSelected ? 'bg-primary text-primary-foreground shadow' : 'bg-black/60 text-white/70 hover:text-white'
                                          }`}
                                          title="Select episode"
                                        >
                                          {isEpSelected && <Check className="w-3 h-3 stroke-[3]" />}
                                        </button>
                                      </div>
                                      <div className="absolute bottom-1.5 right-1.5 z-10">
                                        <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-black/75 text-white backdrop-blur-md">
                                          {ep.durationFormatted}
                                        </span>
                                      </div>
                                      <div
                                        onClick={() => handleStartPlay(epVideo)}
                                        className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center cursor-pointer z-20"
                                        title="Stream Episode"
                                      >
                                        <div className="w-9 h-9 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-lg group-hover:scale-110 transition-transform">
                                          <Play className="w-4 h-4 fill-current ml-0.5" />
                                        </div>
                                      </div>
                                    </div>

                                    {/* Episode Meta */}
                                    <div className="pt-2 space-y-1.5 flex-1 flex flex-col justify-between">
                                      <div>
                                        <div
                                          onClick={() => handleStartPlay(epVideo)}
                                          className="text-xs font-semibold text-foreground line-clamp-1 hover:text-primary transition-colors cursor-pointer"
                                          title={ep.displayTitle}
                                        >
                                          {ep.displayTitle}
                                        </div>
                                        <div className="flex items-center justify-between text-[10px] text-muted-foreground font-mono mt-1">
                                          <span>{ep.sizeFormatted}</span>
                                          {ep.formattedDate && (
                                            <span className="flex items-center gap-1 text-primary">
                                              <Calendar className="w-2.5 h-2.5" />
                                              {ep.formattedDate}
                                            </span>
                                          )}
                                        </div>
                                      </div>

                                      {/* Actions */}
                                      <div className="flex items-center gap-1.5 pt-1.5 border-t border-border/40">
                                        <Button
                                          size="sm"
                                          variant="default"
                                          onClick={() => handleStartPlay(epVideo)}
                                          className="h-6 flex-1 text-[10px] px-2 gap-1 cursor-pointer bg-primary text-primary-foreground hover:opacity-90 font-medium"
                                        >
                                          <Play className="w-2.5 h-2.5 fill-current" />
                                          <span>Play</span>
                                        </Button>
                                        <Button
                                          size="sm"
                                          variant="secondary"
                                          onClick={() => void handlePlayInVlc(epVideo)}
                                          className="h-6 px-2 text-[10px] gap-1 cursor-pointer text-orange-400 hover:text-orange-300"
                                          title="Play in VLC"
                                        >
                                          <span>VLC</span>
                                        </Button>
                                        <button
                                          onClick={() => handleCopyStreamLink(epVideo)}
                                          className="w-6 h-6 rounded text-muted-foreground hover:text-foreground flex items-center justify-center hover:bg-muted/60 transition-colors cursor-pointer"
                                          title="Copy link"
                                        >
                                          <Copy className="w-3 h-3" />
                                        </button>
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                )
              )}

              {/* Web Series Draft Cards (Stacked Bundles view) */}
              {seriesViewStyle === 'grouped' && (
                webSeriesDrafts.length === 0 ? (
                  <div className="p-12 text-center rounded-2xl border border-dashed border-border/80 bg-card/20 flex flex-col items-center justify-center gap-2">
                    <Tv className="w-10 h-10 text-muted-foreground/40 mb-1" />
                    <p className="text-sm font-semibold text-foreground">No Web Series Detected</p>
                    <p className="text-xs text-muted-foreground max-w-sm">
                      No multi-episode web series naming patterns (e.g. S01E01, Season 1 Ep 2) were found in this channel.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {webSeriesDrafts.map((draft) => {
                      const activeSeasonNum = activeSeasons[draft.id] || draft.seasons[0]?.seasonNumber || 1;
                      const activeSeason = draft.seasons.find((s) => s.seasonNumber === activeSeasonNum) || draft.seasons[0];
                      const isExpanded = expandedSeries[draft.id] !== false;
                      const allSeasonSelected = activeSeason?.episodes.length > 0 && activeSeason.episodes.every((e) => selectedIds.has(e.video.message_id));

                      return (
                        <div
                          key={draft.id}
                          className="rounded-2xl border border-border/80 bg-card/85 backdrop-blur-md overflow-hidden shadow-sm hover:border-primary/40 transition-all p-4 space-y-3.5"
                        >
                          {/* Show Header */}
                          <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              {/* Poster / Thumbnail Lead */}
                              <div className="relative w-14 h-14 rounded-xl overflow-hidden bg-zinc-950 border border-border/60 shrink-0 shadow-sm flex items-center justify-center">
                                {draft.leadThumbUrl ? (
                                  <img
                                    src={draft.leadThumbUrl}
                                    alt={draft.seriesTitle}
                                    loading="lazy"
                                    className="w-full h-full object-cover"
                                    onError={(e) => {
                                      (e.currentTarget as HTMLElement).style.display = 'none';
                                    }}
                                  />
                                ) : (
                                  <Clapperboard className="w-6 h-6 text-primary/40" />
                                )}
                              </div>

                              <div className="min-w-0">
                                <h4 className="text-sm font-semibold text-foreground truncate" title={draft.seriesTitle}>
                                  {draft.seriesTitle}
                                </h4>
                                <div className="flex items-center gap-1.5 mt-1 text-[11px] text-muted-foreground flex-wrap">
                                  <Badge variant="outline" className="text-[10px] h-4.5 px-1.5 border-primary/30 text-primary font-semibold">
                                    {draft.totalSeasons} {draft.totalSeasons === 1 ? 'Season' : 'Seasons'}
                                  </Badge>
                                  <span>•</span>
                                  <span>{draft.totalEpisodes} Total Episodes</span>
                                  <span>•</span>
                                  <span className="font-mono">{formatBytes(draft.totalBytes)}</span>
                                  {draft.latestDateFormatted && (
                                    <>
                                      <span>•</span>
                                      <span className="flex items-center gap-1 font-mono text-[10px] text-primary">
                                        <Calendar className="w-2.5 h-2.5" />
                                        Latest: {draft.latestDateFormatted}
                                      </span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </div>

                            {/* Show & Season Action Buttons */}
                            {activeSeason && (
                              <div className="flex items-center gap-2 flex-wrap">
                                <Button
                                  size="sm"
                                  variant="default"
                                  onClick={() => handleBingeSeason(draft.seriesTitle, activeSeason)}
                                  className="h-8 px-3 text-xs gap-1.5 font-medium bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white shadow-xs cursor-pointer"
                                  title={`Play entire ${activeSeason.seasonLabel} (${activeSeason.episodes.length} episodes) sequentially in desktop VLC`}
                                >
                                  <Play className="w-3 h-3 fill-current" />
                                  <span>Binge {activeSeason.seasonLabel}</span>
                                </Button>

                                <Button
                                  size="sm"
                                  variant="secondary"
                                  onClick={() => handleDownloadSeasonM3u(draft.seriesTitle, activeSeason)}
                                  className="h-8 px-2.5 text-xs gap-1 cursor-pointer"
                                  title={`Download ${activeSeason.seasonLabel} M3U playlist file`}
                                >
                                  <Download className="w-3 h-3" />
                                  <span>Playlist</span>
                                </Button>

                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => handleSelectSeason(activeSeason)}
                                  className={`h-8 px-2 text-xs gap-1 cursor-pointer ${
                                    allSeasonSelected ? 'bg-primary/10 text-primary border-primary/40' : ''
                                  }`}
                                  title="Toggle batch selection for all episodes in this season"
                                >
                                  <CheckSquare className="w-3 h-3" />
                                  <span>{allSeasonSelected ? 'Deselect Season' : 'Select Season'}</span>
                                </Button>

                                <button
                                  onClick={() => toggleExpandSeries(draft.id)}
                                  className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 py-1 rounded-md hover:bg-muted/50 transition-colors cursor-pointer"
                                  title="Toggle episode drawer"
                                >
                                  {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                                  <span>{isExpanded ? 'Hide' : 'Episodes'}</span>
                                </button>
                              </div>
                            )}
                          </div>

                          {/* Season Tabs Selector */}
                          {draft.seasons.length > 0 && (
                            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none pt-1 border-t border-border/40">
                              {draft.seasons.map((season) => {
                                const isActive = season.seasonNumber === activeSeasonNum;
                                return (
                                  <button
                                    key={season.seasonNumber}
                                    onClick={() => setActiveSeasons((prev) => ({ ...prev, [draft.id]: season.seasonNumber }))}
                                    className={`text-xs px-3 py-1.5 rounded-lg transition-all cursor-pointer flex items-center gap-1.5 font-medium ${
                                      isActive
                                        ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                                        : 'bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground'
                                    }`}
                                  >
                                    <span>{season.seasonLabel}</span>
                                    <span className={`text-[10px] font-mono px-1 py-0.2 rounded ${isActive ? 'bg-black/25 text-white' : 'bg-black/10 text-muted-foreground'}`}>
                                      {season.totalEpisodes} EPs
                                    </span>
                                    <span className="text-[10px] opacity-75 font-mono">
                                      {formatBytes(season.totalBytes)}
                                    </span>
                                  </button>
                                );
                              })}
                            </div>
                          )}

                          {/* Expandable Episode Drawer */}
                          {isExpanded && activeSeason && (
                            <div className="pt-2">
                              {/* Horizontal Episode Track */}
                              <div className="flex items-stretch gap-3 overflow-x-auto pb-3 scrollbar-thin scroll-smooth">
                                {activeSeason.episodes.map((ep) => {
                                  const epVideo = ep.video;
                                  const epThumb = epVideo.thumb_url || `/stream/${encodeURIComponent(String(epVideo.chat_id))}/${epVideo.message_id}/thumb`;
                                  const isEpSelected = selectedIds.has(epVideo.message_id);

                                  return (
                                    <div
                                      key={`${epVideo.chat_id}_${epVideo.message_id}`}
                                      className={`w-64 sm:w-72 shrink-0 rounded-xl border bg-background/60 hover:bg-accent/40 backdrop-blur-xs p-2.5 transition-all flex flex-col justify-between shadow-xs group ${
                                        isEpSelected ? 'border-primary ring-2 ring-primary/30' : 'border-border/60 hover:border-primary/40'
                                      }`}
                                    >
                                      {/* Episode Thumbnail Frame */}
                                      <div className="relative aspect-[16/10] rounded-lg overflow-hidden bg-zinc-950 flex items-center justify-center">
                                        <img
                                          src={epThumb}
                                          alt={ep.displayTitle}
                                          loading="lazy"
                                          className="w-full h-full object-cover transition-transform group-hover:scale-105 duration-300"
                                          onError={(e) => {
                                            (e.currentTarget as HTMLElement).style.display = 'none';
                                          }}
                                        />

                                        {/* Fallback */}
                                        <div className="absolute inset-0 flex items-center justify-center bg-zinc-900 pointer-events-none -z-0">
                                          <Film className="w-6 h-6 text-zinc-600" />
                                        </div>

                                        {/* Badges Overlay */}
                                        <div className="absolute top-1.5 left-1.5 z-10 flex items-center gap-1">
                                          <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-primary text-primary-foreground shadow-xs">
                                            {ep.episodeLabel}
                                          </span>
                                        </div>

                                        <div className="absolute top-1.5 right-1.5 z-10">
                                          <button
                                            onClick={() => toggleSelectVideo(epVideo.message_id)}
                                            className={`w-5 h-5 rounded flex items-center justify-center transition-all cursor-pointer ${
                                              isEpSelected ? 'bg-primary text-primary-foreground shadow' : 'bg-black/60 text-white/70 hover:text-white'
                                            }`}
                                            title="Select episode"
                                          >
                                            {isEpSelected && <Check className="w-3 h-3 stroke-[3]" />}
                                          </button>
                                        </div>

                                        <div className="absolute bottom-1.5 right-1.5 z-10">
                                          <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-black/75 text-white backdrop-blur-md">
                                            {ep.durationFormatted}
                                          </span>
                                        </div>

                                        {/* Play Hover Overlay */}
                                        <div
                                          onClick={() => handleStartPlay(epVideo)}
                                          className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center cursor-pointer z-20"
                                          title="Stream Episode"
                                        >
                                          <div className="w-10 h-10 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-lg group-hover:scale-110 transition-transform">
                                            <Play className="w-4 h-4 fill-current ml-0.5" />
                                          </div>
                                        </div>
                                      </div>

                                      {/* Episode Info */}
                                      <div className="pt-2 space-y-1.5 flex-1 flex flex-col justify-between">
                                        <div>
                                          <div
                                            onClick={() => handleStartPlay(epVideo)}
                                            className="text-xs font-semibold text-foreground line-clamp-1 hover:text-primary transition-colors cursor-pointer"
                                            title={ep.displayTitle}
                                          >
                                            {ep.displayTitle}
                                          </div>
                                          <div className="flex items-center justify-between text-[10px] text-muted-foreground font-mono mt-1">
                                            <span>{ep.sizeFormatted}</span>
                                            {ep.formattedDate && (
                                              <span className="flex items-center gap-1">
                                                <Calendar className="w-2.5 h-2.5 text-muted-foreground/60" />
                                                {ep.formattedDate}
                                              </span>
                                            )}
                                          </div>
                                        </div>

                                        {/* Episode Actions */}
                                        <div className="flex items-center gap-1.5 pt-1.5 border-t border-border/40">
                                          <Button
                                            size="sm"
                                            variant="default"
                                            onClick={() => handleStartPlay(epVideo)}
                                            className="h-6 flex-1 text-[10px] px-2 gap-1 cursor-pointer bg-primary text-primary-foreground hover:opacity-90 font-medium"
                                          >
                                            <Play className="w-2.5 h-2.5 fill-current" />
                                            <span>Play</span>
                                          </Button>
                                          <Button
                                            size="sm"
                                            variant="secondary"
                                            onClick={() => void handlePlayInVlc(epVideo)}
                                            className="h-6 px-2 text-[10px] gap-1 cursor-pointer text-orange-400 hover:text-orange-300"
                                            title="Play in desktop VLC"
                                          >
                                            <span>VLC</span>
                                          </Button>
                                          <button
                                            onClick={() => handleCopyStreamLink(epVideo)}
                                            className="w-6 h-6 rounded text-muted-foreground hover:text-foreground flex items-center justify-center hover:bg-muted/60 transition-colors cursor-pointer"
                                            title="Copy direct stream link"
                                          >
                                            <Copy className="w-3 h-3" />
                                          </button>
                                        </div>
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )
              )}
            </div>
          )}

          {/* Videos Grid / Table Content (Shown when in all, hd, or flat mode for series/movies) */}
          {((filter !== 'series' && filter !== 'movies') || (filter === 'series' && seriesViewStyle === 'flat') || (filter === 'movies' && movieViewStyle === 'flat')) && (
            <>
              {loading && videos.length === 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="rounded-2xl border border-border/60 bg-card/40 overflow-hidden space-y-3 p-3">
                    <Skeleton className="w-full aspect-[16/10] rounded-xl" />
                    <Skeleton className="h-4 w-3/4 rounded" />
                    <Skeleton className="h-3 w-1/2 rounded" />
                    <div className="grid grid-cols-3 gap-2 pt-2">
                      <Skeleton className="h-7 rounded" />
                      <Skeleton className="h-7 rounded" />
                      <Skeleton className="h-7 rounded" />
                    </div>
                  </div>
                ))}
              </div>
            ) : filteredVideos.length === 0 ? (
              <div className="p-16 text-center rounded-2xl border border-dashed border-border/80 bg-card/20 flex flex-col items-center justify-center gap-2">
                <Film className="w-10 h-10 text-muted-foreground/40 mb-1" />
                <p className="text-sm font-semibold text-foreground">No Videos Found</p>
                <p className="text-xs text-muted-foreground max-w-sm">
                  No video files found matching your search. Choose a different Telegram channel or click Sync Archive.
                </p>
              </div>
            ) : viewMode !== 'files' ? (
            <div
              className={`grid ${
                viewMode === 'dense'
                  ? isChannelDockOpen
                    ? 'grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6 gap-4'
                    : 'grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 3xl:grid-cols-7 gap-4'
                  : isChannelDockOpen
                  ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-5'
                  : 'grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-5'
              }`}
            >
              {dateGroups.map((group) => {
                const groupSelected = group.videos.length > 0 && group.videos.every((v) => selectedIds.has(v.message_id));
                return (
                  <React.Fragment key={`date-group-${group.key}`}>
                    {/* Date Break Line */}
                    <div className="col-span-full my-6 flex items-center gap-3 select-none">
                      <div className="h-px flex-1 bg-gradient-to-r from-transparent via-border to-border/70" />
                      <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold bg-card/90 text-foreground border border-border shadow-xs backdrop-blur-md">
                        <Calendar className="w-3.5 h-3.5 text-primary shrink-0" />
                        <span>{group.label}</span>
                        {group.subLabel && (
                          <span className="text-[10px] text-muted-foreground font-mono font-normal">({group.subLabel})</span>
                        )}
                        <span className="text-[10px] text-muted-foreground font-mono bg-muted/60 px-2 py-0.5 rounded-full font-normal">
                          {group.videos.length} {group.videos.length === 1 ? 'file' : 'files'} • {formatBytes(group.totalSize)}
                        </span>
                        <button
                          onClick={() => handleToggleSelectDateGroup(group.videos)}
                          title={groupSelected ? `Deselect all on ${group.label}` : `Select all on ${group.label}`}
                          className={`ml-1 px-1.5 py-0.5 rounded text-[10px] font-mono transition-colors cursor-pointer border ${
                            groupSelected
                              ? 'bg-primary text-primary-foreground border-primary font-bold'
                              : 'bg-muted/40 text-muted-foreground hover:text-foreground border-border/60 hover:bg-muted'
                          }`}
                        >
                          {groupSelected ? 'Deselect' : 'Select'}
                        </button>
                      </div>
                      <div className="h-px flex-1 bg-gradient-to-l from-transparent via-border to-border/70" />
                    </div>

                    {/* Infinite Video Cards for this Date */}
                    {group.videos.map((video) => {
                      const media = parseMediaInfo(video.filename);
                      const thumbUrl = `/stream/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/thumb`;
                      const isSelected = selectedIds.has(video.message_id);
                      const isDense = viewMode === 'dense';
                      const fileSize = video.size || video.file_size || 0;
                      const resolution =
                        media.resolution ||
                        video.resolution ||
                        (video.height
                          ? (video.height >= 2160
                            ? '4K'
                            : video.height >= 1080
                            ? '1080p'
                            : video.height >= 720
                            ? '720p'
                            : `${video.height}p`)
                          : null);
                      const ext = (video.filename.split('.').pop() || '').toUpperCase();

                      return (
                        <div
                          key={`${video.chat_id}_${video.message_id}`}
                          style={{ contentVisibility: 'auto', containIntrinsicSize: '0 280px' }}
                          className={`group relative rounded-2xl border bg-card/85 backdrop-blur-md overflow-hidden shadow-xs hover:shadow-xl transition-all duration-200 flex flex-col justify-between ${
                      isSelected
                        ? 'border-primary ring-2 ring-primary/40 bg-card'
                        : 'border-border/70 hover:border-primary/50'
                    }`}
                  >
                    {/* Poster Thumbnail Frame */}
                    <div className="relative aspect-[16/10] bg-zinc-950/90 overflow-hidden flex items-center justify-center">
                      <img
                        src={thumbUrl}
                        alt={`Poster frame for ${media.cleanTitle}`}
                        loading="lazy"
                        decoding="async"
                        className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                        onError={(e) => {
                          (e.currentTarget as HTMLElement).style.display = 'none';
                        }}
                      />

                      {/* Art Fallback when thumbnail is missing */}
                      <div className="absolute inset-0 flex flex-col items-center justify-center p-3 text-center bg-gradient-to-br from-zinc-900 via-zinc-950 to-zinc-900 pointer-events-none -z-0">
                        <Clapperboard className="w-8 h-8 mb-2 text-primary/30" />
                        <span className="text-[11px] font-semibold text-zinc-400 line-clamp-2 px-2">
                          {media.cleanTitle}
                        </span>
                      </div>

                      {/* Ambient Gradient Overlay */}
                      <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent pointer-events-none z-10" />

                      {/* Top Badges & Selection Checkbox */}
                      <div className="absolute top-2.5 left-2.5 right-2.5 flex items-center justify-between z-20">
                        <div className="flex items-center gap-1 flex-wrap">
                          {resolution && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-extrabold uppercase bg-emerald-500/90 text-white shadow-xs backdrop-blur-md tracking-wider">
                              {resolution}
                            </span>
                          )}
                          {ext && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-mono uppercase bg-black/60 text-zinc-300 border border-white/10 backdrop-blur-md">
                              {ext}
                            </span>
                          )}
                          {media.audio && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase bg-black/60 text-emerald-400 border border-emerald-500/30 backdrop-blur-md">
                              {media.audio}
                            </span>
                          )}
                          {media.year && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-black/60 text-white/90 border border-white/10 backdrop-blur-md">
                              {media.year}
                            </span>
                          )}
                        </div>

                        <button
                          onClick={() => toggleSelectVideo(video.message_id)}
                          aria-label={isSelected ? `Deselect ${media.cleanTitle}` : `Select ${media.cleanTitle} for playlist`}
                          aria-pressed={isSelected}
                          className={`w-7 h-7 rounded-lg flex items-center justify-center transition-all cursor-pointer backdrop-blur-md focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none ${
                            isSelected
                              ? 'bg-primary text-primary-foreground shadow-md ring-2 ring-primary/50'
                              : 'bg-black/60 text-white/70 border border-white/20 hover:scale-105 hover:text-white'
                          }`}
                          title={isSelected ? 'Deselect video' : 'Select for playlist'}
                        >
                          {isSelected ? <Check className="w-3.5 h-3.5 stroke-[3]" /> : null}
                        </button>
                      </div>

                      {/* Bottom Duration & Size Badges */}
                      <div className="absolute bottom-2.5 left-2.5 right-2.5 text-[10px] flex items-center justify-between z-20">
                        <span className="font-mono text-zinc-300 font-medium bg-black/60 px-1.5 py-0.5 rounded backdrop-blur-md border border-white/10">
                          {formatBytes(fileSize)}
                        </span>
                        <span className="px-1.5 py-0.5 rounded-md font-mono font-medium bg-black/75 text-white backdrop-blur-md border border-white/10">
                          {formatDuration(video.duration)}
                        </span>
                      </div>

                      {/* Watched Progress Indicator */}
                      {(() => {
                        const cardProg = watchProgressMap.get(`${video.chat_id}_${video.message_id}`);
                        const cardPct = cardProg ? (cardProg.percent !== undefined ? cardProg.percent : (cardProg.progress_percent !== undefined ? Math.round(cardProg.progress_percent) : 0)) : 0;
                        if (cardPct <= 0) return null;
                        return (
                          <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/60 z-20">
                            <div
                              className="bg-amber-400 h-full rounded-r transition-all"
                              style={{ width: `${Math.max(5, cardPct)}%` }}
                              title={`Watched: ${cardPct}%`}
                            />
                          </div>
                        );
                      })()}

                      {/* Center Hover Action Controls */}
                      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-200 bg-black/50 backdrop-blur-[2px] gap-2.5 z-30">
                        <button
                          onClick={() => handleStartPlay(video)}
                          aria-label={`Stream ${media.cleanTitle} in browser`}
                          className="w-11 h-11 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-xl hover:scale-110 active:scale-95 transition-all cursor-pointer focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none"
                          title="Stream in Browser (Auto-Resumes)"
                        >
                          <Play className="w-4 h-4 fill-current ml-0.5" />
                        </button>
                        <button
                          onClick={() => setQrModalVideo(video)}
                          aria-label={`Stream ${media.cleanTitle} to mobile or smart TV via QR`}
                          className="w-9 h-9 rounded-full bg-zinc-900/90 text-white flex items-center justify-center shadow-lg hover:scale-110 active:scale-95 transition-all cursor-pointer border border-white/20 focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none"
                          title="Stream to Mobile / Smart TV (QR)"
                        >
                          <QrCode className="w-4 h-4 text-amber-400" />
                        </button>
                      </div>
                    </div>

                    {/* Card Content & Action Bar */}
                    <div className="p-3.5 space-y-2.5 flex-1 flex flex-col justify-between">
                      <div>
                        <div
                          className="text-xs font-semibold text-foreground line-clamp-1 leading-snug hover:text-primary transition-colors cursor-pointer"
                          title={video.filename}
                          onClick={() => handleStartPlay(video)}
                        >
                          {media.cleanTitle}
                        </div>

                        <div className="flex items-center gap-1.5 mt-1 text-[11px] text-muted-foreground flex-wrap">
                          {media.seasonEpisode && (
                            <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 border-primary/30 text-primary bg-primary/5 font-semibold">
                              {media.seasonEpisode}
                            </Badge>
                          )}
                          {video.date ? (
                            <span className="text-[10px] text-muted-foreground/80 font-mono flex items-center gap-1 bg-muted/40 px-1.5 py-0.5 rounded">
                              <Calendar className="w-2.5 h-2.5 text-muted-foreground/60" />
                              {formatMediaDate(video.date)}
                            </span>
                          ) : null}
                          <span className="text-[10px] text-muted-foreground/60 truncate font-mono max-w-full" title={video.filename}>
                            {video.filename}
                          </span>
                        </div>
                      </div>

                      {/* Clean 3-Action Bar: Stream (Primary), VLC (Direct), Dropdown Menu (More) */}
                      <div className="flex items-center gap-1.5 pt-2 border-t border-border/40">
                        <Button
                          size="sm"
                          variant="default"
                          onClick={() => handleStartPlay(video)}
                          aria-label={`Stream ${media.cleanTitle}`}
                          className="h-8 flex-1 gap-1.5 text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 cursor-pointer shadow-xs"
                        >
                          <Play className="w-3.5 h-3.5 fill-current" />
                          <span>Stream</span>
                        </Button>

                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => void handlePlayInVlc(video)}
                          aria-label={`Play ${media.cleanTitle} in desktop VLC`}
                          className="h-8 px-2.5 text-xs font-medium gap-1 text-orange-400 hover:text-orange-300 hover:bg-orange-500/10 transition-colors cursor-pointer"
                          title="Play immediately in desktop VLC"
                        >
                          <PlayCircle className="w-3.5 h-3.5" />
                          <span>VLC</span>
                        </Button>

                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-label="More media options"
                              className="h-8 w-8 p-0 text-muted-foreground hover:text-foreground hover:bg-muted/60 cursor-pointer"
                            >
                              <MoreVertical className="w-4 h-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-56 bg-card/95 border-border shadow-xl backdrop-blur-md">
                            <DropdownMenuItem
                              onClick={() => setQrModalVideo(video)}
                              className="cursor-pointer gap-2 py-2 text-xs"
                            >
                              <QrCode className="w-4 h-4 text-amber-400 shrink-0" />
                              <span>Mobile / TV (QR Code)</span>
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() => void handleTriggerFdm(video)}
                              className="cursor-pointer gap-2 py-2 text-xs"
                            >
                              <ExternalLink className="w-4 h-4 text-emerald-400 shrink-0" />
                              <span>Send to FDM Downloader</span>
                            </DropdownMenuItem>
                            <DropdownMenuItem asChild className="cursor-pointer gap-2 py-2 text-xs">
                              <a
                                href={`/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`}
                                download={video.filename}
                                className="flex items-center gap-2 w-full text-foreground"
                              >
                                <Download className="w-4 h-4 text-sky-400 shrink-0" />
                                <span>Direct File Download</span>
                              </a>
                            </DropdownMenuItem>
                            <DropdownMenuSeparator className="bg-border/50" />
                            <DropdownMenuItem
                              onClick={() => handleCopyStreamLink(video)}
                              className="cursor-pointer gap-2 py-2 text-xs"
                            >
                              <Copy className="w-4 h-4 text-muted-foreground shrink-0" />
                              <span>Copy Direct Stream URL</span>
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>
                  </div>
                );
              })}
            </React.Fragment>
          );
        })}
      </div>
    ) : (
            /* Files Explorer Table View */
            <div className="rounded-2xl border border-border/70 bg-card/80 backdrop-blur-md overflow-hidden shadow-sm">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted/40 border-b border-border text-muted-foreground font-semibold">
                    <tr>
                      <th className="py-3 px-3 w-10 text-center">
                        <button
                          onClick={handleSelectAll}
                          className="w-4 h-4 rounded border border-muted-foreground/40 inline-flex items-center justify-center cursor-pointer"
                          title="Select / Deselect All Filtered Videos"
                        >
                          {filteredVideos.length > 0 && filteredVideos.every((v) => selectedIds.has(v.message_id)) && (
                            <Check className="w-3 h-3 text-primary stroke-[3]" />
                          )}
                        </button>
                      </th>
                      <th className="py-3 px-3">Title & Media Details</th>
                      <th className="py-3 px-3 w-28">Format / Quality</th>
                      <th className="py-3 px-3 w-24">Duration</th>
                      <th className="py-3 px-3 w-24">Size</th>
                      <th className="py-3 px-3 w-28">Date</th>
                      <th className="py-3 px-3 w-48 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {dateGroups.map((group) => {
                      const groupSelected = group.videos.length > 0 && group.videos.every((v) => selectedIds.has(v.message_id));
                      return (
                        <React.Fragment key={`date-group-table-${group.key}`}>
                          {/* Date Break Line in Table */}
                          <tr className="bg-muted/15 select-none hover:bg-muted/15">
                            <td colSpan={7} className="py-2.5 px-4">
                              <div className="flex items-center gap-3">
                                <div className="h-px flex-1 bg-gradient-to-r from-transparent via-border to-border/70" />
                                <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold bg-card/90 text-foreground border border-border shadow-xs backdrop-blur-md">
                                  <Calendar className="w-3.5 h-3.5 text-primary shrink-0" />
                                  <span>{group.label}</span>
                                  {group.subLabel && (
                                    <span className="text-[10px] text-muted-foreground font-mono font-normal">({group.subLabel})</span>
                                  )}
                                  <span className="text-[10px] text-muted-foreground font-mono bg-muted/60 px-2 py-0.5 rounded-full font-normal">
                                    {group.videos.length} {group.videos.length === 1 ? 'file' : 'files'} • {formatBytes(group.totalSize)}
                                  </span>
                                  <button
                                    onClick={() => handleToggleSelectDateGroup(group.videos)}
                                    title={groupSelected ? `Deselect all on ${group.label}` : `Select all on ${group.label}`}
                                    className={`ml-1 px-1.5 py-0.5 rounded text-[10px] font-mono transition-colors cursor-pointer border ${
                                      groupSelected
                                        ? 'bg-primary text-primary-foreground border-primary font-bold'
                                        : 'bg-muted/40 text-muted-foreground hover:text-foreground border-border/60 hover:bg-muted'
                                    }`}
                                  >
                                    {groupSelected ? 'Deselect' : 'Select'}
                                  </button>
                                </div>
                                <div className="h-px flex-1 bg-gradient-to-l from-transparent via-border to-border/70" />
                              </div>
                            </td>
                          </tr>

                          {/* Infinite Video Rows for this Date */}
                          {group.videos.map((video) => {
                            const media = parseMediaInfo(video.filename);
                            const isSelected = selectedIds.has(video.message_id);
                            const fileSize = video.size || video.file_size || 0;
                            const resolution =
                              media.resolution ||
                              video.resolution ||
                              (video.height
                                ? (video.height >= 2160
                                  ? '4K'
                                  : video.height >= 1080
                                  ? '1080p'
                                  : video.height >= 720
                                  ? '720p'
                                  : `${video.height}p`)
                                : null);
                            const ext = (video.filename.split('.').pop() || '').toUpperCase();

                            return (
                              <tr
                                key={`${video.chat_id}_${video.message_id}`}
                                style={{ contentVisibility: 'auto', containIntrinsicSize: '0 44px' }}
                                className={`hover:bg-muted/30 transition-colors ${
                                  isSelected ? 'bg-primary/5' : ''
                                }`}
                              >
                                <td className="py-2.5 px-3 text-center">
                                  <button
                                    onClick={() => toggleSelectVideo(video.message_id)}
                                    className={`w-4 h-4 rounded inline-flex items-center justify-center transition-all cursor-pointer ${
                                      isSelected
                                        ? 'bg-primary text-primary-foreground'
                                        : 'border border-border hover:border-primary'
                                    }`}
                                  >
                                    {isSelected ? <Check className="w-3 h-3 stroke-[3]" /> : null}
                                  </button>
                                </td>

                                <td className="py-2.5 px-3">
                                  <div className="font-semibold text-foreground flex items-center gap-2">
                                    <span>{media.cleanTitle}</span>
                                    {media.year && (
                                      <span className="text-[10px] font-mono text-muted-foreground bg-muted px-1.5 py-0.2 rounded">
                                        {media.year}
                                      </span>
                                    )}
                                    {media.seasonEpisode && (
                                      <Badge variant="outline" className="text-[9px] px-1 py-0 h-4 border-primary/30 text-primary">
                                        {media.seasonEpisode}
                                      </Badge>
                                    )}
                                  </div>
                                  <div className="text-[10px] text-muted-foreground/60 truncate max-w-md font-mono mt-0.5" title={video.filename}>
                                    {video.filename}
                                  </div>
                                </td>

                                <td className="py-2.5 px-3">
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    {ext && (
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-muted/80 text-foreground border border-border/70">
                                        {ext}
                                      </span>
                                    )}
                                    {resolution && (
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-extrabold uppercase bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                                        {resolution}
                                      </span>
                                    )}
                                    {media.audio && (
                                      <span className="px-1 py-0.5 rounded text-[9px] font-medium bg-muted text-muted-foreground">
                                        {media.audio}
                                      </span>
                                    )}
                                    {!ext && !resolution && !media.audio && (
                                      <span className="text-muted-foreground text-[10px]">--</span>
                                    )}
                                  </div>
                                </td>

                                <td className="py-2.5 px-3 font-mono text-muted-foreground">
                                  {formatDuration(video.duration)}
                                </td>

                                <td className="py-2.5 px-3 font-mono font-medium text-foreground">
                                  {formatBytes(fileSize)}
                                </td>

                                <td className="py-2.5 px-3 font-mono text-muted-foreground whitespace-nowrap">
                                  {formatMediaDate(video.date) || '--'}
                                </td>

                                <td className="py-2.5 px-3 text-right">
                                  <div className="inline-flex items-center gap-1.5">
                                    <Button
                                      size="sm"
                                      variant="default"
                                      onClick={() => handleStartPlay(video)}
                                      className="h-7 text-xs px-2.5 gap-1.5 cursor-pointer font-medium bg-primary text-primary-foreground hover:opacity-90"
                                      title="Stream in Browser (Auto-Resumes)"
                                    >
                                      <Play className="w-3 h-3 fill-current" />
                                      <span>Stream</span>
                                    </Button>

                                    <Button
                                      size="sm"
                                      variant="secondary"
                                      onClick={() => void handlePlayInVlc(video)}
                                      className="h-7 px-2 text-xs font-medium gap-1 text-orange-400 hover:text-orange-300 hover:bg-orange-500/10 cursor-pointer"
                                      title="Play in Desktop VLC"
                                    >
                                      <PlayCircle className="w-3 h-3" />
                                      <span>VLC</span>
                                    </Button>

                                    <DropdownMenu>
                                      <DropdownMenuTrigger asChild>
                                        <Button
                                          size="sm"
                                          variant="ghost"
                                          className="h-7 w-7 p-0 cursor-pointer text-muted-foreground hover:text-foreground"
                                        >
                                          <MoreVertical className="w-3.5 h-3.5" />
                                        </Button>
                                      </DropdownMenuTrigger>
                                      <DropdownMenuContent align="end" className="w-52 bg-card/95 border-border shadow-xl backdrop-blur-md">
                                        <DropdownMenuItem
                                          onClick={() => setQrModalVideo(video)}
                                          className="cursor-pointer gap-2 py-2 text-xs"
                                        >
                                          <QrCode className="w-3.5 h-3.5 text-amber-400" />
                                          <span>Stream to Mobile / TV (QR)</span>
                                        </DropdownMenuItem>
                                        <DropdownMenuItem
                                          onClick={() => void handleTriggerFdm(video)}
                                          className="cursor-pointer gap-2 py-2 text-xs"
                                        >
                                          <ExternalLink className="w-3.5 h-3.5 text-emerald-400" />
                                          <span>Send to FDM Downloader</span>
                                        </DropdownMenuItem>
                                        <DropdownMenuItem asChild className="cursor-pointer gap-2 py-2 text-xs">
                                          <a
                                            href={`/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`}
                                            download={video.filename}
                                            className="flex items-center gap-2 w-full text-foreground"
                                          >
                                            <Download className="w-3.5 h-3.5 text-sky-400" />
                                            <span>Direct File Download</span>
                                          </a>
                                        </DropdownMenuItem>
                                        <DropdownMenuSeparator className="bg-border/50" />
                                        <DropdownMenuItem
                                          onClick={() => handleCopyStreamLink(video)}
                                          className="cursor-pointer gap-2 py-2 text-xs"
                                        >
                                          <Copy className="w-3.5 h-3.5 text-muted-foreground" />
                                          <span>Copy Direct Stream URL</span>
                                        </DropdownMenuItem>
                                      </DropdownMenuContent>
                                    </DropdownMenu>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Infinite Timeline Archive Summary */}
          {filteredVideos.length > 0 && (
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4 p-4 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-md shadow-sm select-none">
              <div className="text-xs text-muted-foreground flex items-center gap-2">
                <Film className="w-4 h-4 text-primary" />
                <span>
                  Showing all <span className="font-semibold text-foreground">{filteredVideos.length}</span> videos across{' '}
                  <span className="font-semibold text-foreground">{dateGroups.length}</span> date timeline sections
                </span>
              </div>

              <div className="flex items-center gap-3 text-xs text-muted-foreground font-mono">
                <span>
                  Total Volume:{' '}
                  <span className="font-semibold text-foreground">
                    {formatBytes(filteredVideos.reduce((acc, v) => acc + (v.size || v.file_size || 0), 0))}
                  </span>
                </span>
                <span>•</span>
                <button
                  onClick={handleSelectAll}
                  className="text-primary hover:underline font-semibold cursor-pointer"
                >
                  {filteredVideos.length > 0 && filteredVideos.every((v) => selectedIds.has(v.message_id))
                    ? 'Deselect All'
                    : 'Select All'}
                </button>
              </div>
            </div>
          )}
          </>)}
        </div>
      </div>

      {/* Global Modals */}
      <ChatPickerModal
        open={isChatPickerOpen}
        onOpenChange={setChatPickerOpen}
        onSelect={setSelectedChat}
        selectedChatId={selectedChat.id}
      />

      <VideoPlayerModal
        video={previewVideo}
        open={Boolean(previewVideo)}
        onOpenChange={(open) => {
          if (!open) {
            setPreviewVideo(null);
            loadContinueWatching();
          }
        }}
      />

      <MobileQrModal
        video={qrModalVideo}
        open={Boolean(qrModalVideo)}
        onOpenChange={(open) => !open && setQrModalVideo(null)}
      />
    </div>
  );
};
