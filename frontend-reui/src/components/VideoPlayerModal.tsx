import React, { useRef, useState, useEffect } from 'react';
import type { CinemaVideo } from '@/types';
import { api } from '@/services/api';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cleanFileName, formatBytes, formatDuration } from '@/lib/formatters';
import {
  Clapperboard,
  Download,
  ExternalLink,
  Play,
  Subtitles,
  Volume2,
  X,
  AlertCircle,
  Zap,
} from 'lucide-react';

interface VideoPlayerModalProps {
  video: (CinemaVideo & { playback_seconds?: number }) | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onLaunchVlc: (video: CinemaVideo) => void;
}

export const VideoPlayerModal: React.FC<VideoPlayerModalProps> = ({
  video,
  open,
  onOpenChange,
  onLaunchVlc,
}) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playbackRate, setPlaybackRate] = useState<number>(1);
  const initialSeekDone = useRef(false);
  const lastReportedTime = useRef(0);

  // Buffer & Stream health state (Feature 4)
  const [bufferedPercent, setBufferedPercent] = useState<number>(0);
  const [bufferSecondsAhead, setBufferSecondsAhead] = useState<number>(0);
  const [currentTimeSec, setCurrentTimeSec] = useState<number>(0);

  // Subtitle Controls state (Feature 5)
  const [subtitlesEnabled, setSubtitlesEnabled] = useState<boolean>(true);
  const [subOffset, setSubOffset] = useState<number>(0);
  const [subSize, setSubSize] = useState<'sm' | 'md' | 'lg'>('md');

  // UI OSD & Codec error state
  const [osdMessage, setOsdMessage] = useState<string | null>(null);
  const osdTimerRef = useRef<any>(null);
  const [codecError, setCodecError] = useState<boolean>(false);

  const streamUrl = video ? `/stream/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}` : '';
  const subtitleUrl = video ? `/api/media/subtitles/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/0.vtt` : '';
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
    setSubOffset(0);
    setCodecError(false);

    // Adaptive pre-warming: pre-load initial 10MB chunk in RAM cache
    if (open && video) {
      fetch(`/api/media/prewarm/${encodeURIComponent(String(video.chat_id))}/${video.message_id}`).catch(() => {});
    }
  }, [video?.chat_id, video?.message_id, open]);

  const handleLoadedMetadata = () => {
    setCodecError(false);
    if (!initialSeekDone.current && video?.playback_seconds && video.playback_seconds > 5 && videoRef.current) {
      videoRef.current.currentTime = video.playback_seconds;
      initialSeekDone.current = true;
      showOsd(`Resumed at ${formatDuration(video.playback_seconds)}`);
    }
  };

  const syncProgress = (seconds: number) => {
    if (!video || seconds <= 0) return;
    const duration = videoRef.current?.duration || video.duration || 0;
    const ratio = duration > 0 ? seconds / duration : 0;
    const approxBytes = Math.floor(ratio * (video.size || video.file_size || 0));
    const chunkIdx = Math.floor(approxBytes / (512 * 1024));

    api.updatePlaybackProgress({
      chat_id: video.chat_id,
      message_id: video.message_id,
      filename: video.filename,
      position_seconds: seconds,
      duration_seconds: duration,
      thumbnail_url: video.thumb_url,
      last_chunk_index: chunkIdx,
      last_byte_offset: approxBytes,
    }).catch(() => {});
  };

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const current = Math.floor(videoRef.current.currentTime);
    setCurrentTimeSec(current);

    if (Math.abs(current - lastReportedTime.current) >= 5 && current > 0) {
      lastReportedTime.current = current;
      syncProgress(current);
    }

    // Update buffer visualization
    updateBufferStatus();
  };

  const updateBufferStatus = () => {
    const v = videoRef.current;
    if (!v) return;
    const duration = v.duration || video?.duration || 0;
    if (duration > 0 && v.buffered.length > 0) {
      const maxEnd = v.buffered.end(v.buffered.length - 1);
      setBufferedPercent(Math.min(100, Math.round((maxEnd / duration) * 100)));
      setBufferSecondsAhead(Math.max(0, Math.round(maxEnd - v.currentTime)));
    }
  };

  const handleSpeedChange = (speed: number) => {
    setPlaybackRate(speed);
    if (videoRef.current) {
      videoRef.current.playbackRate = speed;
      showOsd(`${speed}x Speed`);
    }
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

  const handleAdjustSubOffset = (deltaSec: number) => {
    const v = videoRef.current;
    if (!v) return;
    const track = v.textTracks[0];
    if (track && track.cues) {
      for (let i = 0; i < track.cues.length; i++) {
        const cue = track.cues[i];
        cue.startTime += deltaSec;
        cue.endTime += deltaSec;
      }
    }
    setSubOffset((prev) => {
      const next = Math.round((prev + deltaSec) * 100) / 100;
      showOsd(`Subtitles Sync: ${next > 0 ? '+' : ''}${next}s`);
      return next;
    });
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
        if (v.paused) {
          v.play();
          showOsd('Play');
        } else {
          v.pause();
          showOsd('Pause');
        }
      } else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'j') {
        e.preventDefault();
        v.currentTime = Math.max(0, v.currentTime - 5);
        showOsd('-5s');
      } else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'l') {
        e.preventDefault();
        v.currentTime = Math.min(v.duration || 0, v.currentTime + 5);
        showOsd('+5s');
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        v.volume = Math.min(1, Math.round((v.volume + 0.05) * 100) / 100);
        showOsd(`Volume ${Math.round(v.volume * 100)}%`);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        v.volume = Math.max(0, Math.round((v.volume - 0.05) * 100) / 100);
        showOsd(`Volume ${Math.round(v.volume * 100)}%`);
      } else if (e.key.toLowerCase() === 'm') {
        e.preventDefault();
        v.muted = !v.muted;
        showOsd(v.muted ? 'Muted' : 'Unmuted');
      } else if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        if (!document.fullscreenElement) {
          v.requestFullscreen().catch(() => {});
        } else {
          document.exitFullscreen().catch(() => {});
        }
      } else if (e.key.toLowerCase() === 'c') {
        e.preventDefault();
        handleToggleSubtitles();
      } else if (e.key.toLowerCase() === 'z') {
        e.preventDefault();
        handleAdjustSubOffset(-0.25);
      } else if (e.key.toLowerCase() === 'x') {
        e.preventDefault();
        handleAdjustSubOffset(0.25);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, subtitlesEnabled, subOffset]);

  const handleDialogChange = (isOpen: boolean) => {
    if (!isOpen && videoRef.current) {
      syncProgress(Math.floor(videoRef.current.currentTime));
    }
    onOpenChange(isOpen);
  };

  if (!video) return null;

  return (
    <Dialog open={open} onOpenChange={handleDialogChange}>
      <DialogContent className="sm:max-w-4xl bg-black/95 border-border/60 p-0 overflow-hidden shadow-2xl">
        {/* Custom CSS cue styling for subtitle font sizing */}
        <style>{`
          .cinema-video-player::cue {
            font-size: ${subSize === 'sm' ? '14px' : subSize === 'lg' ? '22px' : '18px'};
            background: rgba(0, 0, 0, 0.85);
            color: #ffffff;
            border-radius: 4px;
            padding: 2px 8px;
            text-shadow: 0 1px 3px rgba(0,0,0,0.9);
          }
        `}</style>

        <DialogHeader className="p-4 border-b border-white/10 flex flex-row items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <Clapperboard className="w-5 h-5 text-primary shrink-0" />
            <DialogTitle className="text-sm font-semibold text-white truncate max-w-lg" title={video.filename}>
              {displayName}
            </DialogTitle>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {/* VLC 2-way sync indicator */}
            <span className="hidden sm:inline-flex items-center gap-1 text-[10px] text-emerald-400 font-mono bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              VLC 2-Way Sync
            </span>
            <Button
              variant="default"
              size="sm"
              onClick={() => onLaunchVlc(video)}
              className="h-8 text-xs gap-1.5 cursor-pointer bg-primary text-primary-foreground hover:opacity-90"
              title="Launch in VLC (Progress syncs automatically)"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>Launch in VLC</span>
            </Button>
          </div>
        </DialogHeader>

        {/* Video Player Container with OSD and Codec Error Fallback */}
        <div className="relative aspect-video bg-black flex items-center justify-center overflow-hidden group">
          {/* OSD Feedback Badge */}
          {osdMessage && (
            <div className="absolute top-4 left-1/2 -translate-x-1/2 z-40 px-3.5 py-1 rounded-full bg-black/85 text-white text-xs font-mono font-medium border border-white/20 shadow-xl backdrop-blur-md animate-in fade-in zoom-in-95 pointer-events-none">
              {osdMessage}
            </div>
          )}

          {/* Codec Fallback Overlay */}
          {codecError && (
            <div className="absolute inset-0 bg-black/90 flex flex-col items-center justify-center p-6 text-center space-y-3 z-30 animate-in fade-in">
              <div className="w-12 h-12 rounded-2xl bg-amber-500/20 text-amber-400 flex items-center justify-center">
                <AlertCircle className="w-6 h-6" />
              </div>
              <div className="space-y-1 max-w-md">
                <h3 className="text-sm font-semibold text-white">Browser Codec Unsupported</h3>
                <p className="text-xs text-zinc-400">
                  This video uses 10-bit HEVC/x265 or multi-channel audio that your web browser cannot decode directly.
                </p>
              </div>
              <Button
                onClick={() => onLaunchVlc(video)}
                className="bg-primary text-primary-foreground gap-1.5 text-xs h-9 cursor-pointer"
              >
                <Play className="w-4 h-4 fill-current" />
                <span>Launch in VLC Player (Resumes at current position)</span>
              </Button>
            </div>
          )}

          <video
            ref={videoRef}
            src={streamUrl}
            controls
            autoPlay
            className="w-full h-full object-contain cinema-video-player"
            preload="metadata"
            onLoadedMetadata={handleLoadedMetadata}
            onTimeUpdate={handleTimeUpdate}
            onProgress={updateBufferStatus}
            onError={() => setCodecError(true)}
            onPause={() => videoRef.current && syncProgress(Math.floor(videoRef.current.currentTime))}
          >
            <track
              label="English / Default Subtitles"
              kind="subtitles"
              srcLang="en"
              src={subtitleUrl}
              default
            />
            Your browser does not support HTML5 video streaming.
          </video>
        </div>

        {/* Dual-Layer Buffer & Progress Indicator Bar (Feature 4) */}
        <div className="px-4 py-2 bg-zinc-950/95 border-t border-white/5 flex items-center justify-between text-[11px] font-mono select-none">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-zinc-300 font-semibold">MTProto Fast-Stream</span>
            <span className="text-zinc-600">•</span>
            <span className="text-emerald-400">
              Buffer: {bufferSecondsAhead}s ({bufferedPercent}%)
            </span>
          </div>

          <div className="flex items-center gap-2">
            <div className="w-32 sm:w-44 h-1.5 rounded-full bg-zinc-800 overflow-hidden relative">
              {/* Lookahead MTProto Buffered Range */}
              <div
                className="absolute top-0 bottom-0 left-0 bg-emerald-500/40 transition-all duration-300"
                style={{ width: `${bufferedPercent}%` }}
                title={`Buffered ahead: ${bufferedPercent}%`}
              />
              {/* Watched Progress */}
              <div
                className="absolute top-0 bottom-0 left-0 bg-primary h-full transition-all duration-150"
                style={{
                  width: `${
                    videoRef.current && (videoRef.current.duration || video.duration)
                      ? Math.min(100, (currentTimeSec / (videoRef.current.duration || video.duration || 1)) * 100)
                      : 0
                  }%`,
                }}
              />
            </div>
            <span className="text-zinc-400 text-[10px]">
              {formatDuration(currentTimeSec)} / {formatDuration(video.duration || 0)}
            </span>
          </div>
        </div>

        {/* Video Controls & Meta Footer */}
        <div className="p-4 bg-zinc-950/90 border-t border-white/10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs text-zinc-400">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="font-mono text-zinc-300">{formatBytes(video.size || video.file_size || 0)}</span>
            {video.resolution && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-zinc-800 text-zinc-200">
                {video.resolution}
              </span>
            )}

            {/* Subtitle Sync & Size Controls (Feature 5) */}
            <div className="flex items-center gap-1.5 pl-2 border-l border-white/10">
              <button
                onClick={handleToggleSubtitles}
                className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold transition-colors cursor-pointer flex items-center gap-1 ${
                  subtitlesEnabled
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-zinc-800 text-zinc-400 hover:text-white'
                }`}
                title="Toggle Subtitles (C)"
              >
                <Subtitles className="w-3 h-3" />
                <span>CC</span>
              </button>

              {subtitlesEnabled && (
                <>
                  <div className="flex items-center rounded bg-zinc-900 border border-white/10 overflow-hidden text-[10px] font-mono">
                    <button
                      onClick={() => handleAdjustSubOffset(-0.25)}
                      className="px-1.5 py-0.5 hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors cursor-pointer"
                      title="Shift subtitles -250ms (Z)"
                    >
                      -0.25s
                    </button>
                    <span
                      onClick={() => {
                        handleAdjustSubOffset(-subOffset);
                        showOsd('Subtitles Reset');
                      }}
                      className="px-1.5 py-0.5 text-zinc-300 hover:text-white cursor-pointer select-none"
                      title="Click to reset subtitle sync"
                    >
                      {subOffset === 0 ? 'Sync' : `${subOffset > 0 ? '+' : ''}${subOffset}s`}
                    </span>
                    <button
                      onClick={() => handleAdjustSubOffset(0.25)}
                      className="px-1.5 py-0.5 hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors cursor-pointer"
                      title="Shift subtitles +250ms (X)"
                    >
                      +0.25s
                    </button>
                  </div>

                  {/* Subtitle Font Size */}
                  <div className="flex items-center rounded bg-zinc-900 border border-white/10 overflow-hidden text-[10px] font-mono">
                    {(['sm', 'md', 'lg'] as const).map((sz) => (
                      <button
                        key={sz}
                        onClick={() => setSubSize(sz)}
                        className={`px-1.5 py-0.5 transition-colors cursor-pointer uppercase ${
                          subSize === sz
                            ? 'bg-zinc-700 text-white font-bold'
                            : 'text-zinc-500 hover:text-zinc-300'
                        }`}
                        title={`Subtitle size: ${sz}`}
                      >
                        {sz}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Playback Speed Chips */}
            <div className="flex items-center gap-1 pl-2 border-l border-white/10">
              <span className="text-[10px] text-zinc-500 font-mono">SPEED:</span>
              {[0.75, 1, 1.25, 1.5, 2].map((s) => (
                <button
                  key={s}
                  onClick={() => handleSpeedChange(s)}
                  className={`px-1.5 py-0.5 rounded text-[10px] font-mono transition-colors cursor-pointer ${
                    playbackRate === s
                      ? 'bg-primary text-primary-foreground font-bold'
                      : 'bg-zinc-800 text-zinc-400 hover:text-white'
                  }`}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <a
              href={`/dl/${encodeURIComponent(String(video.chat_id))}/${video.message_id}/${encodeURIComponent(video.filename)}`}
              download={video.filename}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-white transition-colors cursor-pointer text-xs"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download File</span>
            </a>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
