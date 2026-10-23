import React, { useEffect, useState } from 'react';
import { useApp } from '@/context/AppContext';
import type { CinemaVideo, ContinueWatchingItem } from '@/types';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';
import { cleanFileName, formatBytes, formatDuration, parseMediaInfo } from '@/lib/formatters';
import { scrollRightSideToTop } from '@/lib/scrollUtils';
import { ChatPickerModal } from '@/components/ChatPickerModal';
import { VideoPlayerModal } from '@/components/VideoPlayerModal';
import { MobileQrModal } from '@/components/MobileQrModal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import {
  ArrowDownUp,
  Check,
  CheckSquare,
  ChevronRight,
  Clapperboard,
  Copy,
  Download,
  Film,
  Grid3x3,
  HardDrive,
  LayoutGrid,
  List,
  ListVideo,
  MessageSquare,
  Play,
  PlayCircle,
  QrCode,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Tv,
  X,
} from 'lucide-react';

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
  const [isChatPickerOpen, setChatPickerOpen] = useState(false);
  const [previewVideo, setPreviewVideo] = useState<CinemaVideo | null>(null);
  const [qrModalVideo, setQrModalVideo] = useState<CinemaVideo | null>(null);
  const [launchingVideoId, setLaunchingVideoId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const handleSetViewMode = (mode: 'large' | 'dense' | 'files') => {
    setViewMode(mode);
    localStorage.setItem('cinema_view_mode', mode);
  };

  const loadVideos = async (forceRefresh = false) => {
    setLoading(true);
    try {
      const res = await api.getCinemaVideos(selectedChat.id, forceRefresh);
      setVideos(res.videos || []);
      // Ensure right-side files catalog remains at top upon completing load
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

    // Auto-scroll right side files catalog to top on chat selection
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

  const handleLaunchVlc = async (
    video: { chat_id: string | number; message_id: number; filename: string; playback_seconds?: number },
    playerType = 'vlc'
  ) => {
    setLaunchingVideoId(video.message_id);
    const media = parseMediaInfo(video.filename);
    addToast({
      type: 'info',
      title: 'Launching Player',
      message: `Starting "${media.cleanTitle}" in ${playerType.toUpperCase()}...`,
    });

    try {
      const res = await api.playInVlc({
        chat_id: video.chat_id,
        message_id: video.message_id,
        filename: video.filename,
        player: playerType,
        resume: true,
        start_time: video.playback_seconds && video.playback_seconds > 10 ? video.playback_seconds : undefined,
      });

      if (res.launched) {
        let msg = `Streaming in ${playerType.toUpperCase()}!`;
        if (res.resume_seconds && res.resume_seconds > 0) {
          msg += ` (Resumed at ${formatDuration(res.resume_seconds)})`;
        }
        addToast({ type: 'success', title: 'Player Active', message: msg });
        setTimeout(() => loadContinueWatching(), 1500);
      } else if (res.playlist_url) {
        const a = document.createElement('a');
        a.href = res.playlist_url;
        a.download = `${video.filename}.m3u`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        addToast({
          type: 'info',
          title: 'Playlist Generated',
          message: 'Local player not detected. Downloaded M3U playlist file.',
        });
      }
    } catch (err: any) {
      addToast({ type: 'error', title: 'Launch Error', message: err.message });
    } finally {
      setLaunchingVideoId(null);
    }
  };

  const handlePlayBatchVlc = async () => {
    if (selectedIds.size === 0) return;
    const msgIds = Array.from(selectedIds);
    addToast({
      type: 'info',
      title: 'Batch Playlist',
      message: `Enqueuing ${msgIds.length} video(s) into VLC playlist...`,
    });

    try {
      await api.playBatchVlc(selectedChat.id, msgIds);
      addToast({
        type: 'success',
        title: 'Batch Player Active',
        message: `Playing ${msgIds.length} episodes in VLC!`,
      });
      setSelectedIds(new Set());
    } catch (err: any) {
      addToast({ type: 'error', title: 'Batch Error', message: err.message });
    }
  };

  const handleExportPlaylist = () => {
    if (selectedIds.size === 0) return;
    const selectedVideos = filteredVideos.filter((v) => selectedIds.has(v.message_id));
    const lines = ['#EXTM3U'];
    const origin = window.location.origin;
    for (const v of selectedVideos) {
      const streamUrl = `${origin}/dl/${encodeURIComponent(String(v.chat_id))}/${v.message_id}/${encodeURIComponent(v.filename)}`;
      const subUrl = `${origin}/api/media/subtitles/download/${encodeURIComponent(String(v.chat_id))}/${v.message_id}`;
      lines.push(`#EXTINF:-1,${cleanFileName(v.filename)}`);
      lines.push('#EXTVLCOPT:network-caching=1000');
      lines.push('#EXTVLCOPT:file-caching=1000');
      lines.push('#EXTVLCOPT:http-reconnect');
      lines.push('#EXTVLCOPT:input-fast-seek');
      lines.push('#EXTVLCOPT:prefetch-seek-threshold=4194304');
      lines.push('#EXTVLCOPT:no-mkv-preload-clusters');
      lines.push('#EXTVLCOPT:prefetch-buffer-size=16777216');
      lines.push('#EXTVLCOPT:avcodec-hw=any');
      lines.push('#EXTVLCOPT:deinterlace=-1');
      lines.push('#EXTVLCOPT:deinterlace-mode=auto');
      lines.push('#EXTVLCOPT:sub-language=eng,tam,hin,all');
      lines.push('#EXTVLCOPT:audio-language=tam,eng,hin,tel,mal,kan,all');
      lines.push(`#EXTVLCOPT:input-slave=${subUrl}`);
      lines.push(`#EXTVLCOPT:sub-file=${subUrl}`);
      lines.push(streamUrl);
    }
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
      message: `Downloaded .M3U playlist with ${selectedVideos.length} episode(s) with audio normalizer & subtitles for VLC!`,
    });
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

  const handleRemoveContinueWatching = async (item: ContinueWatchingItem) => {
    try {
      await api.removeContinueWatching(item.chat_id, item.message_id);
      setContinueWatching((prev) =>
        prev.filter((c) => !(String(c.chat_id) === String(item.chat_id) && c.message_id === item.message_id))
      );
    } catch {}
  };

  // Sorting and Filtering
  const filteredVideos = videos
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

  const hasContinueWatching = continueWatching.length > 0;
  const showDock = hasContinueWatching && isContinueWatchingOpen;

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
                Browsing archive from:{' '}
                <button
                  onClick={() => setChatPickerOpen(true)}
                  className="font-medium text-foreground hover:underline cursor-pointer"
                >
                  {selectedChat.name}
                </button>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2.5 flex-wrap">
            {/* Continue Watching Quick Dock Toggle */}
            {hasContinueWatching && (
              <Button
                variant={showDock ? 'secondary' : 'default'}
                size="sm"
                onClick={() => setContinueWatchingOpen(!isContinueWatchingOpen)}
                className="h-9 gap-1.5 cursor-pointer text-xs"
              >
                <PlayCircle className="w-3.5 h-3.5 text-amber-400" />
                <span>Resume ({continueWatching.length})</span>
              </Button>
            )}

            <Button
              variant="outline"
              size="sm"
              onClick={() => setChatPickerOpen(true)}
              className="h-9 gap-1.5 cursor-pointer text-xs"
            >
              <MessageSquare className="w-3.5 h-3.5 text-primary" />
              <span>Change Channel</span>
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
            {/* View Mode Toggle: Large Grid (2-3) vs Dense Grid (3-5) vs Files Table */}
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
                title="Large Grid (2-3 per row)"
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
                title="Dense Grid (3-5 files per row)"
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
              variant="default"
              onClick={handlePlayBatchVlc}
              className="h-8 text-xs gap-1.5 cursor-pointer"
            >
              <ListVideo className="w-3.5 h-3.5" />
              <span>Play Playlist in VLC</span>
            </Button>
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
              variant="ghost"
              onClick={() => setSelectedIds(new Set())}
              className="h-8 text-xs cursor-pointer"
            >
              Clear Selection
            </Button>
          </div>
        </div>
      )}

      {/* Main Workspace: Left Catalog + Right Docked Continue Watching */}
      <div className="flex flex-col lg:flex-row gap-6 items-start w-full">
        {/* Main Files / Catalog Section */}
        <div id="cinema-files-catalog" className="flex-1 min-w-0 w-full space-y-4">
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
            /* Cinematic Grid View: Large (2-3/row) or Dense (3-5/row) */
            <div
              className={`grid ${
                viewMode === 'dense'
                  ? showDock
                    ? 'grid-cols-2 sm:grid-cols-3 md:grid-cols-3 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6 gap-3'
                    : 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 3xl:grid-cols-7 gap-3'
                  : showDock
                  ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-3 gap-4'
                  : 'grid-cols-1 sm:grid-cols-2 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-3 2xl:grid-cols-4 gap-4'
              }`}
            >
              {filteredVideos.map((video) => {
                const media = parseMediaInfo(video.filename);
                const thumbUrl = `/stream/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/thumb`;
                const isLaunching = launchingVideoId === video.message_id;
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
                    className={`group relative ${
                      isDense ? 'rounded-xl' : 'rounded-2xl'
                    } border bg-card/85 backdrop-blur-md overflow-hidden shadow-sm transition-all duration-300 flex flex-col justify-between ${
                      isSelected
                        ? 'border-primary ring-2 ring-primary/40 bg-card'
                        : 'border-border/70 hover:border-emerald-500/60 hover:shadow-xl hover:shadow-emerald-500/5'
                    }`}
                  >
                    {/* Poster Thumbnail / Video Frame */}
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

                      {/* Art Fallback when thumbnail is missing/loading */}
                      <div className="absolute inset-0 flex flex-col items-center justify-center p-3 text-center bg-gradient-to-br from-zinc-900 via-zinc-950 to-emerald-950/40 pointer-events-none -z-0">
                        <Clapperboard className={`${isDense ? 'w-6 h-6 mb-1' : 'w-8 h-8 mb-2'} text-emerald-500/30`} />
                        <span className={`${isDense ? 'text-[10px]' : 'text-[11px]'} font-semibold text-zinc-400 line-clamp-2 px-1`}>
                          {media.cleanTitle}
                        </span>
                      </div>

                      {/* Ambient Gradient Overlay */}
                      <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/25 to-transparent pointer-events-none z-10" />

                      {/* Top Badges & Batch Selection Checkbox */}
                      <div className={`absolute ${isDense ? 'top-2 left-2 right-2' : 'top-2.5 left-2.5 right-2.5'} flex items-center justify-between z-20`}>
                        <div className="flex items-center gap-1 flex-wrap">
                          {resolution && (
                            <span className={`px-1.5 py-0.5 rounded ${isDense ? 'text-[8px]' : 'text-[9px]'} font-extrabold uppercase bg-emerald-500/90 text-white shadow-sm backdrop-blur-md tracking-wider`}>
                              {resolution}
                            </span>
                          )}
                          {ext && (
                            <span className={`px-1.5 py-0.5 rounded ${isDense ? 'text-[8px]' : 'text-[9px]'} font-mono uppercase bg-black/60 text-zinc-300 border border-white/10 backdrop-blur-md`}>
                              {ext}
                            </span>
                          )}
                          {media.audio && (
                            <span className={`px-1.5 py-0.5 rounded ${isDense ? 'text-[8px]' : 'text-[9px]'} font-semibold uppercase bg-black/60 text-emerald-400 border border-emerald-500/30 backdrop-blur-md`}>
                              {media.audio}
                            </span>
                          )}
                          {media.year && (
                            <span className={`px-1.5 py-0.5 rounded ${isDense ? 'text-[8px]' : 'text-[9px]'} font-mono bg-black/60 text-white/90 border border-white/10 backdrop-blur-md`}>
                              {media.year}
                            </span>
                          )}
                        </div>

                        <button
                          onClick={() => toggleSelectVideo(video.message_id)}
                          aria-label={isSelected ? `Deselect ${media.cleanTitle}` : `Select ${media.cleanTitle} for playlist`}
                          aria-pressed={isSelected}
                          className={`${
                            isDense ? 'w-6 h-6 rounded-md' : 'w-7 h-7 rounded-lg'
                          } flex items-center justify-center transition-all cursor-pointer backdrop-blur-md focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none ${
                            isSelected
                              ? 'bg-primary text-primary-foreground shadow-md ring-2 ring-primary/50'
                              : 'bg-black/60 text-white/70 border border-white/20 hover:scale-110 hover:text-white'
                          }`}
                          title={isSelected ? 'Deselect video' : 'Select for playlist'}
                        >
                          {isSelected ? <Check className={`${isDense ? 'w-3 h-3' : 'w-3.5 h-3.5'} stroke-[3]`} /> : null}
                        </button>
                      </div>

                      {/* Bottom Duration & Size Badges */}
                      <div className={`absolute ${isDense ? 'bottom-2 left-2 right-2 text-[9px]' : 'bottom-2.5 left-2.5 right-2.5 text-[10px]'} flex items-center justify-between z-20`}>
                        <span className="font-mono text-zinc-300 font-medium">
                          {formatBytes(fileSize)}
                        </span>
                        <span className="px-1.5 py-0.5 rounded-md font-mono font-medium bg-black/75 text-white backdrop-blur-md border border-white/10">
                          {formatDuration(video.duration)}
                        </span>
                      </div>

                      {/* Center Hover Action Controls */}
                      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-200 bg-black/50 backdrop-blur-[2px] gap-2 z-30">
                        <button
                          onClick={() => handleLaunchVlc(video, 'vlc')}
                          disabled={isLaunching}
                          aria-label={`Stream ${media.cleanTitle} instantly in VLC`}
                          className={`${
                            isDense ? 'w-10 h-10' : 'w-12 h-12'
                          } rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-2xl hover:scale-110 active:scale-95 transition-all cursor-pointer focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none`}
                          title="Stream instantly in VLC"
                        >
                          <Play className={`${isDense ? 'w-4 h-4' : 'w-5 h-5'} fill-current ml-0.5`} />
                        </button>
                        <button
                          onClick={() => setPreviewVideo(video)}
                          aria-label={`Preview ${media.cleanTitle} in browser`}
                          className={`${
                            isDense ? 'w-8 h-8' : 'w-10 h-10'
                          } rounded-full bg-zinc-900/90 text-white flex items-center justify-center shadow-lg hover:scale-110 active:scale-95 transition-all cursor-pointer border border-white/20 focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none`}
                          title="Preview in Browser"
                        >
                          <Tv className={`${isDense ? 'w-3.5 h-3.5' : 'w-4 h-4'}`} />
                        </button>
                      </div>
                    </div>

                    {/* Card Content & Action Bar */}
                    <div className={`${isDense ? 'p-2.5 space-y-2' : 'p-3.5 space-y-3'} flex-1 flex flex-col justify-between`}>
                      <div>
                        <div
                          className="text-xs font-semibold text-foreground line-clamp-1 leading-snug hover:text-primary transition-colors"
                          title={video.filename}
                        >
                          {media.cleanTitle}
                        </div>

                        <div className="flex items-center gap-1.5 mt-1 text-[11px] text-muted-foreground flex-wrap">
                          {media.seasonEpisode && (
                            <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 border-primary/30 text-primary bg-primary/5 font-semibold">
                              {media.seasonEpisode}
                            </Badge>
                          )}
                          <span className="text-[10px] text-muted-foreground/70 truncate max-w-[180px]" title={video.filename}>
                            {video.filename}
                          </span>
                        </div>
                      </div>

                      {/* Action Button Row */}
                      <div className={`grid grid-cols-5 gap-1 ${isDense ? 'pt-1.5' : 'pt-2'} border-t border-border/40`}>
                        <Button
                          size="sm"
                          variant="default"
                          onClick={() => handleLaunchVlc(video, 'vlc')}
                          disabled={isLaunching}
                          aria-label={`Play ${media.cleanTitle} in VLC`}
                          className={`${isDense ? 'h-7 text-[10px] px-1' : 'h-8 text-[11px] px-1.5'} cursor-pointer col-span-2 gap-1 font-medium`}
                        >
                          <Play className="w-3 h-3 fill-current" />
                          <span>{isLaunching ? 'Opening...' : 'Play VLC'}</span>
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setPreviewVideo(video)}
                          aria-label={`Preview ${media.cleanTitle} in browser`}
                          className={`${isDense ? 'h-7 text-[10px] px-1' : 'h-8 text-[11px] px-1'} cursor-pointer font-medium gap-1 hover:bg-primary/10 hover:text-primary transition-colors`}
                          title="Instant Browser Preview"
                        >
                          <Tv className="w-3 h-3 text-primary" />
                          <span>Preview</span>
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setQrModalVideo(video)}
                          aria-label={`Stream ${media.cleanTitle} to mobile or smart TV via QR`}
                          className={`${isDense ? 'h-7 w-7' : 'h-8 w-8'} p-0 text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 cursor-pointer mx-auto`}
                          title="Stream to Mobile / Smart TV (QR & VLC)"
                        >
                          <QrCode className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleCopyStreamLink(video)}
                          aria-label={`Copy stream link for ${media.cleanTitle}`}
                          className={`${isDense ? 'h-7 w-7' : 'h-8 w-8'} p-0 text-muted-foreground hover:text-foreground cursor-pointer mx-auto`}
                          title="Copy Direct Media Stream Link"
                        >
                          <Copy className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            /* Redesigned Files Explorer Table View */
            <div className="rounded-2xl border border-border/70 bg-card/80 backdrop-blur-md overflow-hidden shadow-sm">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted/40 border-b border-border text-muted-foreground font-semibold">
                    <tr>
                      <th className="py-3 px-3 w-10 text-center">
                        <button
                          onClick={handleSelectAll}
                          className="w-4 h-4 rounded border border-muted-foreground/40 inline-flex items-center justify-center cursor-pointer"
                          title="Select / Deselect All"
                        >
                          {selectedIds.size === filteredVideos.length && filteredVideos.length > 0 && (
                            <Check className="w-3 h-3 text-primary stroke-[3]" />
                          )}
                        </button>
                      </th>
                      <th className="py-3 px-3">Title & Media Details</th>
                      <th className="py-3 px-3 w-28">Format / Quality</th>
                      <th className="py-3 px-3 w-24">Duration</th>
                      <th className="py-3 px-3 w-24">Size</th>
                      <th className="py-3 px-3 w-44 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {filteredVideos.map((video) => {
                      const media = parseMediaInfo(video.filename);
                      const isSelected = selectedIds.has(video.message_id);
                      const isLaunching = launchingVideoId === video.message_id;
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
                            <div className="text-[10px] text-muted-foreground/70 truncate max-w-md font-mono mt-0.5" title={video.filename}>
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
                                onClick={() => handleLaunchVlc(video, 'vlc')}
                                disabled={isLaunching}
                                className="h-7 text-xs px-2.5 gap-1 cursor-pointer"
                              >
                                <Play className="w-3 h-3 fill-current" />
                                <span>VLC</span>
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => setPreviewVideo(video)}
                                className="h-7 text-xs px-2.5 gap-1.5 cursor-pointer font-medium hover:bg-primary/10 hover:text-primary transition-colors"
                                title="Instant Browser Preview"
                              >
                                <Tv className="w-3.5 h-3.5 text-primary" />
                                <span>Browser Preview</span>
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setQrModalVideo(video)}
                                className="h-7 w-7 p-0 cursor-pointer text-amber-400 hover:text-amber-300 hover:bg-amber-500/10"
                                title="Stream to Mobile / Smart TV (QR & VLC)"
                              >
                                <QrCode className="w-3.5 h-3.5" />
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => handleCopyStreamLink(video)}
                                className="h-7 w-7 p-0 cursor-pointer text-muted-foreground hover:text-foreground"
                                title="Copy Stream Link"
                              >
                                <Copy className="w-3.5 h-3.5" />
                              </Button>
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
        </div>

        {/* Right-Side Docked Continue Watching Panel */}
        {showDock && (
          <aside className="w-full lg:w-80 xl:w-88 2xl:w-96 shrink-0 lg:sticky lg:top-20 space-y-3 animate-in slide-in-from-right-4 duration-300">
            <div className="p-4 rounded-2xl border border-border/80 bg-card/90 backdrop-blur-xl shadow-lg space-y-3">
              <div className="flex items-center justify-between pb-2 border-b border-border/50">
                <div className="flex items-center gap-2">
                  <PlayCircle className="w-4 h-4 text-amber-400" />
                  <h3 className="text-xs font-semibold text-foreground">Continue Watching</h3>
                  <Badge variant="outline" className="text-[10px] h-4 px-1.5 border-amber-500/30 text-amber-400 bg-amber-500/10">
                    {continueWatching.length}
                  </Badge>
                </div>
                <button
                  onClick={() => setContinueWatchingOpen(false)}
                  className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors cursor-pointer"
                  title="Collapse Continue Watching sidebar"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>

              {/* Scrollable List of Resume Items */}
              <div className="space-y-2 max-h-[calc(100vh-14rem)] overflow-y-auto pr-1 scrollbar-thin">
                {continueWatching.map((item) => {
                  const media = parseMediaInfo(item.filename);
                  const pct = item.percent || (item.total_seconds ? Math.round((item.playback_seconds / item.total_seconds) * 100) : 0);
                  const thumbUrl = item.thumb_url || `/stream/${encodeURIComponent(String(item.chat_id))}/${item.message_id}/thumb`;

                  return (
                    <div
                      key={`${item.chat_id}_${item.message_id}`}
                      className="group relative flex items-center gap-2.5 p-2 rounded-xl border border-border/60 bg-background/60 hover:bg-accent/40 hover:border-border transition-all"
                    >
                      {/* Compact Thumbnail with Progress Bar & Play Overlay */}
                      <div
                        onClick={() =>
                          handleLaunchVlc({
                            chat_id: item.chat_id,
                            message_id: item.message_id,
                            filename: item.filename,
                          })
                        }
                        className="relative w-20 h-12 shrink-0 rounded-lg overflow-hidden bg-zinc-950/80 border border-border/50 group/thumb cursor-pointer shadow-sm"
                        title="Resume playback"
                      >
                        <img
                          src={thumbUrl}
                          alt={`Thumbnail for ${item.filename}`}
                          loading="lazy"
                          decoding="async"
                          className="w-full h-full object-cover group-hover/thumb:scale-105 transition-transform"
                          onError={(e) => {
                            (e.currentTarget as HTMLElement).style.display = 'none';
                          }}
                        />
                        <div className="absolute inset-0 bg-black/25 group-hover/thumb:bg-black/45 flex items-center justify-center transition-colors">
                          <Play className="w-3.5 h-3.5 text-white fill-current opacity-80 group-hover/thumb:opacity-100 group-hover/thumb:scale-110 transition-all drop-shadow" />
                        </div>
                        {/* Resume Progress Bar at bottom */}
                        <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/60">
                          <div
                            className="bg-amber-400 h-full rounded-r transition-all"
                            style={{ width: `${Math.max(5, pct)}%` }}
                          />
                        </div>
                      </div>

                      {/* Title & Timestamp */}
                      <div className="flex-1 min-w-0">
                        <div
                          className="text-xs font-semibold text-foreground truncate cursor-pointer hover:text-primary transition-colors leading-tight"
                          title={item.filename}
                          onClick={() =>
                            handleLaunchVlc({
                              chat_id: item.chat_id,
                              message_id: item.message_id,
                              filename: item.filename,
                            })
                          }
                        >
                          {media.cleanTitle}
                        </div>
                        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground font-mono mt-1">
                          <span>{formatDuration(item.playback_seconds)}</span>
                          <span className="text-muted-foreground/40">•</span>
                          <span className="text-amber-400 font-semibold">{pct}%</span>
                        </div>
                      </div>

                      {/* Quick Actions */}
                      <div className="flex items-center gap-1 shrink-0">
                        <Button
                          size="sm"
                          variant="default"
                          onClick={() =>
                            handleLaunchVlc({
                              chat_id: item.chat_id,
                              message_id: item.message_id,
                              filename: item.filename,
                            })
                          }
                          className="h-7 px-2 text-[11px] gap-1 cursor-pointer font-medium"
                          title="Resume in VLC"
                        >
                          <Play className="w-3 h-3 fill-current" />
                          <span>VLC</span>
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            setPreviewVideo({
                              chat_id: item.chat_id,
                              message_id: item.message_id,
                              filename: item.filename,
                              size: 0,
                              duration: item.total_seconds,
                              thumb_url: item.thumb_url,
                              playback_seconds: item.playback_seconds,
                            })
                          }
                          className="h-7 w-7 p-0 cursor-pointer text-muted-foreground hover:text-foreground"
                          title="Preview in Browser"
                        >
                          <Tv className="w-3.5 h-3.5 text-primary" />
                        </Button>
                        <button
                          onClick={() => handleRemoveContinueWatching(item)}
                          className="w-6 h-6 rounded-md text-muted-foreground/60 hover:text-destructive hover:bg-destructive/10 flex items-center justify-center transition-colors cursor-pointer"
                          title="Dismiss from history"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </aside>
        )}
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
        onOpenChange={(open) => !open && setPreviewVideo(null)}
        onLaunchVlc={(v) => {
          setPreviewVideo(null);
          handleLaunchVlc(v, 'vlc');
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
