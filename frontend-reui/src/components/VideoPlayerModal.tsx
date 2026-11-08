import React, { useRef, useState, useEffect, useCallback } from 'react';
import type { CinemaVideo } from '@/types';
import { api } from '@/services/api';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { cleanFileName, formatBytes, formatDuration } from '@/lib/formatters';
import {
  Clapperboard,
  Download,
  Play,
  Pause,
  RotateCcw,
  RotateCw,
  Subtitles,
  Volume2,
  VolumeX,
  Volume1,
  Maximize2,
  Minimize2,
  AlertCircle,
  Tv,
  ExternalLink,
  SkipBack,
  SkipForward,
} from 'lucide-react';

interface VideoPlayerModalProps {
  video: (CinemaVideo & { playback_seconds?: number }) | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onNext?: () => void;
  onPrevious?: () => void;
  hasNext?: boolean;
  hasPrevious?: boolean;
}

export const VideoPlayerModal: React.FC<VideoPlayerModalProps> = ({
  video,
  open,
  onOpenChange,
  onNext,
  onPrevious,
  hasNext = false,
  hasPrevious = false,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const isDraggingTimeline = useRef(false);

  // Playback state
  const [isPlaying, setIsPlaying] = useState<boolean>(true);
  const [volume, setVolume] = useState<number>(1);
  const [isMuted, setIsMuted] = useState<boolean>(false);
  const prevVolumeRef = useRef<number>(1);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [fitMode, setFitMode] = useState<'contain' | 'cover'>('contain');

  // Time & Buffer tracking
  const [currentTimeSec, setCurrentTimeSec] = useState<number>(0);
  const [bufferedPercent, setBufferedPercent] = useState<number>(0);
  const [bufferSecondsAhead, setBufferSecondsAhead] = useState<number>(0);
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [hoverPct, setHoverPct] = useState<number | null>(null);

  // Subtitles & UI feedback
  const [subtitlesEnabled, setSubtitlesEnabled] = useState<boolean>(true);
  const [osdMessage, setOsdMessage] = useState<string | null>(null);
  const osdTimerRef = useRef<any>(null);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [isTriggeringFdm, setIsTriggeringFdm] = useState<boolean>(false);

  const initialSeekDone = useRef(false);
  const lastReportedTime = useRef(0);

  const duration = (videoRef.current?.duration && isFinite(videoRef.current.duration) && videoRef.current.duration > 0)
    ? videoRef.current.duration
    : (video?.duration || 0);

  const isMkvContainer = video ? (
    Boolean(video.is_mkv) ||
    /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(video.filename)
  ) : false;

  const [forceTransmux, setForceTransmux] = useState<boolean>(false);

  useEffect(() => {
    setForceTransmux(false);
  }, [video?.chat_id, video?.message_id]);

  const useTransmux = isMkvContainer || forceTransmux;

  // Direct MTProto range stream URL or real-time transmuxed stream
  const streamUrl = video
    ? (useTransmux
        ? (video.stream_transmux_url || `/stream/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename.replace(/\.[^/.]+$/, '.mp4'))}`)
        : `/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`)
    : '';
  const subtitleUrl = video
    ? `/api/media/subtitles/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/0.vtt`
    : '';
  const displayName = video ? cleanFileName(video.filename) : '';

  const showOsd = (msg: string) => {
    setOsdMessage(msg);
    if (osdTimerRef.current) clearTimeout(osdTimerRef.current);
    osdTimerRef.current = setTimeout(() => setOsdMessage(null), 1500);
  };

  // Reset state when video changes
  useEffect(() => {
    initialSeekDone.current = false;
    lastReportedTime.current = 0;
    setBufferedPercent(0);
    setBufferSecondsAhead(0);
    setCurrentTimeSec(0);
    setPlaybackError(null);
    setIsPlaying(true);
    setHoverTime(null);
    setHoverPct(null);

    // Adaptive RAM buffer pre-warming on open
    if (open && video) {
      fetch(`/api/media/prewarm/${encodeURIComponent(String(video.chat_id))}/${video.message_id}`).catch(() => {});
    }
  }, [video?.chat_id, video?.message_id, open]);

  const resumeTargetSec = video?.playback_seconds || (video as any)?.last_position_seconds || 0;

  const attemptResume = useCallback(() => {
    if (initialSeekDone.current || !videoRef.current || resumeTargetSec <= 5) return;
    try {
      videoRef.current.currentTime = resumeTargetSec;
      initialSeekDone.current = true;
      showOsd(`Resumed at ${formatDuration(resumeTargetSec)}`);
    } catch (e) {
      console.debug('Resume seek deferred:', e);
    }
  }, [resumeTargetSec]);

  const handleLoadedMetadata = () => {
    setPlaybackError(null);
    if (videoRef.current) {
      videoRef.current.volume = volume;
      videoRef.current.muted = isMuted;
    }
    attemptResume();
  };

  const handleCanPlay = () => {
    if (!initialSeekDone.current && resumeTargetSec > 5) {
      attemptResume();
    }
  };

  const syncProgress = useCallback((seconds: number) => {
    if (!video || seconds <= 0) return;
    const dur = duration > 0 ? duration : 0;
    const ratio = dur > 0 ? seconds / dur : 0;
    const approxBytes = Math.floor(ratio * (video.size || video.file_size || 0));
    const chunkIdx = Math.floor(approxBytes / (512 * 1024));

    api.updatePlaybackProgress({
      chat_id: video.chat_id,
      message_id: video.message_id,
      filename: video.filename,
      position_seconds: seconds,
      duration_seconds: dur,
      thumbnail_url: video.thumb_url,
      last_chunk_index: chunkIdx,
      last_byte_offset: approxBytes,
    }).catch(() => {});
  }, [video, duration]);

  const updateBufferStatus = () => {
    const v = videoRef.current;
    if (!v) return;
    const dur = duration > 0 ? duration : 1;
    if (v.buffered.length > 0) {
      const maxEnd = v.buffered.end(v.buffered.length - 1);
      setBufferedPercent(Math.min(100, Math.round((maxEnd / dur) * 100)));
      setBufferSecondsAhead(Math.max(0, Math.round(maxEnd - v.currentTime)));
    }
  };

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const current = Math.floor(videoRef.current.currentTime);
    setCurrentTimeSec(current);

    // Guard: Prevent initial 0..5s buffer playback from wiping out existing progress > 10s
    if (resumeTargetSec > 10 && !initialSeekDone.current && current < 10) {
      return;
    }

    if (Math.abs(current - lastReportedTime.current) >= 5 && current > 0) {
      lastReportedTime.current = current;
      syncProgress(current);
    }

    updateBufferStatus();
  };

  const handleEnded = () => {
    if (onNext && hasNext) {
      showOsd('Playing next video...');
      onNext();
    } else {
      setIsPlaying(false);
      if (video) {
        api.removeContinueWatching(video.chat_id, video.message_id).catch(() => {});
        showOsd('Completed playback');
      }
    }
  };

  const seekDebounceRef = useRef<any>(null);
  const pendingSeekTargetRef = useRef<number | null>(null);

  const handleSeek = (targetSec: number, immediate: boolean = false) => {
    const total = duration > 0 ? duration : 999999;
    const bounded = Math.max(0, Math.min(total, targetSec));
    pendingSeekTargetRef.current = bounded;
    setCurrentTimeSec(bounded);
    showOsd(`Seek: ${formatDuration(Math.floor(bounded))}`);

    if (seekDebounceRef.current) clearTimeout(seekDebounceRef.current);
    const applySeek = () => {
      const target = pendingSeekTargetRef.current !== null ? pendingSeekTargetRef.current : bounded;
      pendingSeekTargetRef.current = null;
      if (videoRef.current) {
        videoRef.current.currentTime = target;
        videoRef.current.play().catch(() => {});
        setIsPlaying(true);
      }
    };

    if (immediate) {
      applySeek();
    } else {
      seekDebounceRef.current = setTimeout(applySeek, 150);
    }
  };

  const handleSeekDelta = (deltaSec: number) => {
    const currentBase = pendingSeekTargetRef.current !== null
      ? pendingSeekTargetRef.current
      : (videoRef.current?.currentTime ?? currentTimeSec);
    handleSeek(currentBase + deltaSec, false);
  };

  const togglePlayPause = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      v.play().catch(() => {});
      setIsPlaying(true);
      showOsd('Play');
    } else {
      v.pause();
      setIsPlaying(false);
      showOsd('Pause');
    }
  };

  const handleVolumeChange = (newVol: number) => {
    const bounded = Math.max(0, Math.min(1, Math.round(newVol * 100) / 100));
    setVolume(bounded);
    if (videoRef.current) {
      videoRef.current.volume = bounded;
      videoRef.current.muted = bounded === 0;
    }
    setIsMuted(bounded === 0);
    showOsd(`Volume ${Math.round(bounded * 100)}%`);
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    if (isMuted || volume === 0) {
      const next = prevVolumeRef.current > 0 ? prevVolumeRef.current : 1;
      videoRef.current.muted = false;
      videoRef.current.volume = next;
      setVolume(next);
      setIsMuted(false);
      showOsd(`Unmuted (${Math.round(next * 100)}%)`);
    } else {
      prevVolumeRef.current = volume;
      videoRef.current.muted = true;
      setIsMuted(true);
      showOsd('Muted');
    }
  };

  const toggleFullscreen = () => {
    const container = containerRef.current;
    if (!container) return;
    if (!document.fullscreenElement) {
      container.requestFullscreen().catch(() => {});
      setIsFullscreen(true);
    } else {
      document.exitFullscreen().catch(() => {});
      setIsFullscreen(false);
    }
  };

  const toggleFitMode = () => {
    setFitMode((prev) => {
      const next = prev === 'contain' ? 'cover' : 'contain';
      showOsd(next === 'cover' ? 'Fit: Zoom to Fill' : 'Fit: Original 16:9');
      return next;
    });
  };

  const handleToggleSubtitles = () => {
    const v = videoRef.current;
    if (!v) return;
    const track = v.textTracks[0];
    if (track) {
      const next = !subtitlesEnabled;
      track.mode = next ? 'showing' : 'hidden';
      setSubtitlesEnabled(next);
      showOsd(next ? 'Subtitles ON' : 'Subtitles OFF');
    }
  };

  // Timeline Scrubbing Handlers
  const calculateTimelineTime = (clientX: number) => {
    if (!timelineRef.current || duration <= 0) return 0;
    const rect = timelineRef.current.getBoundingClientRect();
    const pos = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return Math.round(pos * duration);
  };

  const handleTimelineMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!timelineRef.current || duration <= 0) return;
    const rect = timelineRef.current.getBoundingClientRect();
    const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    setHoverPct(pos * 100);
    setHoverTime(Math.round(pos * duration));
  };

  const handleTimelineMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    isDraggingTimeline.current = true;
    const initialTarget = calculateTimelineTime(e.clientX);
    setCurrentTimeSec(initialTarget);

    const onMouseMove = (moveEvent: MouseEvent) => {
      if (isDraggingTimeline.current && timelineRef.current && duration > 0) {
        const rect = timelineRef.current.getBoundingClientRect();
        const pos = Math.max(0, Math.min(1, (moveEvent.clientX - rect.left) / rect.width));
        const t = Math.round(pos * duration);
        setHoverPct(pos * 100);
        setHoverTime(t);
        setCurrentTimeSec(t);
      }
    };

    const onMouseUp = (upEvent: MouseEvent) => {
      if (isDraggingTimeline.current) {
        isDraggingTimeline.current = false;
        const finalTarget = calculateTimelineTime(upEvent.clientX);
        handleSeek(finalTarget);
      }
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  };

  const handleTimelineMouseLeave = () => {
    if (!isDraggingTimeline.current) {
      setHoverTime(null);
      setHoverPct(null);
    }
  };

  const handleTouchStart = (e: React.TouchEvent<HTMLDivElement>) => {
    if (e.touches[0]) {
      const target = calculateTimelineTime(e.touches[0].clientX);
      handleSeek(target);
    }
  };

  // Keyboard navigation shortcuts
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName)) return;
      const v = videoRef.current;
      if (!v) return;

      if (e.code === 'Space' || e.key.toLowerCase() === 'k') {
        e.preventDefault();
        togglePlayPause();
      } else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'j') {
        e.preventDefault();
        handleSeekDelta(e.shiftKey ? -30 : -10);
      } else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'l') {
        e.preventDefault();
        handleSeekDelta(e.shiftKey ? 30 : 10);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        handleVolumeChange(volume + 0.05);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        handleVolumeChange(volume - 0.05);
      } else if (e.key.toLowerCase() === 'm') {
        e.preventDefault();
        toggleMute();
      } else if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        toggleFullscreen();
      } else if (e.key.toLowerCase() === 'c') {
        e.preventDefault();
        handleToggleSubtitles();
      } else if (e.key.toLowerCase() === 'z') {
        e.preventDefault();
        toggleFitMode();
      } else if (e.shiftKey && e.key.toLowerCase() === 'n') {
        if (onNext && hasNext) {
          e.preventDefault();
          onNext();
        }
      } else if (e.shiftKey && e.key.toLowerCase() === 'p') {
        if (onPrevious && hasPrevious) {
          e.preventDefault();
          onPrevious();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, currentTimeSec, duration, volume, isMuted, subtitlesEnabled, fitMode, onNext, onPrevious, hasNext, hasPrevious]);

  const [isLaunchingVlc, setIsLaunchingVlc] = useState(false);

  useEffect(() => {
    if (!open && videoRef.current) {
      try {
        videoRef.current.pause();
        videoRef.current.removeAttribute('src');
        videoRef.current.load();
      } catch {}
    }
  }, [open]);

  const handleLaunchInVlc = async () => {
    if (!video) return;
    setIsLaunchingVlc(true);
    if (seekDebounceRef.current) clearTimeout(seekDebounceRef.current);
    try {
      const currentSec = Math.floor(videoRef.current?.currentTime || currentTimeSec);
      syncProgress(currentSec);
      if (videoRef.current) {
        try {
          videoRef.current.pause();
          videoRef.current.removeAttribute('src');
          videoRef.current.load();
        } catch {}
      }
      await api.launchVlc(video.chat_id, video.message_id, video.filename);
      onOpenChange(false);
    } catch (err: any) {
      showOsd(err?.message || 'Failed to launch VLC');
    } finally {
      setIsLaunchingVlc(false);
    }
  };

  const handleTriggerFdm = async () => {
    if (!video) return;
    setIsTriggeringFdm(true);
    try {
      const res = await api.triggerFdm(video.chat_id, video.message_id, video.filename);
      if (res && res.success) {
        showOsd('Dispatched to FDM');
      } else {
        const fullUrl = `${window.location.origin}/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`;
        await navigator.clipboard.writeText(fullUrl);
        showOsd('FDM URL Copied');
      }
    } catch {
      const fullUrl = `${window.location.origin}/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`;
      await navigator.clipboard.writeText(fullUrl).catch(() => {});
      showOsd('FDM Link Copied');
    } finally {
      setIsTriggeringFdm(false);
    }
  };

  const handleDialogChange = (isOpen: boolean) => {
    if (!isOpen) {
      if (seekDebounceRef.current) clearTimeout(seekDebounceRef.current);
      if (videoRef.current) {
        syncProgress(Math.floor(currentTimeSec));
        try {
          videoRef.current.pause();
          videoRef.current.removeAttribute('src');
          videoRef.current.load();
        } catch {}
      }
    }
    onOpenChange(isOpen);
  };

  if (!video) return null;

  const progressPercent = duration > 0 ? Math.min(100, Math.max(0, (currentTimeSec / duration) * 100)) : 0;

  return (
    <Dialog open={open} onOpenChange={handleDialogChange}>
      <DialogContent className="sm:max-w-4xl bg-black/95 border-border/60 p-0 overflow-hidden shadow-2xl">
        {/* Subtitle Cue Styling */}
        <style>{`
          .cinema-video-player::cue {
            font-size: 17px;
            background: rgba(0, 0, 0, 0.85);
            color: #ffffff;
            border-radius: 4px;
            padding: 3px 10px;
            text-shadow: 0 1px 3px rgba(0,0,0,0.9);
          }
        `}</style>

        {/* Modal Header */}
        <DialogHeader className="p-3.5 border-b border-white/10 flex flex-row items-center justify-between">
          <div className="flex items-center gap-2 min-w-0 pr-4">
            <Clapperboard className="w-4 h-4 text-primary shrink-0" />
            <DialogTitle className="text-sm font-semibold text-white truncate max-w-xl" title={video.filename}>
              {displayName}
            </DialogTitle>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleLaunchInVlc}
            disabled={isLaunchingVlc}
            className="h-7 px-2.5 text-xs gap-1.5 border-white/20 bg-white/5 hover:bg-white/10 text-white cursor-pointer shrink-0"
            title="Switch playback seamlessly to VLC Desktop"
          >
            <Tv className="w-3.5 h-3.5 text-amber-400" />
            <span>{isLaunchingVlc ? 'Launching...' : 'Play in VLC'}</span>
          </Button>
        </DialogHeader>

        {/* Video Player Container */}
        <div
          ref={containerRef}
          className="relative aspect-video bg-black flex items-center justify-center overflow-hidden group select-none"
        >
          {/* OSD Feedback Badge */}
          {osdMessage && (
            <div className="absolute top-4 left-1/2 -translate-x-1/2 z-40 px-3.5 py-1 rounded-full bg-black/85 text-white text-xs font-mono font-medium border border-white/20 shadow-xl backdrop-blur-md animate-in fade-in zoom-in-95 pointer-events-none">
              {osdMessage}
            </div>
          )}

          {/* Playback Error Overlay */}
          {playbackError && (
            <div className="absolute inset-0 bg-black/90 flex flex-col items-center justify-center p-6 text-center space-y-3 z-30 animate-in fade-in">
              <div className="w-12 h-12 rounded-2xl bg-amber-500/20 text-amber-400 flex items-center justify-center">
                <AlertCircle className="w-6 h-6" />
              </div>
              <div className="space-y-1 max-w-md">
                <h3 className="text-sm font-semibold text-white">Playback Interrupted</h3>
                <p className="text-xs text-zinc-400">
                  {playbackError}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => {
                    setPlaybackError(null);
                    if (videoRef.current) {
                      videoRef.current.load();
                      videoRef.current.play().catch(() => {});
                    }
                  }}
                  className="bg-primary text-primary-foreground gap-1.5 text-xs h-8 cursor-pointer"
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>Retry Playback</span>
                </Button>
                {video && (
                  <Button
                    variant="outline"
                    onClick={async () => {
                      try {
                        await api.launchVlc(video.chat_id, video.message_id, video.filename);
                        showOsd('Launched in Desktop VLC');
                      } catch (e) {
                        console.debug('Failed to launch VLC:', e);
                      }
                    }}
                    className="border-white/20 text-white gap-1.5 text-xs h-8 cursor-pointer"
                  >
                    <Tv className="w-3.5 h-3.5 text-amber-400" />
                    <span>Launch VLC</span>
                  </Button>
                )}
              </div>
            </div>
          )}

          <video
            ref={videoRef}
            src={open ? streamUrl : undefined}
            autoPlay
            playsInline
            onClick={togglePlayPause}
            className={`w-full h-full ${fitMode === 'cover' ? 'object-cover' : 'object-contain'} cinema-video-player cursor-pointer`}
            preload="metadata"
            onLoadedMetadata={handleLoadedMetadata}
            onCanPlay={handleCanPlay}
            onTimeUpdate={handleTimeUpdate}
            onProgress={updateBufferStatus}
            onPlay={() => setIsPlaying(true)}
            onPause={() => {
              setIsPlaying(false);
              syncProgress(Math.floor(currentTimeSec));
            }}
            onEnded={handleEnded}
            onError={() => {
              if (!useTransmux) {
                setForceTransmux(true);
                showOsd('Auto-switching to real-time transmuxed stream...');
              } else {
                setPlaybackError('The browser could not decode this media stream. Open in Desktop VLC or download directly.');
              }
            }}
          >
            <track
              label="Default Subtitles"
              kind="subtitles"
              srcLang="en"
              src={subtitleUrl}
              default
            />
            Your browser does not support HTML5 video streaming.
          </video>
        </div>

        {/* Interactive Scrubbing Timeline Bar */}
        <div className="bg-zinc-950 px-4 pt-3 pb-1.5 border-t border-white/10 select-none">
          <div
            ref={timelineRef}
            role="slider"
            tabIndex={0}
            aria-label="Video scrubber timeline"
            aria-valuemin={0}
            aria-valuemax={duration > 0 ? duration : 0}
            aria-valuenow={currentTimeSec}
            aria-valuetext={`${formatDuration(currentTimeSec)} of ${formatDuration(duration)}`}
            onMouseDown={handleTimelineMouseDown}
            onMouseMove={handleTimelineMouseMove}
            onMouseLeave={handleTimelineMouseLeave}
            onTouchStart={handleTouchStart}
            className="group/timeline relative w-full h-3 flex items-center cursor-pointer"
          >
            {/* Timeline Hover Timestamp Tooltip */}
            {hoverTime !== null && hoverPct !== null && (
              <div
                style={{ left: `${Math.min(94, Math.max(6, hoverPct))}%` }}
                className="absolute -top-7 -translate-x-1/2 px-2 py-0.5 rounded bg-zinc-900 border border-white/20 text-white text-[10px] font-mono shadow-lg pointer-events-none z-30"
              >
                {formatDuration(hoverTime)}
              </div>
            )}

            {/* Background Track */}
            <div className="w-full h-1.5 group-hover/timeline:h-2 rounded-full bg-zinc-800 transition-all overflow-hidden relative">
              {/* Buffer Range Indicator */}
              <div
                className="absolute top-0 bottom-0 left-0 bg-emerald-500/40 transition-all duration-300"
                style={{ width: `${bufferedPercent}%` }}
                title={`Buffered: ${bufferedPercent}%`}
              />
              {/* Watched Progress Bar */}
              <div
                className="absolute top-0 bottom-0 left-0 bg-primary h-full transition-all duration-75"
                style={{ width: `${progressPercent}%` }}
              />
            </div>

            {/* Scrubber Thumb */}
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full bg-white shadow-md ring-2 ring-primary transition-transform opacity-0 group-hover/timeline:opacity-100 group-hover/timeline:scale-110 pointer-events-none"
              style={{ left: `calc(${progressPercent}% - 7px)` }}
            />
          </div>
        </div>

        {/* Transport Controls Toolbar */}
        <div className="px-4 py-2.5 bg-zinc-950/95 flex flex-wrap items-center justify-between gap-3 text-xs border-t border-white/5 select-none">
          {/* Left: Playback Transport Buttons */}
          <div className="flex items-center gap-1.5">
            {/* Previous Video */}
            {onPrevious && (
              <button
                onClick={onPrevious}
                disabled={!hasPrevious}
                aria-label="Previous video (Shift+P)"
                className="w-8 h-8 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed text-zinc-300 hover:text-white flex items-center justify-center transition-all active:scale-95 cursor-pointer"
                title="Previous video (Shift+P)"
              >
                <SkipBack className="w-3.5 h-3.5" />
              </button>
            )}

            {/* Play/Pause */}
            <button
              onClick={togglePlayPause}
              aria-label={isPlaying ? 'Pause video (Space or K)' : 'Play video (Space or K)'}
              className="w-8 h-8 rounded-lg bg-primary text-primary-foreground hover:opacity-90 flex items-center justify-center transition-all active:scale-95 cursor-pointer shadow-md"
              title={isPlaying ? 'Pause (Space or K)' : 'Play (Space or K)'}
            >
              {isPlaying ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
            </button>

            {/* Next Video */}
            {onNext && (
              <button
                onClick={onNext}
                disabled={!hasNext}
                aria-label="Next video (Shift+N)"
                className="w-8 h-8 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed text-zinc-300 hover:text-white flex items-center justify-center transition-all active:scale-95 cursor-pointer"
                title="Next video (Shift+N)"
              >
                <SkipForward className="w-3.5 h-3.5" />
              </button>
            )}

            {/* Replay -10s */}
            <button
              onClick={() => handleSeekDelta(-10)}
              aria-label="Seek back 10 seconds (Left Arrow or J)"
              className="w-8 h-8 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-zinc-300 hover:text-white flex items-center justify-center transition-all active:scale-95 cursor-pointer"
              title="Seek back 10s (Left Arrow or J)"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>

            {/* Forward +10s */}
            <button
              onClick={() => handleSeekDelta(10)}
              aria-label="Seek forward 10 seconds (Right Arrow or L)"
              className="w-8 h-8 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-zinc-300 hover:text-white flex items-center justify-center transition-all active:scale-95 cursor-pointer"
              title="Seek forward 10s (Right Arrow or L)"
            >
              <RotateCw className="w-3.5 h-3.5" />
            </button>

            {/* Restart from beginning if progressed > 30s */}
            {currentTimeSec > 30 && (
              <button
                onClick={() => {
                  handleSeek(0);
                  if (video) {
                    api.updatePlaybackProgress({
                      chat_id: video.chat_id,
                      message_id: video.message_id,
                      filename: video.filename,
                      position_seconds: 0,
                      duration_seconds: duration,
                    }).catch(() => {});
                  }
                  showOsd('Restarted from beginning');
                }}
                aria-label="Restart from 0:00"
                className="px-2 h-8 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-zinc-300 hover:text-white flex items-center justify-center transition-all active:scale-95 cursor-pointer text-[10px] font-mono"
                title="Restart from 0:00"
              >
                0:00
              </button>
            )}

            {/* Volume Control */}
            <div className="flex items-center gap-1 pl-2 border-l border-white/10">
              <button
                onClick={toggleMute}
                aria-label={isMuted ? 'Unmute (M)' : 'Mute (M)'}
                className="w-7 h-7 rounded hover:bg-zinc-800 text-zinc-300 hover:text-white flex items-center justify-center transition-colors cursor-pointer"
                title="Toggle Mute (M)"
              >
                {isMuted || volume === 0 ? (
                  <VolumeX className="w-4 h-4 text-red-400" />
                ) : volume < 0.5 ? (
                  <Volume1 className="w-4 h-4" />
                ) : (
                  <Volume2 className="w-4 h-4" />
                )}
              </button>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={isMuted ? 0 : volume}
                onChange={(e) => handleVolumeChange(Number(e.target.value))}
                aria-label="Volume Slider"
                className="w-16 h-1.5 bg-zinc-800 rounded-lg appearance-none cursor-pointer accent-primary"
                title={`Volume: ${isMuted ? 0 : Math.round(volume * 100)}%`}
              />
            </div>

            {/* Time Stamp Display */}
            <div className="pl-2 font-mono text-[11px] text-zinc-400 select-none">
              <span className="text-white font-medium">{formatDuration(currentTimeSec)}</span>
              <span className="text-zinc-600 mx-1">/</span>
              <span>{formatDuration(duration)}</span>
            </div>
          </div>

          {/* Right: Subtitles CC, Fullscreen, Download */}
          <div className="flex items-center gap-2">
            {/* Subtitles CC Toggle */}
            <button
              onClick={handleToggleSubtitles}
              className={`px-2.5 py-1 rounded text-[11px] font-mono font-bold transition-colors cursor-pointer flex items-center gap-1.5 ${
                subtitlesEnabled
                  ? 'bg-primary text-primary-foreground shadow-sm'
                  : 'bg-zinc-900 border border-white/10 text-zinc-400 hover:text-white'
              }`}
              title="Toggle Subtitles (C)"
            >
              <Subtitles className="w-3.5 h-3.5" />
              <span>CC</span>
            </button>

            {/* Fit / Fill Mode Toggle */}
            <button
              onClick={toggleFitMode}
              aria-label={`Video fit mode: ${fitMode === 'contain' ? 'Fit Window' : 'Fill Screen'}`}
              className={`px-2.5 py-1 rounded text-[11px] font-mono font-bold transition-colors cursor-pointer flex items-center gap-1 ${
                fitMode === 'cover'
                  ? 'bg-primary text-primary-foreground shadow-sm'
                  : 'bg-zinc-900 border border-white/10 text-zinc-400 hover:text-white'
              }`}
              title="Toggle Fill Screen / Fit (Z)"
            >
              <span>{fitMode === 'cover' ? 'Fill' : 'Fit'}</span>
            </button>

            {/* Fullscreen Button */}
            <button
              onClick={toggleFullscreen}
              aria-label="Toggle Fullscreen (F)"
              className="w-8 h-8 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-zinc-300 hover:text-white flex items-center justify-center transition-colors cursor-pointer"
              title="Fullscreen (F)"
            >
              {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
            </button>

            <a
              href={`/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`}
              download={video.filename}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-zinc-300 hover:text-white transition-colors cursor-pointer text-xs"
              title="Download file directly"
            >
              <Download className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Download</span>
            </a>

            <button
              onClick={handleTriggerFdm}
              disabled={isTriggeringFdm}
              aria-label="Download with Free Download Manager (FDM)"
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/30 hover:bg-emerald-500/25 text-emerald-400 hover:text-emerald-300 transition-colors cursor-pointer text-xs font-semibold disabled:opacity-50"
              title="Download with Free Download Manager (FDM)"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              <span>FDM</span>
            </button>
          </div>
        </div>

        {/* Stream Health & Meta Footer */}
        <div className="px-4 py-2 bg-zinc-950/90 border-t border-white/10 flex items-center justify-between text-[11px] font-mono text-zinc-400">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-zinc-300 font-semibold">
              Direct MTProto Range Stream
            </span>
            <span className="text-zinc-600">•</span>
            <span className="text-emerald-400">
              Buffer Ahead: {bufferSecondsAhead}s ({bufferedPercent}%)
            </span>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-zinc-300">{formatBytes(video.size || video.file_size || 0)}</span>
            {video.resolution && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-zinc-800 text-zinc-200">
                {video.resolution}
              </span>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
