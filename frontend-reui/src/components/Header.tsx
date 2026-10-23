import React, { useEffect, useState, useRef, useLayoutEffect } from 'react';
import { useApp } from '@/context/AppContext';
import type { TabType } from '@/types';
import { formatBytes, formatSpeed } from '@/lib/formatters';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Activity,
  ArrowDownCircle,
  ArrowUpCircle,
  ChevronDown,
  Clapperboard,
  Clock,
  Cpu,
  Download,
  HardDrive,
  Moon,
  Radio,
  Send,
  Server,
  Settings,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Sun,
  Upload,
  Wifi,
} from 'lucide-react';

export const Header: React.FC = () => {
  const {
    activeTab,
    setActiveTab,
    authStatus,
    setAuthModalOpen,
    telemetry,
    isSocketConnected,
    theme,
    toggleTheme,
    uploadQueue,
    isMobileDockOpen,
    toggleMobileDock,
  } = useApp();

  const [installPrompt, setInstallPrompt] = useState<any>(null);

  useEffect(() => {
    const handleBeforeInstall = (e: any) => {
      e.preventDefault();
      setInstallPrompt(e);
    };
    window.addEventListener('beforeinstallprompt', handleBeforeInstall);
    return () => window.removeEventListener('beforeinstallprompt', handleBeforeInstall);
  }, []);

  const handleInstallApp = async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    const result = await installPrompt.userChoice;
    if (result.outcome === 'accepted') {
      setInstallPrompt(null);
    }
  };

  const activeUploadsCount = uploadQueue.filter(
    (t) => t.status === 'uploading' || t.status === 'streaming' || t.status === 'queued'
  ).length;

  const tabs: { id: TabType; label: string; icon: React.ReactNode; shortcut: string; badge?: React.ReactNode }[] = [
    {
      id: 'uploader',
      label: 'Uploader',
      icon: <Upload className="w-4 h-4" />,
      shortcut: '1',
      badge:
        activeUploadsCount > 0 ? (
          <span className="w-5 h-5 rounded-full bg-primary text-primary-foreground text-[10px] font-bold flex items-center justify-center animate-in zoom-in">
            {activeUploadsCount}
          </span>
        ) : null,
    },
    {
      id: 'sniffer',
      label: 'Sniffer & FDM',
      icon: <Radio className="w-4 h-4" />,
      shortcut: '2',
      badge: (
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
          <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
        </span>
      ),
    },
    {
      id: 'cinema',
      label: 'Cinema',
      icon: <Clapperboard className="w-4 h-4" />,
      shortcut: '3',
    },
    {
      id: 'history',
      label: 'History',
      icon: <Clock className="w-4 h-4" />,
      shortcut: '4',
    },
    {
      id: 'settings',
      label: 'Settings',
      icon: <Settings className="w-4 h-4" />,
      shortcut: '5',
    },
  ];

  const navRef = useRef<HTMLElement | null>(null);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [indicator, setIndicator] = useState<{ left: number; width: number; ready: boolean }>({
    left: 0,
    width: 0,
    ready: false,
  });

  const updateIndicator = () => {
    const activeEl = tabRefs.current[activeTab];
    if (activeEl && navRef.current) {
      setIndicator({
        left: activeEl.offsetLeft,
        width: activeEl.offsetWidth,
        ready: true,
      });
      activeEl.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' });
    }
  };

  useLayoutEffect(() => {
    updateIndicator();
    const timer = setTimeout(updateIndicator, 50);
    window.addEventListener('resize', updateIndicator);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('resize', updateIndicator);
    };
  }, [activeTab]);

  // Quick Access keyboard listener (1-5, Alt+ArrowLeft/Right)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        !target ||
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable ||
        e.metaKey ||
        e.ctrlKey
      ) {
        return;
      }

      const num = parseInt(e.key, 10);
      if (num >= 1 && num <= tabs.length) {
        e.preventDefault();
        setActiveTab(tabs[num - 1].id);
        return;
      }

      if (e.altKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        const currentIdx = tabs.findIndex((t) => t.id === activeTab);
        const prevIdx = (currentIdx - 1 + tabs.length) % tabs.length;
        setActiveTab(tabs[prevIdx].id);
      } else if (e.altKey && e.key === 'ArrowRight') {
        e.preventDefault();
        const currentIdx = tabs.findIndex((t) => t.id === activeTab);
        const nextIdx = (currentIdx + 1) % tabs.length;
        setActiveTab(tabs[nextIdx].id);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeTab]);

  return (
    <header className="sticky top-0 z-40 w-full border-b border-border/60 bg-card/85 backdrop-blur-xl transition-all">
      <div className="w-full px-4 sm:px-6 lg:px-8 xl:px-10 h-16 flex items-center justify-between gap-4">
        {/* Brand Section */}
        <div className="flex items-center gap-3 shrink-0">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-sky-500 to-emerald-500 flex items-center justify-center shadow-md shadow-sky-500/20 text-white transition-transform hover:scale-105 cursor-pointer">
            <Send className="w-5 h-5 -rotate-12 translate-x-px" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-sm tracking-tight text-foreground">TG Power Suite</span>
              <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 border-sky-500/30 text-sky-400 bg-sky-500/10">
                PRO
              </Badge>
            </div>
            <div className="text-[10px] text-muted-foreground flex items-center gap-1.5">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  isSocketConnected ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'
                }`}
              />
              <span>{isSocketConnected ? 'Live MTProto Core' : 'Reconnecting...'}</span>
            </div>
          </div>
        </div>

        {/* Slidable ReUI Navigation Quick Access */}
        <div className="hidden md:flex items-center max-w-full overflow-hidden">
          <nav
            ref={navRef}
            role="tablist"
            aria-label="Main Navigation"
            className="relative flex items-center gap-1 p-1 rounded-2xl bg-muted/40 border border-border/60 backdrop-blur-md overflow-x-auto scrollbar-none scroll-smooth shadow-inner"
          >
            {/* Smooth Slidable Pill Indicator */}
            <div
              aria-hidden="true"
              className={`absolute top-1 bottom-1 rounded-xl bg-background border border-border/70 shadow-sm shadow-black/10 transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] pointer-events-none ${
                indicator.ready ? 'opacity-100' : 'opacity-0'
              }`}
              style={{
                transform: `translateX(${indicator.left}px)`,
                width: `${indicator.width}px`,
              }}
            />

            {tabs.map((tab, idx) => {
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  ref={(el) => {
                    tabRefs.current[tab.id] = el;
                  }}
                  role="tab"
                  aria-selected={isActive}
                  aria-label={`${tab.label} (Press ${tab.shortcut})`}
                  onClick={() => setActiveTab(tab.id)}
                  className={`relative z-10 flex items-center gap-2 px-3.5 py-1.5 rounded-xl text-xs font-medium transition-colors duration-200 cursor-pointer shrink-0 select-none group focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none ${
                    isActive
                      ? 'text-foreground font-semibold'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                  title={`${tab.label} (Quick Access: Press ${tab.shortcut})`}
                >
                  <span
                    aria-hidden="true"
                    className={`transition-transform duration-200 ${
                      isActive ? 'scale-110 text-primary' : 'group-hover:scale-105'
                    }`}
                  >
                    {tab.icon}
                  </span>
                  <span>{tab.label}</span>
                  {tab.badge}
                  <span aria-hidden="true" className="hidden xl:inline-flex items-center justify-center px-1 py-0.2 text-[9px] font-mono rounded bg-muted/70 text-muted-foreground/80 border border-border/40 ml-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
                    {tab.shortcut}
                  </span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* Right Action Items */}
        <div className="flex items-center gap-2.5">
          {/* PWA Install Button */}
          {installPrompt && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleInstallApp}
              aria-label="Install TG Power Suite as local app"
              className="hidden sm:flex h-8 px-2.5 text-xs gap-1.5 border-primary/40 text-primary hover:bg-primary/10 cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Install App</span>
            </Button>
          )}

          {/* Detailed Live Telemetry Dropdown */}
          <Popover>
            <PopoverTrigger asChild>
              <button
                aria-label="View live hardware and network telemetry details"
                className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-border/60 bg-muted/20 hover:bg-muted/40 text-[11px] text-muted-foreground font-mono transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
                title="Click for detailed system performance"
              >
                <div className="flex items-center gap-1">
                  <Cpu className="w-3.5 h-3.5 text-sky-400" />
                  <span>{telemetry?.cpu_percent ? `${Math.round(telemetry.cpu_percent)}%` : '--'}</span>
                </div>
                <div className="hidden sm:flex items-center gap-1">
                  <Activity className="w-3.5 h-3.5 text-emerald-400" />
                  <span>{telemetry?.ram_percent ? `${Math.round(telemetry.ram_percent)}%` : '--'}</span>
                </div>
                {telemetry?.upload_speed_bytes !== undefined && telemetry.upload_speed_bytes > 0 && (
                  <div className="hidden lg:flex items-center gap-1 text-sky-400 font-semibold">
                    <Upload className="w-3 h-3" />
                    <span>{formatSpeed(telemetry.upload_speed_bytes)}</span>
                  </div>
                )}
                <ChevronDown className="w-3 h-3 opacity-60 ml-0.5" />
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-72 p-3.5 space-y-3 bg-card/95 backdrop-blur-xl border-border shadow-xl">
              <div className="flex items-center justify-between pb-1 border-b border-border/40">
                <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                  <Server className="w-3.5 h-3.5 text-primary" />
                  Hardware & Network
                </span>
                <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 bg-emerald-500/10 text-emerald-400 border-emerald-500/20">
                  Healthy
                </Badge>
              </div>

              {/* CPU Metric */}
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-muted-foreground">CPU Processor</span>
                  <span className="font-semibold text-foreground">{Math.round(telemetry?.cpu_percent || 0)}%</span>
                </div>
                <Progress value={telemetry?.cpu_percent || 0} className="h-1.5 bg-muted/60" />
              </div>

              {/* RAM Metric */}
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-muted-foreground">RAM Memory</span>
                  <span className="font-semibold text-foreground">{Math.round(telemetry?.ram_percent || 0)}%</span>
                </div>
                <Progress value={telemetry?.ram_percent || 0} className="h-1.5 bg-muted/60" />
              </div>

              {/* Disk Metric */}
              {telemetry?.disk_free_gb && (
                <div className="flex items-center justify-between text-xs font-mono pt-1 text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <HardDrive className="w-3.5 h-3.5" />
                    Disk Free
                  </span>
                  <span className="font-semibold text-foreground">{telemetry.disk_free_gb.toFixed(1)} GB</span>
                </div>
              )}

              {/* Bandwidth Speeds */}
              <div className="grid grid-cols-2 gap-2 pt-1 border-t border-border/40 font-mono text-[11px]">
                <div className="flex items-center gap-1.5 text-sky-400">
                  <ArrowUpCircle className="w-3.5 h-3.5 shrink-0" />
                  <div className="truncate">
                    <div className="text-[9px] text-muted-foreground">UPLOAD</div>
                    <div>{formatSpeed(telemetry?.upload_speed_bytes)}</div>
                  </div>
                </div>
                <div className="flex items-center gap-1.5 text-emerald-400">
                  <ArrowDownCircle className="w-3.5 h-3.5 shrink-0" />
                  <div className="truncate">
                    <div className="text-[9px] text-muted-foreground">DOWNLOAD</div>
                    <div>{formatSpeed(telemetry?.download_speed_bytes)}</div>
                  </div>
                </div>
              </div>
            </PopoverContent>
          </Popover>

          {/* Mobile Telegram Dock Toggle Button */}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleMobileDock}
            aria-label={isMobileDockOpen ? 'Hide Mobile Telegram Dock (Ctrl+B)' : 'Show Mobile Telegram Dock (Ctrl+B)'}
            aria-expanded={isMobileDockOpen}
            className={`w-9 h-9 rounded-lg border transition-all cursor-pointer ${
              isMobileDockOpen
                ? 'border-sky-500/40 bg-sky-500/15 text-sky-400 shadow-sm shadow-sky-500/10'
                : 'border-border/50 text-muted-foreground hover:text-foreground hover:bg-accent'
            }`}
            title={`Toggle Mobile Telegram Dock (Ctrl+B) — ${isMobileDockOpen ? 'Visible' : 'Hidden'}`}
          >
            <Smartphone className="w-4 h-4" />
          </Button>

          {/* Theme Switcher Button */}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            aria-label={`Current theme: ${
              theme === 'cyber-emerald'
                ? 'Midnight Emerald'
                : theme === 'dark'
                ? 'Onyx Dark'
                : 'Daylight'
            }. Click to switch theme`}
            className={`w-9 h-9 rounded-lg border transition-all cursor-pointer ${
              theme === 'cyber-emerald'
                ? 'border-emerald-500/40 bg-emerald-500/10 hover:bg-emerald-500/20 shadow-sm shadow-emerald-500/10'
                : 'border-border/50 hover:bg-accent'
            }`}
            title={`Current: ${
              theme === 'cyber-emerald'
                ? 'Midnight Emerald (Cyber Core)'
                : theme === 'dark'
                ? 'Onyx Dark'
                : 'Daylight'
            } — Click to switch`}
          >
            {theme === 'cyber-emerald' ? (
              <Sparkles className="w-4 h-4 text-emerald-400 animate-pulse" />
            ) : theme === 'dark' ? (
              <Moon className="w-4 h-4 text-sky-400" />
            ) : (
              <Sun className="w-4 h-4 text-amber-500" />
            )}
          </Button>

          {/* Telegram MTProto Auth Status Button */}
          <button
            onClick={() => setAuthModalOpen(true)}
            aria-label={
              authStatus?.authenticated
                ? `Telegram account connected as ${authStatus.first_name || authStatus.username || 'user'}. Click to manage session`
                : 'Connect Telegram account via QR or phone'
            }
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none ${
              authStatus?.authenticated
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20'
                : 'bg-primary text-primary-foreground hover:opacity-90 shadow-sm'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate max-w-[130px]">
              {authStatus?.authenticated
                ? authStatus.first_name || authStatus.username || 'Authorized'
                : 'Connect Telegram'}
            </span>
          </button>
        </div>
      </div>

      {/* Mobile Nav Bar */}
      <nav
        role="tablist"
        aria-label="Mobile Navigation"
        className="md:hidden flex items-center justify-around border-t border-border/40 px-2 py-1.5 bg-card/85 backdrop-blur-md"
      >
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={isActive}
              aria-label={`${tab.label} tab`}
              onClick={() => setActiveTab(tab.id)}
              className={`relative flex flex-col items-center gap-0.5 px-3 py-1 rounded-xl text-[10px] font-medium transition-all duration-200 cursor-pointer ${
                isActive
                  ? 'bg-background text-foreground font-bold shadow-xs border border-border/70'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <div className="relative">
                <span className={isActive ? 'text-primary' : ''}>{tab.icon}</span>
                {tab.badge && <span className="absolute -top-1 -right-1">{tab.badge}</span>}
              </div>
              <span>{tab.label.split(' ')[0]}</span>
            </button>
          );
        })}
      </nav>
    </header>
  );
};
