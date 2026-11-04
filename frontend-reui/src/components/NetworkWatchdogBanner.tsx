import React, { useEffect, useState } from 'react';
import { Wifi, WifiOff, RefreshCw } from 'lucide-react';
import { socketService } from '@/services/socket';
import { useApp } from '@/context/AppContext';

export const NetworkWatchdogBanner: React.FC = () => {
  const { addToast } = useApp();
  const [isOnline, setIsOnline] = useState<boolean>(navigator.onLine !== false);
  const [isReconnecting, setIsReconnecting] = useState<boolean>(false);
  const [justReconnected, setJustReconnected] = useState<boolean>(false);
  const [lanIp, setLanIp] = useState<string>('');

  useEffect(() => {
    let reconnectTimeout: any = null;

    const handleBrowserOnline = () => {
      setIsReconnecting(true);
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
      if (!connected && navigator.onLine) {
        setIsReconnecting(true);
      } else if (connected) {
        setIsReconnecting(false);
      }
    });

    return () => {
      window.removeEventListener('online', handleBrowserOnline);
      window.removeEventListener('offline', handleBrowserOffline);
      unsubNetwork();
      unsubSocket();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
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
        socketService.requestQueueSnapshot();
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

  if (isOnline && !justReconnected && !isReconnecting) {
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
            Transfers frozen & dead MTProto sockets purged. Waiting for Wi-Fi reconnection...
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
};
