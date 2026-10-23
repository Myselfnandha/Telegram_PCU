import React, { useEffect, useState } from 'react';
import { useApp } from '@/context/AppContext';
import type { CinemaVideo, SnifferFeedItem } from '@/types';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';
import { cleanFileName, formatBytes, getFileCategory } from '@/lib/formatters';
import { ChatPickerModal } from '@/components/ChatPickerModal';
import { VideoPlayerModal } from '@/components/VideoPlayerModal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Activity,
  Check,
  Clapperboard,
  Copy,
  Download,
  ExternalLink,
  FileArchive,
  FileText,
  FileVideo,
  Filter,
  Music,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Tv,
  X,
} from 'lucide-react';

export const SnifferView: React.FC = () => {
  const { addToast } = useApp();
  const [activeChannels, setActiveChannels] = useState<string[]>([]);
  const [feed, setFeed] = useState<SnifferFeedItem[]>([]);
  const [detectedManagers, setDetectedManagers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [isChatPickerOpen, setChatPickerOpen] = useState(false);
  const [manualChannelInput, setManualChannelInput] = useState('');
  const [search, setSearch] = useState('');
  const [selectedChannelFilter, setSelectedChannelFilter] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<'all' | 'video' | 'audio' | 'document'>('all');
  const [previewVideo, setPreviewVideo] = useState<CinemaVideo | null>(null);

  const loadStatus = async () => {
    try {
      const status = await api.getSnifferStatus();
      setActiveChannels(status.active_channels || []);
      setDetectedManagers(status.detected_managers || {});
    } catch (e) {
      console.debug('Failed to load sniffer status:', e);
    }
  };

  const loadFeed = async () => {
    setLoading(true);
    try {
      const items = await api.getSnifferFeed();
      setFeed(items || []);
    } catch (e) {
      console.debug('Failed to load sniffer feed:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
    loadFeed();

    const unsubFeed = socketService.onSnifferFeed((item) => {
      setFeed((prev) => [item, ...prev]);
      addToast({
        type: 'info',
        title: 'New Media Sniffed',
        message: `${item.filename} received from watched channel.`,
      });
    });

    return () => {
      unsubFeed();
    };
  }, []);

  const handleAddChannel = async (channelIdOrUsername: string) => {
    if (!channelIdOrUsername.trim()) return;
    try {
      await api.addWatchedChannel(channelIdOrUsername.trim());
      addToast({
        type: 'success',
        title: 'Channel Added',
        message: `Watching '${channelIdOrUsername}' for new media.`,
      });
      setManualChannelInput('');
      loadStatus();
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    }
  };

  const handleRemoveChannel = async (channel: string) => {
    try {
      await api.removeWatchedChannel(channel);
      addToast({
        type: 'info',
        title: 'Channel Removed',
        message: `Stopped watching '${channel}'.`,
      });
      loadStatus();
      if (selectedChannelFilter === channel) setSelectedChannelFilter(null);
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    }
  };

  const handleTriggerFdm = async (item: SnifferFeedItem) => {
    const url = item.download_url || `/dl/${item.chat_id}/${item.message_id}/${encodeURIComponent(item.filename)}`;
    try {
      await api.triggerFdmDownload(url);
      addToast({
        type: 'success',
        title: 'Dispatched to FDM',
        message: `Sent "${item.filename}" to Free Download Manager!`,
      });
    } catch (err: any) {
      addToast({
        type: 'warning',
        title: 'Trigger Failed',
        message: 'Could not trigger local manager. Copied link instead.',
      });
      navigator.clipboard.writeText(window.location.origin + url);
    }
  };

  const handlePlayVlc = async (item: SnifferFeedItem) => {
    addToast({
      type: 'info',
      title: 'Launching VLC',
      message: `Starting "${item.filename}" in VLC Media Player...`,
    });
    try {
      const res = await api.playInVlc({
        chat_id: item.chat_id,
        message_id: item.message_id,
        filename: item.filename,
        player: 'vlc',
      });
      if (res.launched) {
        addToast({ type: 'success', title: 'Streaming in VLC', message: `Now playing "${item.filename}".` });
      } else if (res.playlist_url) {
        const a = document.createElement('a');
        a.href = res.playlist_url;
        a.download = `${item.filename}.m3u`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    } catch (err: any) {
      addToast({ type: 'error', title: 'Player Error', message: err.message });
    }
  };

  const handleCopyLink = (item: SnifferFeedItem) => {
    const url =
      item.download_url ||
      `${window.location.origin}/dl/${item.chat_id}/${item.message_id}/${encodeURIComponent(item.filename)}`;
    navigator.clipboard.writeText(url);
    addToast({
      type: 'success',
      title: 'Link Copied',
      message: 'Direct media link copied to clipboard.',
    });
  };

  const handleClearFeed = () => {
    setFeed([]);
    addToast({ type: 'info', title: 'Feed Cleared', message: 'Cleared active media feed view.' });
  };

  const filteredFeed = feed.filter((item) => {
    const matchesSearch = item.filename.toLowerCase().includes(search.toLowerCase());
    if (!matchesSearch) return false;

    if (selectedChannelFilter && String(item.chat_id) !== selectedChannelFilter && item.chat_title !== selectedChannelFilter) {
      return false;
    }

    if (categoryFilter !== 'all') {
      const cat = getFileCategory(item.filename);
      if (categoryFilter === 'video' && cat !== 'video') return false;
      if (categoryFilter === 'audio' && cat !== 'audio') return false;
      if (categoryFilter === 'document' && cat !== 'document' && cat !== 'archive') return false;
    }

    return true;
  });

  const getCategoryIcon = (filename: string) => {
    const cat = getFileCategory(filename);
    switch (cat) {
      case 'video':
        return <FileVideo className="w-4 h-4 text-sky-400" />;
      case 'audio':
        return <Music className="w-4 h-4 text-emerald-400" />;
      case 'archive':
        return <FileArchive className="w-4 h-4 text-amber-400" />;
      default:
        return <FileText className="w-4 h-4 text-muted-foreground" />;
    }
  };

  return (
    <div className="w-full px-4 sm:px-6 lg:px-8 xl:px-10 py-6 space-y-6 animate-in fade-in duration-300">
      {/* Top Banner: Status & Channel Management */}
      <div className="p-5 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-md shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/10 text-emerald-400 flex items-center justify-center shrink-0 shadow-inner">
              <Radio className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <div className="text-sm font-semibold text-foreground flex items-center gap-2">
                <span>Channel Sniffer & FDM Proxy Engine</span>
                <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px]">
                  Listening
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                Automatically catches new media from watched channels and dispatches to your download manager or player.
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="default"
              size="sm"
              onClick={() => setChatPickerOpen(true)}
              className="h-9 gap-1.5 cursor-pointer text-xs"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Pick Channel</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={loadFeed}
              disabled={loading}
              className="h-9 text-xs cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        </div>

        {/* Monitored Channels Badges */}
        <div className="pt-2 border-t border-border/40 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-foreground">
              Watched Channels ({activeChannels.length})
            </span>
            <div className="flex items-center gap-1.5">
              <Input
                placeholder="Or type @channel or ID..."
                value={manualChannelInput}
                onChange={(e) => setManualChannelInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddChannel(manualChannelInput);
                }}
                className="h-7 text-xs w-48"
              />
              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleAddChannel(manualChannelInput)}
                className="h-7 text-xs px-2.5 cursor-pointer"
              >
                Add
              </Button>
            </div>
          </div>

          {activeChannels.length === 0 ? (
            <div className="text-xs text-muted-foreground/70 italic py-1">
              No channels monitored yet. Click "+ Pick Channel" above to start listening.
            </div>
          ) : (
            <div className="flex flex-wrap gap-2 pt-1">
              {activeChannels.map((ch) => {
                const isFiltered = selectedChannelFilter === ch;
                return (
                  <div
                    key={ch}
                    onClick={() => setSelectedChannelFilter(isFiltered ? null : ch)}
                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-mono transition-all cursor-pointer ${
                      isFiltered
                        ? 'border-primary bg-primary/10 text-primary font-semibold ring-1 ring-primary/40'
                        : 'border-border bg-background text-foreground hover:border-border/80'
                    }`}
                    title={isFiltered ? 'Click to clear filter' : 'Click to filter feed'}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    <span>{ch}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRemoveChannel(ch);
                      }}
                      className="text-muted-foreground hover:text-destructive p-0.5 rounded transition-colors cursor-pointer"
                      title="Stop watching"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                );
              })}
              {selectedChannelFilter && (
                <button
                  onClick={() => setSelectedChannelFilter(null)}
                  className="text-xs text-muted-foreground hover:text-foreground underline cursor-pointer self-center"
                >
                  Clear channel filter
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Detected Managers */}
      {Object.keys(detectedManagers).length > 0 && (
        <div className="flex items-center gap-3 p-3 rounded-xl border border-border/40 bg-muted/20 text-xs">
          <Sparkles className="w-4 h-4 text-primary shrink-0" />
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-foreground">Detected External Managers:</span>
            {Object.entries(detectedManagers).map(([mgr, path]) => (
              <Badge key={mgr} variant="secondary" className="text-[10px] uppercase font-mono" title={path}>
                {mgr} (Active)
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Live Dispatched Media Feed */}
      <div className="space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-foreground">Dispatched Media Feed</h2>
            <Badge variant="secondary" className="text-xs h-5 px-2">
              {filteredFeed.length}
            </Badge>
            {feed.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClearFeed}
                className="h-7 text-xs text-muted-foreground hover:text-foreground cursor-pointer px-2 ml-1"
              >
                Clear
              </Button>
            )}
          </div>

          <div className="flex items-center gap-2 w-full sm:w-auto">
            {/* Category Filter Chips */}
            <div className="flex items-center gap-1 overflow-x-auto">
              {(
                [
                  { id: 'all', label: 'All' },
                  { id: 'video', label: 'Videos' },
                  { id: 'audio', label: 'Audio' },
                  { id: 'document', label: 'Docs' },
                ] as const
              ).map((cat) => (
                <button
                  key={cat.id}
                  onClick={() => setCategoryFilter(cat.id)}
                  className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                    categoryFilter === cat.id
                      ? 'bg-primary text-primary-foreground font-semibold'
                      : 'bg-muted/40 text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>

            <div className="relative w-48 sm:w-56 shrink-0">
              <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search feed..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-8 h-8 text-xs"
              />
            </div>
          </div>
        </div>

        {filteredFeed.length === 0 ? (
          <div className="p-16 text-center rounded-2xl border border-dashed border-border/80 bg-card/20 flex flex-col items-center justify-center gap-2">
            <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground mb-1">
              <Radio className="w-6 h-6 opacity-40" />
            </div>
            <p className="text-sm font-semibold text-foreground">Waiting for Media Posts</p>
            <p className="text-xs text-muted-foreground max-w-sm">
              Any file or video posted to your watched channels will immediately appear here with one-click VLC streaming and download triggers.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3.5">
            {filteredFeed.map((item) => {
              const displayName = cleanFileName(item.filename);
              const isVideo = getFileCategory(item.filename) === 'video';

              return (
                <div
                  key={`${item.chat_id}_${item.message_id}_${item.id}`}
                  className="p-4 rounded-xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-3 flex flex-col justify-between hover:border-primary/40 transition-all"
                >
                  <div className="flex items-start gap-3">
                    <div className="w-9 h-9 rounded-lg bg-accent/60 flex items-center justify-center shrink-0 border border-border/40">
                      {getCategoryIcon(item.filename)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <div className="text-xs font-semibold text-foreground line-clamp-1" title={item.filename}>
                          {displayName}
                        </div>
                        <Badge variant="outline" className="text-[10px] shrink-0 bg-primary/10 text-primary border-primary/20">
                          {item.status.toUpperCase()}
                        </Badge>
                      </div>

                      <div className="text-[11px] text-muted-foreground mt-1 flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-emerald-400 font-mono">
                          {item.size_formatted || (item.size ? formatBytes(item.size) : 'File')}
                        </span>
                        <span>•</span>
                        <span>From: {item.chat_title || item.chat_id}</span>
                        {item.manager && (
                          <>
                            <span>•</span>
                            <span>Manager: {item.manager}</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Enhanced Action Buttons Row */}
                  <div className="flex items-center gap-2 pt-2 border-t border-border/40">
                    {isVideo && (
                      <Button
                        variant="default"
                        size="sm"
                        onClick={() => handlePlayVlc(item)}
                        className="h-8 text-xs flex-1 gap-1 cursor-pointer"
                      >
                        <Play className="w-3.5 h-3.5 fill-current" />
                        <span>Stream VLC</span>
                      </Button>
                    )}
                    {isVideo && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setPreviewVideo({
                            chat_id: item.chat_id,
                            message_id: item.message_id,
                            filename: item.filename,
                            size: item.size || 0,
                          })
                        }
                        className="h-8 text-xs px-2.5 cursor-pointer"
                        title="In-Browser Preview"
                      >
                        <Tv className="w-3.5 h-3.5" />
                      </Button>
                    )}
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => handleTriggerFdm(item)}
                      className="h-8 text-xs flex-1 gap-1 cursor-pointer"
                    >
                      <Download className="w-3.5 h-3.5" />
                      <span>Send FDM</span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleCopyLink(item)}
                      className="h-8 w-8 text-muted-foreground hover:text-foreground cursor-pointer shrink-0"
                      title="Copy Link"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <ChatPickerModal
        open={isChatPickerOpen}
        onOpenChange={setChatPickerOpen}
        onSelect={(chat) => handleAddChannel(String(chat.id))}
      />

      <VideoPlayerModal
        video={previewVideo}
        open={Boolean(previewVideo)}
        onOpenChange={(open) => !open && setPreviewVideo(null)}
        onLaunchVlc={(v) => {
          setPreviewVideo(null);
          handlePlayVlc({
            id: String(v.message_id),
            chat_id: v.chat_id,
            message_id: v.message_id,
            filename: v.filename,
            status: 'stream',
          });
        }}
      />
    </div>
  );
};
