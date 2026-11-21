import React, { useEffect, useState, useRef } from 'react';
import { Wifi, WifiOff, RefreshCw, Radio } from 'lucide-react';
import { socketService } from '@/services/socket';
import { useApp } from '@/context/AppContext';

export const NetworkWatchdogBanner: React.FC = () => {
  const { addToast } = useApp();
  const [isOnline, setIsOnline] = useState<boolean>(navigator.onLine !== false);
  const [isSocketConnected, setIsSocketConnected] = useState<boolean>(true);
  const [isReconnecting, setIsReconnecting] = useState<boolean>(false);
  const [justReconnected, setJustReconnected] = useState<boolean>(false);
  const [showSocketWarning, setShowSocketWarning] = useState<boolean>(false);
  const [lanIp, setLanIp] = useState<string>('');
  const disconnectTimerRef = useRef<any>(null);

  useEffect(() => {
    let reconnectTimeout: any = null;

    const handleBrowserOnline = () => {
      setIsOnline(true);
      probeConnection();
    };

    const handleBrowserOffline = () => {
      setIsOnline(false);
      setIsReconnecting(false);
      setJustReconnected(false);
    };

    window.addEventListener('online', handleBrowserOnline);
    window.addEventListener('offline', handleBrowserOffline);

    const unsubNetwork = socketService.onNetworkStatus((data) => {
      if (data.online) {
        setIsOnline(true);
        setIsReconnecting(false);
        setJustReconnected(true);
        if (data.ip) setLanIp(data.ip);
        socketService.requestQueueSnapshot();
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(() => {
          setJustReconnected(false);
        }, 3500);
      } else {
        setIsOnline(false);
        setIsReconnecting(false);
        setJustReconnected(false);
      }
    });

    const unsubSocket = socketService.onConnectionChange((connected) => {
      setIsSocketConnected(connected);
      if (connected) {
        setIsReconnecting(false);
        setShowSocketWarning(false);
        if (disconnectTimerRef.current) clearTimeout(disconnectTimerRef.current);
      } else if (navigator.onLine) {
        // Debounce socket warning by 6 seconds to prevent false alarms during token handshake
        if (!disconnectTimerRef.current) {
          disconnectTimerRef.current = setTimeout(() => {
            setShowSocketWarning(true);
          }, 6000);
        }
      }
    });

    return () => {
      window.removeEventListener('online', handleBrowserOnline);
      window.removeEventListener('offline', handleBrowserOffline);
      unsubNetwork();
      unsubSocket();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (disconnectTimerRef.current) clearTimeout(disconnectTimerRef.current);
    };
  }, []);

  const probeConnection = async () => {
    setIsReconnecting(true);
    try {
      const res = await fetch('/api/auth/status', { cache: 'no-store' });
      if (res.ok) {
        setIsOnline(true);
        setIsReconnecting(false);
        setJustReconnected(true);
        socketService.reconnectWithToken();
        addToast({
          type: 'success',
          title: 'Network Active',
          message: 'Connection verified. In-flight operations resumed.',
        });
        setTimeout(() => setJustReconnected(false), 3500);
      }
    } catch {
      setIsReconnecting(false);
      setIsOnline(false);
    }
  };

  // If online, socket connected, and not showing reconnect banner, render nothing
  if (isOnline && (isSocketConnected || !showSocketWarning) && !justReconnected) {
    return null;
  }

  if (justReconnected) {
    return (
      <div className="relative mx-4 mt-3 mb-1 p-3.5 rounded-xl border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 backdrop-blur-md shadow-lg flex items-center justify-between gap-4 animate-in fade-in duration-300">
        <div className="flex items-center gap-3">
          <div className="relative flex items-center justify-center p-2 rounded-lg bg-emerald-500/20">
            <Wifi className="w-5 h-5 text-emerald-400" />
          </div>
          <div>
            <div className="text-sm font-semibold text-emerald-200 flex items-center gap-2">
              <span>Network Reconnected</span>
              <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-emerald-500/30 text-emerald-300">
                Online
              </span>
            </div>
            <div className="text-xs text-emerald-300/80">
              Telegram MTProto restored & in-flight transfers resumed{lanIp ? ` (LAN: ${lanIp})` : ''}.
            </div>
          </div>
        </div>
      </div>
    );
  }

  // True network offline
  if (!isOnline) {
    return (
      <div className="relative mx-4 mt-3 mb-1 p-3.5 rounded-xl border border-rose-500/40 bg-rose-500/10 text-rose-400 backdrop-blur-md shadow-lg flex items-center justify-between gap-4 animate-in fade-in duration-300">
        <div className="flex items-center gap-3">
          <div className="relative flex items-center justify-center p-2 rounded-lg bg-rose-500/20">
            <WifiOff className="w-5 h-5 animate-pulse text-rose-400" />
            <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-rose-500"></span>
            </span>
          </div>
          <div>
            <div className="text-sm font-semibold text-rose-200 flex items-center gap-2">
              <span>Network Disconnected / Carrier Down</span>
              <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-rose-500/30 text-rose-300">
                Offline
              </span>
            </div>
            <div className="text-xs text-rose-300/80">
              Network interface offline. Waiting for internet connection to restore...
            </div>
          </div>
        </div>
        <button
          onClick={probeConnection}
          disabled={isReconnecting}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/20 hover:bg-rose-500/30 text-rose-200 text-xs font-medium border border-rose-500/30 transition-all cursor-pointer disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isReconnecting ? 'animate-spin' : ''}`} />
          <span>{isReconnecting ? 'Reconnecting...' : 'Reconnect'}</span>
        </button>
      </div>
    );
  }

  // Realtime stream reconnecting (subtle amber notice)
  return (
    <div className="relative mx-4 mt-3 mb-1 p-3 rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-300 backdrop-blur-md shadow-lg flex items-center justify-between gap-4 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="p-1.5 rounded-lg bg-amber-500/20 text-amber-400">
          <Radio className="w-4 h-4 animate-pulse" />
        </div>
        <div>
          <div className="text-xs font-semibold text-amber-200 flex items-center gap-2">
            <span>Syncing Realtime Telemetry</span>
            <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-amber-500/30 text-amber-300">
              Connecting
            </span>
          </div>
          <div className="text-[11px] text-amber-300/80">
            Connecting socket for speed gauges and upload progress...
          </div>
        </div>
      </div>
      <button
        onClick={() => socketService.reconnectWithToken()}
        className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 text-xs font-medium border border-amber-500/30 transition-all cursor-pointer"
      >
        <RefreshCw className="w-3 h-3" />
        <span>Sync</span>
      </button>
    </div>
  );
};
