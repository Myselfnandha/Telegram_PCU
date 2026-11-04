import React, { useEffect, useState, useMemo } from 'react';
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
  Check,
  CheckSquare,
  ChevronLeft,
  ChevronRight,
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
  X,
} from 'lucide-react';

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

  // Client-side pagination to eliminate DOM bloat (e.g. 23,000 down to ~1,500 nodes)
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(() => {
    const saved = localStorage.getItem('cinema_page_size');
    return saved ? Number(saved) : 24;
  });

  const handleSetViewMode = (mode: 'large' | 'dense' | 'files') => {
    setViewMode(mode);
    localStorage.setItem('cinema_view_mode', mode);
  };

  const handleSetPageSize = (size: number) => {
    setPageSize(size);
    setCurrentPage(1);
    localStorage.setItem('cinema_page_size', String(size));
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
    setCurrentPage(1);

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

  // Reset page when search, filter, or sort changes
  useEffect(() => {
    setCurrentPage(1);
  }, [search, filter, sortBy]);

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

  const handleSelectAllCurrentPage = () => {
    const pageIds = paginatedVideos.map((v) => v.message_id);
    const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));

    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allPageSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
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

  // Sorting and Filtering
  const filteredVideos = useMemo(() => {
    return videos
      .filter((v) => {
        const matchesSearch = v.filename.toLowerCase().includes(search.toLowerCase());
        if (!matchesSearch) return false;

        if (filter === 'series') return Boolean(v.series_name || v.season || v.episode);
        if (filter === 'movies') return !v.series_name && !v.season && !v.episode;
        if (filter === 'hd') return v.resolution?.includes('1080') || v.resolution?.includes('4K') || v.resolution?.includes('2160');

        return true;
      })
      .sort((a, b) => {
        if (sortBy === 'newest') return b.message_id - a.message_id;
        if (sortBy === 'oldest') return a.message_id - b.message_id;
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

        {/* Search, Filter, Sort, and View Mode Toolbar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-2 border-t border-border/40">
          <div role="tablist" aria-label="Video category filters" className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
            {(
              [
                { id: 'all', label: 'All Videos' },
                { id: 'movies', label: 'Movies' },
                { id: 'series', label: 'Series / Episodes' },
                { id: 'hd', label: '1080p & 4K' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                role="tab"
                aria-selected={filter === tab.id}
                onClick={() => setFilter(tab.id)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-colors cursor-pointer ${
                  filter === tab.id
                    ? 'bg-primary text-primary-foreground font-semibold shadow-sm'
                    : 'bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-muted'
                }`}
              >
                {tab.label}
              </button>
            ))}
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
                <option value="newest" className="bg-card text-foreground">Newest First</option>
                <option value="oldest" className="bg-card text-foreground">Oldest First</option>
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

          {/* Videos Grid / Table Content */}
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
              {paginatedVideos.map((video) => {
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
                          onClick={handleSelectAllCurrentPage}
                          className="w-4 h-4 rounded border border-muted-foreground/40 inline-flex items-center justify-center cursor-pointer"
                          title="Select / Deselect Current Page"
                        >
                          {paginatedVideos.length > 0 && paginatedVideos.every((v) => selectedIds.has(v.message_id)) && (
                            <Check className="w-3 h-3 text-primary stroke-[3]" />
                          )}
                        </button>
                      </th>
                      <th className="py-3 px-3">Title & Media Details</th>
                      <th className="py-3 px-3 w-28">Format / Quality</th>
                      <th className="py-3 px-3 w-24">Duration</th>
                      <th className="py-3 px-3 w-24">Size</th>
                      <th className="py-3 px-3 w-48 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {paginatedVideos.map((video) => {
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
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Clean Pagination Bar */}
          {filteredVideos.length > 0 && (
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4 p-4 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-md shadow-sm">
              <div className="text-xs text-muted-foreground">
                Showing{' '}
                <span className="font-semibold text-foreground">
                  {pageSize > 0 ? (currentPage - 1) * pageSize + 1 : 1}
                </span>
                –
                <span className="font-semibold text-foreground">
                  {pageSize > 0 ? Math.min(currentPage * pageSize, filteredVideos.length) : filteredVideos.length}
                </span>{' '}
                of <span className="font-semibold text-foreground">{filteredVideos.length}</span> videos
              </div>

              {/* Page Number Buttons */}
              {totalPages > 1 && (
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handlePageChange(currentPage - 1)}
                    disabled={currentPage === 1}
                    className="h-8 px-2.5 text-xs gap-1 cursor-pointer"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">Prev</span>
                  </Button>

                  {generatePagePills(currentPage, totalPages).map((p, idx) =>
                    p === -1 ? (
                      <span key={`ellipsis-${idx}`} className="px-1 text-xs text-muted-foreground">
                        …
                      </span>
                    ) : (
                      <Button
                        key={p}
                        variant={currentPage === p ? 'default' : 'ghost'}
                        size="sm"
                        onClick={() => handlePageChange(p)}
                        className={`h-8 w-8 p-0 text-xs cursor-pointer ${
                          currentPage === p
                            ? 'font-bold shadow-xs'
                            : 'text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        {p}
                      </Button>
                    )
                  )}

                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handlePageChange(currentPage + 1)}
                    disabled={currentPage === totalPages}
                    className="h-8 px-2.5 text-xs gap-1 cursor-pointer"
                  >
                    <span className="hidden sm:inline">Next</span>
                    <ChevronRight className="w-3.5 h-3.5" />
                  </Button>
                </div>
              )}

              {/* Page Size Selector */}
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span>Per page:</span>
                {[24, 48, 96].map((size) => (
                  <button
                    key={size}
                    onClick={() => handleSetPageSize(size)}
                    className={`px-2 py-1 rounded-md text-xs font-mono transition-colors cursor-pointer ${
                      pageSize === size
                        ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                        : 'bg-muted/50 text-muted-foreground hover:text-foreground hover:bg-muted'
                    }`}
                  >
                    {size}
                  </button>
                ))}
                <button
                  onClick={() => handleSetPageSize(0)}
                  className={`px-2 py-1 rounded-md text-xs font-mono transition-colors cursor-pointer ${
                    pageSize === 0
                      ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                      : 'bg-muted/50 text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
                >
                  All
                </button>
              </div>
            </div>
          )}
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
