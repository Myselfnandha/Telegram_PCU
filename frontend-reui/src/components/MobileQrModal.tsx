import React, { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { QrCode, Tv, Smartphone, Copy, Download, Check } from 'lucide-react';
import { api } from '../services/api';
import { useApp } from '../context/AppContext';

interface MobileQrModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  video: {
    chat_id: string | number;
    message_id: number;
    filename: string;
  } | null;
}

export function MobileQrModal({ open, onOpenChange, video }: MobileQrModalProps) {
  const { addToast } = useApp();
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [mobileInfo, setMobileInfo] = useState<{
    lan_ip?: string;
    lan_stream_url?: string;
    lan_vlc_url?: string;
    lan_android_intent?: string;
    lan_playlist_url?: string;
    lan_qrcode_url?: string;
    filename?: string;
  } | null>(null);

  useEffect(() => {
    if (!open || !video) {
      setMobileInfo(null);
      return;
    }

    let isMounted = true;
    setLoading(true);

    api
      .getMobilePlaybackInfo(video.chat_id, video.message_id, video.filename)
      .then((data) => {
        if (isMounted) {
          setMobileInfo(data);
        }
      })
      .catch((err) => {
        if (isMounted) {
          addToast({ type: 'error', title: 'Network Hub Error', message: err.message });
        }
      })
      .finally(() => {
        if (isMounted) {
          setLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [open, video]);

  const handleCopy = (text?: string, label: string = 'Link') => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    addToast({
      type: 'success',
      title: 'Copied to Clipboard',
      message: `${label} copied! Paste into VLC or your browser on your phone/TV.`,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md bg-zinc-950/95 border-amber-500/30 text-white backdrop-blur-2xl p-6 shadow-2xl">
        <DialogHeader className="space-y-1">
          <div className="flex items-center gap-2 text-amber-400">
            <Tv className="w-5 h-5" />
            <DialogTitle className="text-base font-semibold">Stream to Mobile / Smart TV (VLC)</DialogTitle>
          </div>
          <DialogDescription className="text-xs text-zinc-400">
            Stream directly over your local Wi-Fi with hardware acceleration and audio normalization.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="py-12 flex flex-col items-center justify-center space-y-3">
            <div className="w-8 h-8 rounded-full border-2 border-amber-500 border-t-transparent animate-spin" />
            <span className="text-xs text-zinc-400">Resolving LAN network interface...</span>
          </div>
        ) : mobileInfo ? (
          <div className="space-y-4 pt-2">
            {/* Scannable QR Code */}
            <div className="flex flex-col items-center justify-center p-4 rounded-xl bg-black/60 border border-white/10 space-y-2.5">
              <div className="p-2.5 rounded-xl bg-white shadow-xl">
                {mobileInfo.lan_qrcode_url ? (
                  <img
                    src={mobileInfo.lan_qrcode_url}
                    alt="VLC Stream QR Code"
                    className="w-48 h-48 object-contain"
                  />
                ) : (
                  <div className="w-48 h-48 flex items-center justify-center text-zinc-400">
                    <QrCode className="w-12 h-12" />
                  </div>
                )}
              </div>
              <p className="text-[11px] text-zinc-400 text-center max-w-xs">
                Scan with your phone's camera, tablet, or Android TV QR scanner to open directly in VLC.
              </p>
              {mobileInfo.lan_ip && (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
                  Wi-Fi Host: {mobileInfo.lan_ip}:8088
                </span>
              )}
            </div>

            {/* Quick Action Buttons */}
            <div className="grid grid-cols-2 gap-2 pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleCopy(mobileInfo.lan_stream_url, 'Stream URL')}
                className="h-9 text-xs gap-1.5 border-white/15 bg-white/5 hover:bg-white/10 text-white cursor-pointer"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>Copy Stream URL</span>
              </Button>

              <a
                href={mobileInfo.lan_playlist_url}
                download={`${mobileInfo.filename || 'stream'}.m3u`}
                className="inline-flex"
              >
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full h-9 text-xs gap-1.5 border-white/15 bg-white/5 hover:bg-white/10 text-white cursor-pointer"
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Download .M3U</span>
                </Button>
              </a>
            </div>

            {/* Direct Open in VLC app */}
            <div className="pt-1">
              <a href={mobileInfo.lan_vlc_url} className="block w-full">
                <Button
                  size="sm"
                  className="w-full h-9 text-xs gap-2 bg-gradient-to-r from-amber-500 to-orange-500 text-black font-semibold hover:opacity-95 shadow-md shadow-amber-500/20 cursor-pointer"
                >
                  <Smartphone className="w-4 h-4 fill-current" />
                  <span>Open in VLC (This Device)</span>
                </Button>
              </a>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
