import React from 'react';
import type { ContinueWatchingItem } from '@/types';
import { cleanFileName, formatDuration, parseMediaInfo } from '@/lib/formatters';
import { Badge } from '@/components/ui/badge';
import { ChevronRight, Play, PlayCircle, X } from 'lucide-react';

interface DockedContinueWatchingProps {
  items: ContinueWatchingItem[];
  onPlay: (item: ContinueWatchingItem) => void;
  onRemove: (item: ContinueWatchingItem) => void;
  onClose: () => void;
}

/**
 * Compact right-docked sidebar for quick playback resumption of recently watched media.
 * Designed with a slim, space-efficient width (w-56 to w-60) and compact card items to maximize files catalog space.
 */
export const DockedContinueWatching: React.FC<DockedContinueWatchingProps> = ({
  items,
  onPlay,
  onRemove,
  onClose,
}) => {
  return (
    <>
      {/* Mobile/Tablet Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-xs z-40 lg:hidden"
        onClick={onClose}
        aria-hidden="true"
      />

      <aside className="fixed inset-y-0 right-0 z-50 w-64 max-w-[80vw] p-2 bg-background/95 backdrop-blur-xl shadow-2xl lg:static lg:z-auto lg:w-56 xl:w-60 lg:p-0 lg:bg-transparent lg:shadow-none shrink-0 h-full flex flex-col min-h-0 animate-in slide-in-from-right-3 duration-200">
        <div className="h-full flex flex-col min-h-0 p-2 rounded-xl border border-border/70 bg-card/85 backdrop-blur-xl shadow-xs space-y-1.5">
          {/* Header Bar */}
          <div className="flex items-center justify-between pb-1.5 border-b border-border/50 shrink-0 px-0.5">
            <div className="flex items-center gap-1.5 min-w-0">
              <PlayCircle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              <h3 className="text-xs font-semibold text-foreground truncate">Continue Watching</h3>
              <Badge
                variant="outline"
                className="text-[9px] h-3.5 px-1 border-amber-500/30 text-amber-400 bg-amber-500/10 font-mono shrink-0"
              >
                {items.length}
              </Badge>
            </div>
            <button
              onClick={onClose}
              className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors cursor-pointer shrink-0"
              title="Collapse continue watching sidebar"
              aria-label="Close continue watching"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* List of Resume Items */}
          {items.length === 0 ? (
            <div className="flex-1 min-h-0 flex flex-col items-center justify-center p-3 text-center gap-1 text-muted-foreground">
              <PlayCircle className="w-6 h-6 text-muted-foreground/30 mb-0.5" />
              <p className="font-medium text-xs text-foreground">No Active Playback</p>
              <p className="text-[10px] text-muted-foreground">Resume watched videos right here.</p>
            </div>
          ) : (
            <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-0.5 scrollbar-thin">
              {items.map((item) => {
                const media = parseMediaInfo(item.filename);
                const seconds = item.playback_seconds || item.last_position_seconds || 0;
                const total = item.total_seconds || item.duration_seconds || 0;
                const pct =
                  item.percent !== undefined
                    ? item.percent
                    : item.progress_percent !== undefined
                    ? Math.round(item.progress_percent)
                    : total > 0
                    ? Math.round((seconds / total) * 100)
                    : 0;
                const thumbUrl =
                  item.thumb_url ||
                  item.thumbnail_url ||
                  `/stream/${encodeURIComponent(String(item.chat_id))}/${item.message_id}/thumb`;

                return (
                  <div
                    key={`${item.chat_id}_${item.message_id}`}
                    onClick={() => onPlay(item)}
                    className="group relative p-1.5 rounded-lg border border-border/60 bg-background/50 hover:bg-accent/40 hover:border-primary/40 transition-all flex items-center gap-2 shadow-2xs cursor-pointer select-none"
                    title={`Click to resume "${media.cleanTitle || cleanFileName(item.filename)}"`}
                  >
                    {/* Compact Thumbnail with Play Overlay & Progress Line */}
                    <div
                      className="relative w-13 h-9 sm:w-14 sm:h-9.5 shrink-0 rounded-md overflow-hidden bg-zinc-950 border border-border/50 group/thumb shadow-2xs"
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
                        <Play className="w-3 h-3 text-white fill-current opacity-85 group-hover/thumb:opacity-100 group-hover/thumb:scale-110 transition-all drop-shadow" />
                      </div>
                      <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/70">
                        <div
                          className="bg-amber-400 h-full rounded-r transition-all"
                          style={{ width: `${Math.max(5, Math.min(100, pct))}%` }}
                        />
                      </div>
                    </div>

                    {/* Metadata */}
                    <div className="flex-1 min-w-0 pr-0.5">
                      <div
                        className="text-[11px] font-semibold text-foreground truncate group-hover:text-primary transition-colors leading-tight"
                        title={item.filename}
                      >
                        {media.cleanTitle || cleanFileName(item.filename)}
                      </div>
                      <div className="flex items-center gap-1 text-[10px] text-muted-foreground font-mono mt-0.5">
                        <span>{formatDuration(seconds)}</span>
                        <span className="text-muted-foreground/40">•</span>
                        <span className="text-amber-400 font-semibold">{pct}%</span>
                      </div>
                    </div>

                    {/* Dismiss Button on Hover */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemove(item);
                      }}
                      className="w-5 h-5 rounded-md text-muted-foreground/40 hover:text-destructive hover:bg-destructive/10 flex items-center justify-center transition-all opacity-0 group-hover:opacity-100 shrink-0 cursor-pointer"
                      title="Dismiss from history"
                      aria-label="Dismiss from history"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </aside>
    </>
  );
};
