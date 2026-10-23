import React, { useState, useEffect, useRef } from 'react';
import { Play, Pause, RotateCcw, RotateCw, Volume2, VolumeX, Maximize2, Square, Tv, Zap } from 'lucide-react';
import { api } from '../services/api';

function formatTime(seconds?: number): string {
  if (!seconds || seconds <= 0) return '00:00';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

export function VlcRemoteDock() {
  const [status, setStatus] = useState<{
    active: boolean;
    state?: string;
    time?: number;
    length?: number;
    volume?: number;
    volume_percent?: number;
    fullscreen?: boolean;
    filename?: string;
  }>({ active: false });

  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [hoverPct, setHoverPct] = useState<number | null>(null);
  const progressRef = useRef<HTMLDivElement>(null);

  // Dynamic adaptive polling: back off when inactive or tab is hidden to eliminate CPU/network waste
  useEffect(() => {
    let isMounted = true;
    let timeoutId: any = null;

    const fetchStatus = async () => {
      // Pause polling if user is on another tab to save battery and network bandwidth
      if (document.hidden) {
        timeoutId = setTimeout(fetchStatus, 4000);
        return;
      }

      let isActive = false;
      try {
        const res = await api.getVlcLiveStatus();
        if (isMounted) {
          setStatus(res);
          isActive = Boolean(res.active);
        }
      } catch {
        if (isMounted) {
          setStatus((prev) => (prev.active ? { ...prev, active: false } : prev));
          isActive = false;
        }
      }

      if (isMounted) {
        // Fast 2s polling when VLC is active, backed off to 7s when idle
        const delay = isActive ? 2000 : 7000;
        timeoutId = setTimeout(fetchStatus, delay);
      }
    };

    fetchStatus();

    const handleVisibility = () => {
      if (!document.hidden) {
        clearTimeout(timeoutId);
        fetchStatus();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      isMounted = false;
      clearTimeout(timeoutId);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, []);

  const handleControl = async (command: string, val?: string | number) => {
    try {
      setLoadingAction(command);
      // Optimistic update for seeking
      if (command === 'seek' && typeof val === 'number') {
        setStatus((prev) => ({ ...prev, time: val }));
      } else if (command === 'seek' && typeof val === 'string') {
        if (val.startsWith('+') || val.startsWith('-')) {
          const delta = parseInt(val.replace('s', ''), 10) || 0;
          setStatus((prev) => ({
            ...prev,
            time: Math.max(0, Math.min(prev.length || 999999, (prev.time || 0) + delta)),
          }));
        }
      }

      const res = await api.sendVlcControl(command, val);
      if (res && res.state) {
        setStatus((prev) => ({
          ...prev,
          state: res.state,
          time: res.time !== undefined ? res.time : prev.time,
          length: res.length !== undefined ? res.length : prev.length,
          volume: res.volume !== undefined ? res.volume : prev.volume,
          volume_percent: res.volume_percent !== undefined ? res.volume_percent : prev.volume_percent,
        }));
      }
      if (command === 'pl_stop') {
        setStatus({ active: false });
      }
    } catch (e) {
      console.debug('VLC control failed:', e);
    } finally {
      setLoadingAction(null);
    }
  };

  // Keyboard shortcut listener for instantaneous seeking & scrubbing
  useEffect(() => {
    if (!status.active) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        return;
      }
      if (e.code === 'Space') {
        e.preventDefault();
        handleControl('pl_pause');
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        if (e.shiftKey) {
          handleControl('seek', '-30s');
        } else {
          handleControl('seek', '-10s');
        }
      } else if (e.code === 'ArrowRight') {
        e.preventDefault();
        if (e.shiftKey) {
          handleControl('seek', '+30s');
        } else {
          handleControl('seek', '+10s');
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [status.active]);

  if (!status.active) {
    return null;
  }

  const isPlaying = status.state === 'playing';
  const progressPercent =
    status.length && status.length > 0
      ? Math.min(100, Math.max(0, ((status.time || 0) / status.length) * 100))
      : 0;

  const handleVolumeChange = (newPct: number) => {
    const rawVal = Math.round((newPct / 100) * 256);
    handleControl('volume', rawVal);
  };

  const handleTimelineMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!progressRef.current || !status.length) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    setHoverPct(pos * 100);
    setHoverTime(Math.round(pos * status.length));
  };

  const handleTimelineMouseLeave = () => {
    setHoverTime(null);
    setHoverPct(null);
  };

  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!progressRef.current || !status.length) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const targetSec = Math.round(pos * status.length);
    handleControl('seek', targetSec);
  };

  const handleTimelineKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      handleControl('seek', e.shiftKey ? '+30s' : '+10s');
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      handleControl('seek', e.shiftKey ? '-30s' : '-10s');
    }
  };

  return (
    <aside
      aria-label="VLC Live Remote Controller"
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[95%] max-w-xl animate-in fade-in slide-in-from-bottom-4 duration-300 pointer-events-auto"
    >
      <div className="relative rounded-2xl bg-zinc-950/92 backdrop-blur-xl border border-amber-500/30 p-3 shadow-2xl shadow-amber-950/30 text-white select-none">
        {/* Interactive Accessible Scrubbing Timeline */}
        <div
          ref={progressRef}
          role="slider"
          aria-label="Playback timeline"
          aria-valuemin={0}
          aria-valuemax={status.length || 0}
          aria-valuenow={status.time || 0}
          aria-valuetext={`${formatTime(status.time)} of ${formatTime(status.length)}`}
          tabIndex={0}
          onClick={handleTimelineClick}
          onMouseMove={handleTimelineMouseMove}
          onMouseLeave={handleTimelineMouseLeave}
          onKeyDown={handleTimelineKeyDown}
          className="group absolute -top-2 left-0 right-0 h-4 flex items-center cursor-pointer z-20 px-2 focus:outline-none"
          title="Click or drag to scrub (Left/Right to seek)"
        >
          {/* Track background */}
          <div className="w-full h-1.5 group-hover:h-2.5 group-focus:h-2.5 rounded-full bg-zinc-800 transition-all duration-150 overflow-hidden relative">
            {/* Active playback fill */}
            <div
              className="h-full bg-gradient-to-r from-amber-500 to-orange-500 transition-all duration-300"
              style={{ width: `${progressPercent}%` }}
            />
          </div>

          {/* Glowing Scrub Head Handle */}
          <div
            className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full bg-amber-400 border-2 border-white shadow-lg shadow-amber-500/80 -translate-x-1/2 opacity-0 group-hover:opacity-100 group-focus:opacity-100 transition-opacity duration-150 pointer-events-none"
            style={{ left: `calc(${progressPercent}% + 8px - (16px * ${progressPercent / 100}))` }}
          />

          {/* Hover Time Tooltip */}
          {hoverTime !== null && hoverPct !== null && (
            <div
              aria-hidden="true"
              className="absolute -top-7 -translate-x-1/2 px-2 py-0.5 rounded bg-zinc-900 border border-amber-500/50 text-[10px] font-mono text-amber-300 shadow-xl pointer-events-none whitespace-nowrap z-30"
              style={{ left: `${hoverPct}%` }}
            >
              {formatTime(hoverTime)}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 pt-1.5">
          {/* File & Status Info */}
          <div className="flex items-center gap-2.5 min-w-0 flex-1">
            <div
              aria-hidden="true"
              className="relative flex-shrink-0 w-8 h-8 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center border border-amber-500/30"
            >
              <Tv className="w-4 h-4" />
              {isPlaying && (
                <span className="absolute -top-0.5 -right-0.5 flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
                </span>
              )}
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-semibold tracking-wider uppercase px-1.5 py-0.2 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30">
                  VLC Remote
                </span>
                <span className="hidden sm:inline-flex items-center gap-0.5 text-[9px] font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-1 py-0.2 rounded">
                  <Zap className="w-2.5 h-2.5" /> Fast Seek
                </span>
                <span className="text-xs font-medium truncate text-zinc-200">
                  {status.filename || 'Streaming in VLC'}
                </span>
              </div>
              <div className="text-[11px] font-mono text-zinc-400 mt-0.5">
                {formatTime(status.time)} / {formatTime(status.length)}
              </div>
            </div>
          </div>

          {/* Quick Playback & Extended Multi-Seek Controls */}
          <div className="flex items-center gap-1 flex-shrink-0">
            {/* -30s Seek */}
            <button
              onClick={() => handleControl('seek', '-30s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek backward 30 seconds (Shift + Left Arrow)"
              className="hidden xs:inline-flex items-center justify-center min-w-[36px] min-h-[36px] px-2 py-1 rounded-lg hover:bg-white/10 active:scale-95 text-zinc-400 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer text-[10px] font-mono font-semibold"
              title="Seek -30 seconds (Shift + Left Arrow)"
            >
              -30s
            </button>

            {/* -10s Seek */}
            <button
              onClick={() => handleControl('seek', '-10s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek backward 10 seconds (Left Arrow)"
              className="min-w-[36px] min-h-[36px] p-2 flex items-center justify-center rounded-lg hover:bg-white/10 active:scale-95 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer"
              title="Seek -10 seconds (Left Arrow)"
            >
              <RotateCcw className="w-4 h-4" />
            </button>

            {/* Play/Pause Button */}
            <button
              onClick={() => handleControl('pl_pause')}
              disabled={loadingAction === 'pl_pause'}
              aria-label={isPlaying ? 'Pause VLC playback (Space)' : 'Play VLC stream (Space)'}
              className="min-w-[40px] min-h-[40px] p-2.5 rounded-xl bg-amber-500 text-black hover:bg-amber-400 active:scale-95 font-semibold focus-visible:ring-2 focus-visible:ring-amber-300 focus-visible:outline-none transition-all shadow-md shadow-amber-500/20 cursor-pointer flex items-center justify-center"
              title={isPlaying ? 'Pause VLC (Space)' : 'Play VLC (Space)'}
            >
              {isPlaying ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
            </button>

            {/* +10s Seek */}
            <button
              onClick={() => handleControl('seek', '+10s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek forward 10 seconds (Right Arrow)"
              className="min-w-[36px] min-h-[36px] p-2 flex items-center justify-center rounded-lg hover:bg-white/10 active:scale-95 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer"
              title="Seek +10 seconds (Right Arrow)"
            >
              <RotateCw className="w-4 h-4" />
            </button>

            {/* +30s Seek */}
            <button
              onClick={() => handleControl('seek', '+30s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek forward 30 seconds (Shift + Right Arrow)"
              className="hidden xs:inline-flex items-center justify-center min-w-[36px] min-h-[36px] px-2 py-1 rounded-lg hover:bg-white/10 active:scale-95 text-zinc-400 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer text-[10px] font-mono font-semibold"
              title="Seek +30 seconds (Shift + Right Arrow)"
            >
              +30s
            </button>

            {/* Volume Quick Control */}
            <div className="hidden sm:flex items-center gap-1 ml-1 pl-1 border-l border-white/10">
              <button
                onClick={() => handleControl('volume', status.volume_percent && status.volume_percent > 0 ? 0 : 256)}
                aria-label={status.volume_percent && status.volume_percent > 0 ? 'Mute audio' : 'Unmute audio'}
                className="min-w-[32px] min-h-[32px] p-1.5 rounded-lg hover:bg-white/10 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer flex items-center justify-center"
                title="Toggle Mute / 100%"
              >
                {status.volume_percent && status.volume_percent > 0 ? (
                  <Volume2 className="w-4 h-4" />
                ) : (
                  <VolumeX className="w-4 h-4 text-red-400" />
                )}
              </button>
              <input
                type="range"
                min="0"
                max="150"
                aria-label="Volume level"
                value={status.volume_percent ?? 100}
                onChange={(e) => handleVolumeChange(Number(e.target.value))}
                className="w-16 h-1.5 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-amber-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-400"
                title={`Volume: ${status.volume_percent ?? 100}%`}
              />
              <span className="text-[10px] font-mono text-zinc-400 w-7 text-right" aria-live="off">
                {status.volume_percent ?? 100}%
              </span>
            </div>

            {/* Fullscreen Toggle */}
            <button
              onClick={() => handleControl('fullscreen')}
              aria-label="Toggle fullscreen mode"
              className="min-w-[32px] min-h-[32px] p-1.5 rounded-lg hover:bg-white/10 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer flex items-center justify-center"
              title="Toggle Fullscreen"
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </button>

            {/* Stop VLC Button */}
            <button
              onClick={() => handleControl('pl_stop')}
              aria-label="Stop VLC Player"
              className="min-w-[32px] min-h-[32px] p-1.5 rounded-lg hover:bg-red-500/20 text-zinc-400 hover:text-red-400 focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:outline-none transition-all cursor-pointer flex items-center justify-center"
              title="Stop VLC Player"
            >
              <Square className="w-3.5 h-3.5 fill-current" />
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
