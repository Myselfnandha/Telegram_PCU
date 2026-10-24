import React, { useEffect, useState } from 'react';
import { useApp } from '@/context/AppContext';
import { api } from '@/services/api';
import { formatBytes } from '@/lib/formatters';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import {
  Activity,
  CheckCircle2,
  Clock,
  FolderDown,
  HardDrive,
  Languages,
  Laptop,
  LogOut,
  Moon,
  Network,
  Radio,
  RefreshCw,
  Server,
  Settings,
  ShieldCheck,
  Sparkles,
  Subtitles,
  Sun,
  Trash2,
  Tv,
  Wifi,
} from 'lucide-react';

export const SettingsView: React.FC = () => {
  const { authStatus, setAuthModalOpen, refreshAuth, telemetry, theme, setTheme, toggleTheme, addToast } = useApp();
  const [preferredPlayer, setPreferredPlayer] = useState<'vlc' | 'web'>(() => {
    const val = localStorage.getItem('tg_player_pref');
    return val === 'web' ? 'web' : 'vlc';
  });
  const [bufferSizeMb, setBufferSizeMb] = useState<number>(() => {
    return Number(localStorage.getItem('tg_buffer_mb') || 4);
  });
  const [networkCachingMs, setNetworkCachingMs] = useState<number>(() => {
    return Number(localStorage.getItem('tg_network_caching') || 1000);
  });
  const [subLanguage, setSubLanguage] = useState<string>(() => {
    return localStorage.getItem('tg_sub_lang') || 'eng,tam,hin,all';
  });
  const [audioLanguage, setAudioLanguage] = useState<string>(() => {
    return localStorage.getItem('tg_audio_lang') || 'tam,eng,hin,tel,mal,kan,all';
  });
  const [cacheStatus, setCacheStatus] = useState<{ total_mb?: number; size_mb?: number; file_count?: number } | null>(null);
  const [networkInfo, setNetworkInfo] = useState<{ local_ip?: string; port?: number; latency_ms?: number } | null>(null);
  const [offlineSaver, setOfflineSaver] = useState<boolean>(false);
  const [clearingCache, setClearingCache] = useState(false);
  const [pinging, setPinging] = useState(false);
  const [pingResult, setPingResult] = useState<number | null>(null);

  const loadCacheStatus = async () => {
    try {
      const res = await api.getMediaCacheStatus();
      setCacheStatus(res);
    } catch {}
  };

  const loadNetworkInfo = async () => {
    try {
      const res = await api.getSystemNetwork();
      setNetworkInfo(res);
    } catch {}
  };

  const loadOfflineSaver = async () => {
    try {
      const res = await api.getOfflineSaverStatus();
      setOfflineSaver(Boolean(res?.enabled));
    } catch {}
  };

  useEffect(() => {
    loadCacheStatus();
    loadNetworkInfo();
    loadOfflineSaver();
  }, []);

  const handleToggleOfflineSaver = async (enabled: boolean) => {
    setOfflineSaver(enabled);
    try {
      await api.toggleOfflineSaver(enabled);
      addToast({
        type: 'success',
        title: 'Offline Cinema Organizer',
        message: enabled
          ? 'Completed streams will automatically save to ~/Videos/Telegram_Cinema'
          : 'Offline stream saving disabled.',
      });
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    }
  };

  const handlePlayerChange = (player: 'vlc' | 'web') => {
    setPreferredPlayer(player);
    localStorage.setItem('tg_player_pref', player);
    addToast({
      type: 'info',
      title: 'Player Saved',
      message: `Default media player set to ${player === 'web' ? 'Browser Preview' : 'VLC'}.`,
    });
  };

  const handleBufferChange = (val: number[]) => {
    const size = val[0] || 4;
    setBufferSizeMb(size);
    localStorage.setItem('tg_buffer_mb', String(size));
  };

  const handleNetworkCachingChange = (val: number[]) => {
    const ms = val[0] || 300;
    setNetworkCachingMs(ms);
    localStorage.setItem('tg_network_caching', String(ms));
  };

  const handleSubLanguageChange = (lang: string) => {
    setSubLanguage(lang);
    localStorage.setItem('tg_sub_lang', lang);
    addToast({ type: 'info', title: 'Preferences', message: 'Subtitle language preferences saved.' });
  };

  const handleAudioLanguageChange = (lang: string) => {
    setAudioLanguage(lang);
    localStorage.setItem('tg_audio_lang', lang);
    addToast({ type: 'info', title: 'Preferences', message: 'Audio language preferences saved.' });
  };

  const handleClearCache = async () => {
    setClearingCache(true);
    try {
      await api.clearMediaCache();
      addToast({
        type: 'success',
        title: 'Cache Cleared',
        message: 'All temporary video preview chunks and metadata cleared.',
      });
      loadCacheStatus();
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    } finally {
      setClearingCache(false);
    }
  };

  const handleTestPing = async () => {
    setPinging(true);
    const start = performance.now();
    try {
      await fetch('/api/auth/status', { method: 'GET' });
      const elapsed = Math.round(performance.now() - start);
      setPingResult(elapsed);
      addToast({
        type: 'success',
        title: 'Latency Test',
        message: `Round-trip response: ${elapsed}ms`,
      });
    } catch {
      setPingResult(null);
    } finally {
      setPinging(false);
    }
  };

  return (
    <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 xl:px-10 py-6 space-y-6 animate-in fade-in duration-300">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0 shadow-inner">
          <Settings className="w-5 h-5" />
        </div>
        <div>
          <h1 className="text-base font-semibold text-foreground">Application Preferences & Hardware</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Manage your MTProto Telegram session, playback buffers, language tracks, and local storage.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Theme & Display Mode Card */}
        <div className="p-5 rounded-2xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-emerald-400" />
              <h2 className="text-sm font-semibold text-foreground">Theme & Interface Style</h2>
            </div>
            <Badge
              variant="outline"
              className={
                theme === 'cyber-emerald'
                  ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10 text-[10px]'
                  : 'text-[10px]'
              }
            >
              {theme === 'cyber-emerald' ? 'Cyber Core' : theme === 'dark' ? 'Onyx Dark' : 'Daylight'}
            </Badge>
          </div>

          <div className="grid grid-cols-3 gap-2.5 pt-1">
            <button
              onClick={() => setTheme('cyber-emerald')}
              className={`p-3 rounded-xl border text-left flex flex-col justify-between gap-2 transition-all cursor-pointer ${
                theme === 'cyber-emerald'
                  ? 'border-emerald-500/50 bg-emerald-500/10 ring-1 ring-emerald-500/40 shadow-sm'
                  : 'border-border/70 bg-card/40 hover:bg-card/70'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="w-3.5 h-3.5 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/50" />
                <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
              </div>
              <div>
                <div className="text-xs font-semibold text-foreground">Midnight Emerald</div>
                <div className="text-[10px] text-muted-foreground">Cyber Core Glow</div>
              </div>
            </button>

            <button
              onClick={() => setTheme('dark')}
              className={`p-3 rounded-xl border text-left flex flex-col justify-between gap-2 transition-all cursor-pointer ${
                theme === 'dark'
                  ? 'border-sky-500/50 bg-sky-500/10 ring-1 ring-sky-500/40 shadow-sm'
                  : 'border-border/70 bg-card/40 hover:bg-card/70'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="w-3.5 h-3.5 rounded-full bg-slate-400" />
                <Moon className="w-3.5 h-3.5 text-sky-400" />
              </div>
              <div>
                <div className="text-xs font-semibold text-foreground">Onyx Dark</div>
                <div className="text-[10px] text-muted-foreground">Monochrome Slate</div>
              </div>
            </button>

            <button
              onClick={() => setTheme('light')}
              className={`p-3 rounded-xl border text-left flex flex-col justify-between gap-2 transition-all cursor-pointer ${
                theme === 'light'
                  ? 'border-amber-500/50 bg-amber-500/10 ring-1 ring-amber-500/40 shadow-sm'
                  : 'border-border/70 bg-card/40 hover:bg-card/70'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="w-3.5 h-3.5 rounded-full bg-amber-400" />
                <Sun className="w-3.5 h-3.5 text-amber-500" />
              </div>
              <div>
                <div className="text-xs font-semibold text-foreground">Daylight</div>
                <div className="text-[10px] text-muted-foreground">Clean Crisp Light</div>
              </div>
            </button>
          </div>
        </div>

        {/* Telegram Account Card */}
        <div className="p-5 rounded-2xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">Telegram MTProto Session</h2>
            </div>
            <Badge
              variant="outline"
              className={
                authStatus?.authenticated
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px]'
                  : 'bg-muted text-muted-foreground text-[10px]'
              }
            >
              {authStatus?.authenticated ? 'Authorized' : 'Disconnected'}
            </Badge>
          </div>

          {authStatus?.authenticated ? (
            <div className="space-y-3 pt-1">
              <div className="p-3.5 rounded-xl bg-accent/40 border border-border flex items-center gap-3">
                <div className="w-11 h-11 rounded-full bg-primary/20 flex items-center justify-center font-bold text-primary text-base shrink-0">
                  {authStatus.first_name ? authStatus.first_name.charAt(0).toUpperCase() : 'U'}
                </div>
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-foreground truncate">
                    {authStatus.first_name || 'Telegram User'}
                  </div>
                  {authStatus.username && (
                    <div className="text-[11px] text-muted-foreground">@{authStatus.username}</div>
                  )}
                  {authStatus.phone && (
                    <div className="text-[11px] text-muted-foreground font-mono">{authStatus.phone}</div>
                  )}
                </div>
              </div>

              <Button
                variant="outline"
                size="sm"
                onClick={() => setAuthModalOpen(true)}
                className="w-full text-xs cursor-pointer"
              >
                Manage Telegram Session
              </Button>
            </div>
          ) : (
            <div className="space-y-3 pt-1">
              <p className="text-xs text-muted-foreground">
                No active Telegram MTProto session. Sign in to access your saved files and channels.
              </p>
              <Button
                variant="default"
                size="sm"
                onClick={() => setAuthModalOpen(true)}
                className="w-full text-xs cursor-pointer"
              >
                Connect Telegram MTProto
              </Button>
            </div>
          )}
        </div>

        {/* Media Player Engine & Buffering */}
        <div className="p-5 rounded-2xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-4">
          <div className="flex items-center gap-2">
            <Tv className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-semibold text-foreground">Media Player Engine</h2>
          </div>

          <div className="space-y-3.5 pt-1">
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">Default Playback Launcher</label>
              <div className="grid grid-cols-2 gap-2">
                {(
                  [
                    { id: 'vlc', label: 'VLC Media' },
                    { id: 'web', label: 'Browser Preview' },
                  ] as const
                ).map((p) => (
                  <button
                    key={p.id}
                    onClick={() => handlePlayerChange(p.id)}
                    className={`p-2 rounded-xl border text-xs font-medium text-center transition-all cursor-pointer ${
                      preferredPlayer === p.id
                        ? 'border-primary bg-primary/10 text-primary font-semibold shadow-sm'
                        : 'border-border bg-accent/20 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Fast-Seek Buffer Slider */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Fast-Seek Pre-fetch Buffer</span>
                <span className="font-mono font-semibold text-foreground">{bufferSizeMb} MB</span>
              </div>
              <Slider
                value={[bufferSizeMb]}
                min={1}
                max={16}
                step={1}
                onValueChange={handleBufferChange}
                className="cursor-pointer"
              />
            </div>

            {/* VLC Network Caching Slider */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">VLC Network Caching</span>
                <span className="font-mono font-semibold text-foreground">{networkCachingMs} ms</span>
              </div>
              <Slider
                value={[networkCachingMs]}
                min={100}
                max={2000}
                step={50}
                onValueChange={handleNetworkCachingChange}
                className="cursor-pointer"
              />
            </div>
          </div>
        </div>

        {/* Audio & Subtitle Language Preferences */}
        <div className="p-5 rounded-2xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-4">
          <div className="flex items-center gap-2">
            <Languages className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-semibold text-foreground">Audio & Subtitle Tracks</h2>
          </div>

          <div className="space-y-3.5 pt-1">
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">Preferred Subtitle Language</label>
              <select
                value={subLanguage}
                onChange={(e) => handleSubLanguageChange(e.target.value)}
                className="w-full h-9 rounded-lg border border-border bg-background px-3 text-xs focus:outline-none cursor-pointer font-medium"
              >
                <option value="eng,tam,hin,all">English + Regional (Default)</option>
                <option value="eng">English Only</option>
                <option value="tam">Tamil</option>
                <option value="hin">Hindi</option>
                <option value="all">Auto All Tracks</option>
                <option value="none">Disabled</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">Preferred Audio Language</label>
              <select
                value={audioLanguage}
                onChange={(e) => handleAudioLanguageChange(e.target.value)}
                className="w-full h-9 rounded-lg border border-border bg-background px-3 text-xs focus:outline-none cursor-pointer font-medium"
              >
                <option value="tam,eng,hin,tel,mal,kan,all">Regional + English (Default)</option>
                <option value="eng">English</option>
                <option value="tam">Tamil</option>
                <option value="hin">Hindi</option>
                <option value="tel">Telugu</option>
                <option value="mal">Malayalam</option>
                <option value="all">Original Track</option>
              </select>
            </div>
          </div>
        </div>

        {/* Storage, Cache & Network Diagnostics */}
        <div className="p-5 rounded-2xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <HardDrive className="w-4 h-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">Storage & Diagnostics</h2>
            </div>
            {cacheStatus && (
              <Badge variant="secondary" className="text-[10px] font-mono">
                {((cacheStatus as any).total_mb ?? (cacheStatus as any).size_mb ?? 0).toFixed(1)} MB
              </Badge>
            )}
          </div>

          <div className="space-y-2 text-xs font-mono">
            <div className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
              <span className="text-muted-foreground">Server Endpoint:</span>
              <span className="text-foreground">{networkInfo?.local_ip || '127.0.0.1'}:{networkInfo?.port || 8088}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
              <span className="text-muted-foreground">Round-Trip Latency:</span>
              <span className="text-emerald-400 font-semibold">
                {pingResult !== null ? `${pingResult} ms` : pinging ? 'Measuring...' : 'Ready'}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTestPing}
              disabled={pinging}
              className="h-8 text-xs gap-1.5 cursor-pointer"
            >
              <Wifi className={`w-3.5 h-3.5 ${pinging ? 'animate-pulse' : ''}`} />
              <span>{pinging ? 'Testing...' : 'Test Latency'}</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleClearCache}
              disabled={clearingCache}
              className="h-8 text-xs gap-1.5 cursor-pointer"
            >
              <Trash2 className="w-3.5 h-3.5 text-destructive" />
              <span>{clearingCache ? 'Clearing...' : 'Flush Cache'}</span>
            </Button>
          </div>
        </div>

        {/* Offline Cinema Library Organizer Card */}
        <div className="p-5 rounded-2xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FolderDown className="w-4 h-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">Offline Cinema Organizer</h2>
            </div>
            <Switch
              checked={offlineSaver}
              onCheckedChange={handleToggleOfflineSaver}
              className="cursor-pointer"
            />
          </div>

          <div className="space-y-2 text-xs">
            <p className="text-muted-foreground leading-relaxed">
              Automatically saves fully streamed videos into your organized offline library at{' '}
              <span className="font-mono text-foreground font-medium">~/Videos/Telegram_Cinema/</span>.
            </p>
            <div className="p-2.5 rounded-lg bg-accent/30 space-y-1 font-mono text-[11px] text-muted-foreground">
              <div className="text-foreground font-semibold">Automatic Structure:</div>
              <div>• Movies: <span className="text-emerald-400">Movies/Title (Year)/Title.mp4</span></div>
              <div>• TV Series: <span className="text-emerald-400">TV_Shows/Title/Season XX/Episode.mp4</span></div>
              <div>• Subtitles: <span className="text-emerald-400">Auto-synced .srt tracks side-by-side</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
