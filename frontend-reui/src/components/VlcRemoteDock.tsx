import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Play,
  Pause,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
  Maximize2,
  Square,
  Tv,
  Zap,
  SkipBack,
  SkipForward,
  Gauge,
} from 'lucide-react';
import { api } from '../services/api';

export interface VlcStatus {
  active: boolean;
  state?: string;
  time?: number;
  length?: number;
  volume?: number;
  volume_percent?: number;
  fullscreen?: boolean;
  rate?: number;
  filename?: string;
  has_batch?: boolean;
  episode_index?: number;
  total_episodes?: number;
}

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

/**
 * Deep hook encapsulating VLC telemetry polling, timer lifecycle, optimistic updates,
 * and command dispatch. Provides a tight, clean interface for callers.
 */
export function useVlcRemote() {
  const [status, setStatus] = useState<VlcStatus>({ active: false });
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const seekDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Leak-safe adaptive polling: avoids timer multiplication during rapid tab switches
  useEffect(() => {
    let isMounted = true;
    let isFetching = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const clearScheduledTimer = () => {
      if (timeoutId !== null) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    const fetchStatus = async () => {
      if (!isMounted) return;
      if (isFetching) return;

      // Pause active polling if tab is hidden to eliminate CPU and network overhead
      if (document.hidden) {
        clearScheduledTimer();
        timeoutId = setTimeout(fetchStatus, 4000);
        return;
      }

      isFetching = true;
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
      } finally {
        isFetching = false;
        if (isMounted) {
          clearScheduledTimer();
          // 1.5s responsive polling when VLC is active, backed off to 7s when idle
          const delay = isActive ? 1500 : 7000;
          timeoutId = setTimeout(fetchStatus, delay);
        }
      }
    };

    fetchStatus();

    const handleVisibility = () => {
      if (!document.hidden && isMounted) {
        clearScheduledTimer();
        if (!isFetching) {
          fetchStatus();
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      isMounted = false;
      clearScheduledTimer();
      if (seekDebounceRef.current) {
        clearTimeout(seekDebounceRef.current);
      }
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, []);

  const handleControl = useCallback(async (command: string, val?: string | number) => {
    try {
      setLoadingAction(command);

      // Optimistic updates for immediate UX responsiveness
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
      } else if (command === 'rate' && typeof val === 'number') {
        setStatus((prev) => ({ ...prev, rate: val }));
      } else if (command === 'volume' && typeof val === 'number') {
        const pct = Math.min(200, Math.max(0, Math.round((val / 256) * 100)));
        setStatus((prev) => ({ ...prev, volume: val, volume_percent: pct }));
      }

      // Fast seek debounce: immediately updates UI, delays RPC by 80ms to prevent flooding VLC
      if (command === 'seek') {
        if (seekDebounceRef.current) {
          clearTimeout(seekDebounceRef.current);
        }
        seekDebounceRef.current = setTimeout(async () => {
          try {
            const res = await api.sendVlcControl(command, val);
            if (res && res.state) {
              setStatus((prev) => ({
                ...prev,
                state: res.state,
                time: res.time !== undefined ? res.time : prev.time,
                length: res.length !== undefined ? res.length : prev.length,
                volume: res.volume !== undefined ? res.volume : prev.volume,
                volume_percent: res.volume_percent !== undefined ? res.volume_percent : prev.volume_percent,
                rate: res.rate !== undefined ? res.rate : prev.rate,
              }));
            }
          } catch (e) {
            console.debug('VLC seek failed:', e);
          } finally {
            setLoadingAction(null);
          }
        }, 80);
        return;
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
          rate: res.rate !== undefined ? res.rate : prev.rate,
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
  }, []);

  const handleCycleSpeed = useCallback(() => {
    const speeds = [1.0, 1.25, 1.5, 2.0];
    const cur = status.rate || 1.0;
    const next = speeds.find((s) => s > cur + 0.05) || speeds[0];
    handleControl('rate', next);
  }, [status.rate, handleControl]);

  return {
    status,
    loadingAction,
    handleControl,
    handleCycleSpeed,
  };
}

export function VlcRemoteDock() {
  const { status, loadingAction, handleControl, handleCycleSpeed } = useVlcRemote();

  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [hoverPct, setHoverPct] = useState<number | null>(null);
  const progressRef = useRef<HTMLDivElement>(null);

  // Keyboard shortcut listener with element focus safety and media hotkeys
  useEffect(() => {
    if (!status.active) return;

    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.closest('input, textarea, select, [contenteditable="true"]') ||
          target.isContentEditable)
      ) {
        return;
      }

      if (e.code === 'Space' || e.key === 'k' || e.key === 'K') {
        e.preventDefault();
        handleControl('pl_pause');
      } else if (e.code === 'ArrowLeft' || e.key === 'j' || e.key === 'J') {
        e.preventDefault();
        handleControl('seek', e.shiftKey ? '-30s' : '-10s');
      } else if (e.code === 'ArrowRight' || e.key === 'l' || e.key === 'L') {
        e.preventDefault();
        handleControl('seek', e.shiftKey ? '+30s' : '+10s');
      } else if (e.key === '[' || e.code === 'BracketLeft') {
        e.preventDefault();
        handleControl('pl_previous');
      } else if (e.key === ']' || e.code === 'BracketRight') {
        e.preventDefault();
        handleControl('pl_next');
      } else if (e.key === 'm' || e.key === 'M') {
        e.preventDefault();
        const currentPct = status.volume_percent ?? 100;
        handleControl('volume', currentPct > 0 ? 0 : 256);
      } else if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        handleControl('fullscreen');
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [status.active, status.volume_percent, handleControl]);

  // Zero DOM rendering when inactive eliminates LCP blocking & memory overhead
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

  // Full WAI-ARIA Slider Keyboard Standards: Arrow keys, Home, End, PageUp, PageDown
  const handleTimelineKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!status.length) return;
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowUp':
        e.preventDefault();
        handleControl('seek', e.shiftKey ? '+30s' : '+10s');
        break;
      case 'ArrowLeft':
      case 'ArrowDown':
        e.preventDefault();
        handleControl('seek', e.shiftKey ? '-30s' : '-10s');
        break;
      case 'PageUp':
        e.preventDefault();
        handleControl('seek', '+60s');
        break;
      case 'PageDown':
        e.preventDefault();
        handleControl('seek', '-60s');
        break;
      case 'Home':
        e.preventDefault();
        handleControl('seek', 0);
        break;
      case 'End':
        e.preventDefault();
        handleControl('seek', Math.max(0, status.length - 1));
        break;
    }
  };

  return (
    <aside
      aria-label="VLC Live Remote Controller"
      style={{ contain: 'layout style', willChange: 'transform, opacity' }}
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[95%] max-w-2xl animate-in fade-in slide-in-from-bottom-4 duration-300 pointer-events-auto"
    >
      <div className="relative rounded-2xl bg-zinc-950/92 backdrop-blur-xl border border-amber-500/30 p-3 shadow-2xl shadow-amber-950/30 text-white select-none">
        {/* Interactive WAI-ARIA Compliant Scrubbing Timeline */}
        <div
          ref={progressRef}
          role="slider"
          aria-label="Playback timeline"
          aria-orientation="horizontal"
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
          title="Click or drag to scrub (Left/Right to seek, Home/End to jump)"
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
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[10px] font-semibold tracking-wider uppercase px-1.5 py-0.2 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30">
                  VLC Remote
                </span>
                {status.episode_index && status.total_episodes ? (
                  <span className="text-[10px] font-semibold tracking-wider uppercase px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                    Ep {status.episode_index}/{status.total_episodes}
                  </span>
                ) : null}
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
            {/* Previous Episode (Batch / Series Mode) */}
            {(status.has_batch || status.episode_index) && (
              <button
                onClick={() => handleControl('pl_previous')}
                disabled={loadingAction === 'pl_previous'}
                aria-label="Previous Episode ( [ )"
                className="min-w-[34px] min-h-[34px] p-1.5 flex items-center justify-center rounded-lg hover:bg-white/10 active:scale-95 text-indigo-300 hover:text-white focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:outline-none transition-all cursor-pointer"
                title="Previous Episode in Playlist ( [ )"
              >
                <SkipBack className="w-4 h-4 fill-current" />
              </button>
            )}

            {/* -30s Seek */}
            <button
              onClick={() => handleControl('seek', '-30s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek backward 30 seconds (Shift + Left Arrow)"
              className="hidden xs:inline-flex items-center justify-center min-w-[34px] min-h-[34px] px-1.5 py-1 rounded-lg hover:bg-white/10 active:scale-95 text-zinc-400 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer text-[10px] font-mono font-semibold"
              title="Seek -30 seconds (Shift + Left Arrow)"
            >
              -30s
            </button>

            {/* -10s Seek */}
            <button
              onClick={() => handleControl('seek', '-10s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek backward 10 seconds (Left Arrow or J)"
              className="min-w-[34px] min-h-[34px] p-1.5 flex items-center justify-center rounded-lg hover:bg-white/10 active:scale-95 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer"
              title="Seek -10 seconds (Left Arrow or J)"
            >
              <RotateCcw className="w-4 h-4" />
            </button>

            {/* Play/Pause Button */}
            <button
              onClick={() => handleControl('pl_pause')}
              disabled={loadingAction === 'pl_pause'}
              aria-label={isPlaying ? 'Pause VLC playback (Space or K)' : 'Play VLC stream (Space or K)'}
              className="min-w-[38px] min-h-[38px] p-2 rounded-xl bg-amber-500 text-black hover:bg-amber-400 active:scale-95 font-semibold focus-visible:ring-2 focus-visible:ring-amber-300 focus-visible:outline-none transition-all shadow-md shadow-amber-500/20 cursor-pointer flex items-center justify-center"
              title={isPlaying ? 'Pause VLC (Space or K)' : 'Play VLC (Space or K)'}
            >
              {isPlaying ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
            </button>

            {/* +10s Seek */}
            <button
              onClick={() => handleControl('seek', '+10s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek forward 10 seconds (Right Arrow or L)"
              className="min-w-[34px] min-h-[34px] p-1.5 flex items-center justify-center rounded-lg hover:bg-white/10 active:scale-95 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer"
              title="Seek +10 seconds (Right Arrow or L)"
            >
              <RotateCw className="w-4 h-4" />
            </button>

            {/* +30s Seek */}
            <button
              onClick={() => handleControl('seek', '+30s')}
              disabled={loadingAction === 'seek'}
              aria-label="Seek forward 30 seconds (Shift + Right Arrow)"
              className="hidden xs:inline-flex items-center justify-center min-w-[34px] min-h-[34px] px-1.5 py-1 rounded-lg hover:bg-white/10 active:scale-95 text-zinc-400 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer text-[10px] font-mono font-semibold"
              title="Seek +30 seconds (Shift + Right Arrow)"
            >
              +30s
            </button>

            {/* Next Episode (Batch / Series Mode) */}
            {(status.has_batch || status.episode_index) && (
              <button
                onClick={() => handleControl('pl_next')}
                disabled={loadingAction === 'pl_next'}
                aria-label="Next Episode ( ] )"
                className="min-w-[34px] min-h-[34px] p-1.5 flex items-center justify-center rounded-lg hover:bg-white/10 active:scale-95 text-indigo-300 hover:text-white focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:outline-none transition-all cursor-pointer"
                title="Next Episode in Playlist ( ] )"
              >
                <SkipForward className="w-4 h-4 fill-current" />
              </button>
            )}

            {/* Speed Rate Cycler */}
            <button
              onClick={handleCycleSpeed}
              aria-label="Cycle playback speed"
              className="min-w-[34px] min-h-[34px] px-1.5 py-1 rounded-lg hover:bg-white/10 active:scale-95 text-amber-300 hover:text-amber-200 focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:outline-none transition-all cursor-pointer text-[10px] font-mono font-bold flex items-center gap-0.5"
              title="Playback Speed (1x, 1.25x, 1.5x, 2x)"
            >
              <Gauge className="w-3 h-3" />
              {status.rate && status.rate !== 1 ? `${status.rate}x` : '1x'}
            </button>

            {/* Volume Quick Control */}
            <div className="hidden sm:flex items-center gap-1 ml-1 pl-1 border-l border-white/10">
              <button
                onClick={() => handleControl('volume', status.volume_percent && status.volume_percent > 0 ? 0 : 256)}
                aria-label={status.volume_percent && status.volume_percent > 0 ? 'Mute audio (M)' : 'Unmute audio (M)'}
                className="min-w-[32px] min-h-[32px] p-1 rounded-lg hover:bg-white/10 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer flex items-center justify-center"
                title="Toggle Mute / 100% (M)"
              >
                {status.volume_percent && status.volume_percent > 0 ? (
                  <Volume2 className="w-3.5 h-3.5" />
                ) : (
                  <VolumeX className="w-3.5 h-3.5 text-red-400" />
                )}
              </button>
              <input
                type="range"
                min="0"
                max="150"
                aria-label="Volume level"
                value={status.volume_percent ?? 100}
                onChange={(e) => handleVolumeChange(Number(e.target.value))}
                className="w-14 h-1.5 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-amber-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-400"
                title={`Volume: ${status.volume_percent ?? 100}%`}
              />
              <span className="text-[9px] font-mono text-zinc-400 w-6 text-right" aria-live="off">
                {status.volume_percent ?? 100}%
              </span>
            </div>

            {/* Fullscreen Toggle */}
            <button
              onClick={() => handleControl('fullscreen')}
              aria-label="Toggle fullscreen mode (F)"
              className="min-w-[32px] min-h-[32px] p-1 rounded-lg hover:bg-white/10 text-zinc-300 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none transition-all cursor-pointer flex items-center justify-center"
              title="Toggle Fullscreen (F)"
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </button>

            {/* Stop VLC Button */}
            <button
              onClick={() => handleControl('pl_stop')}
              aria-label="Stop VLC Player"
              className="min-w-[32px] min-h-[32px] p-1 rounded-lg hover:bg-red-500/20 text-zinc-400 hover:text-red-400 focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:outline-none transition-all cursor-pointer flex items-center justify-center"
              title="Stop VLC Player"
            >
              <Square className="w-3 h-3 fill-current" />
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}

