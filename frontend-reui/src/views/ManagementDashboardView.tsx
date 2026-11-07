import React, { useState } from 'react';
import { useApp } from '@/context/AppContext';
import { UploaderView } from './UploaderView';
import { SnifferView } from './SnifferView';
import { HistoryView } from './HistoryView';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Clock,
  Columns,
  LayoutDashboard,
  Radio,
  Upload,
  X,
} from 'lucide-react';

interface ManagementDashboardViewProps {
  initialSubTab?: 'all' | 'uploader' | 'sniffer' | 'history';
}

export const ManagementDashboardView: React.FC<ManagementDashboardViewProps> = ({
  initialSubTab = 'all',
}) => {
  const { uploadQueue } = useApp();
  const [activeDeck, setActiveDeck] = useState<'all' | 'uploader' | 'sniffer' | 'history'>(initialSubTab);
  const [isHistoryDockOpen, setHistoryDockOpen] = useState<boolean>(() => {
    return localStorage.getItem('mgmt_history_dock_open') !== 'false';
  });
  const [isSnifferDockOpen, setSnifferDockOpen] = useState<boolean>(() => {
    return localStorage.getItem('mgmt_sniffer_dock_open') !== 'false';
  });

  const toggleHistoryDock = () => {
    setHistoryDockOpen((prev) => {
      const next = !prev;
      localStorage.setItem('mgmt_history_dock_open', String(next));
      return next;
    });
  };

  const toggleSnifferDock = () => {
    setSnifferDockOpen((prev) => {
      const next = !prev;
      localStorage.setItem('mgmt_sniffer_dock_open', String(next));
      return next;
    });
  };

  const activeUploadsCount = uploadQueue.filter(
    (t) => t.status === 'uploading' || t.status === 'streaming' || t.status === 'queued'
  ).length;

  return (
    <div className="w-full h-full p-2 sm:p-2.5 flex flex-col gap-2 min-h-0 overflow-hidden animate-in fade-in duration-300">
      <div className="shrink-0 p-2 sm:p-2.5 rounded-xl border border-border/60 bg-card/75 backdrop-blur-md shadow-xs flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-sky-500/20 to-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0">
            <LayoutDashboard className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-foreground truncate">Management Dashboard</span>
              <Badge variant="outline" className="text-[10px] bg-emerald-500/10 text-emerald-400 border-emerald-500/20 h-4 px-1.5 py-0 shrink-0">
                Operations
              </Badge>
            </div>
            <p className="text-[11px] text-muted-foreground truncate">
              History on left dock • Uploader in center • Sniffer & FDM on right dock
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap shrink-0 ml-auto">
          {activeDeck === 'all' && (
            <div className="flex items-center gap-1.5">
              <Button
                variant={isHistoryDockOpen ? 'secondary' : 'default'}
                size="sm"
                onClick={toggleHistoryDock}
                className="h-7 px-2.5 text-xs gap-1.5 cursor-pointer"
              >
                <Clock className="w-3.5 h-3.5 text-amber-400" />
                <span>History {isHistoryDockOpen ? '(Docked)' : ''}</span>
              </Button>
              <Button
                variant={isSnifferDockOpen ? 'secondary' : 'default'}
                size="sm"
                onClick={toggleSnifferDock}
                className="h-7 px-2.5 text-xs gap-1.5 cursor-pointer"
              >
                <Radio className="w-3.5 h-3.5 text-emerald-400" />
                <span>Sniffer {isSnifferDockOpen ? '(Docked)' : ''}</span>
              </Button>
            </div>
          )}

          <div className="flex items-center gap-1 p-0.5 rounded-lg bg-muted/40 border border-border/60">
            <Button
              size="sm"
              variant={activeDeck === 'all' ? 'default' : 'ghost'}
              onClick={() => setActiveDeck('all')}
              className={`h-6.5 px-2.5 text-xs gap-1 cursor-pointer ${
                activeDeck === 'all'
                  ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Columns className="w-3 h-3" />
              <span>Docks</span>
            </Button>

            <Button
              size="sm"
              variant={activeDeck === 'uploader' ? 'default' : 'ghost'}
              onClick={() => setActiveDeck('uploader')}
              className={`h-6.5 px-2.5 text-xs gap-1 cursor-pointer ${
                activeDeck === 'uploader'
                  ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Upload className="w-3 h-3" />
              <span>Uploader</span>
              {activeUploadsCount > 0 && (
                <span className="w-3.5 h-3.5 rounded-full bg-primary-foreground/20 text-[8px] font-bold flex items-center justify-center">
                  {activeUploadsCount}
                </span>
              )}
            </Button>

            <Button
              size="sm"
              variant={activeDeck === 'sniffer' ? 'default' : 'ghost'}
              onClick={() => setActiveDeck('sniffer')}
              className={`h-6.5 px-2.5 text-xs gap-1 cursor-pointer ${
                activeDeck === 'sniffer'
                  ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Radio className="w-3 h-3" />
              <span>Sniffer</span>
            </Button>

            <Button
              size="sm"
              variant={activeDeck === 'history' ? 'default' : 'ghost'}
              onClick={() => setActiveDeck('history')}
              className={`h-6.5 px-2.5 text-xs gap-1 cursor-pointer ${
                activeDeck === 'history'
                  ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Clock className="w-3 h-3" />
              <span>History</span>
            </Button>
          </div>
        </div>
      </div>

      {activeDeck === 'all' && (
        <div className="flex-1 min-h-0 flex flex-col lg:flex-row items-stretch gap-2.5 w-full overflow-hidden">
          {isHistoryDockOpen && (
            <div className="w-full lg:w-72 xl:w-80 2xl:w-92 shrink-0 h-full flex flex-col rounded-xl border border-border/70 bg-card/65 backdrop-blur-md overflow-hidden shadow-xs animate-in slide-in-from-left duration-200">
              <div className="px-3 py-1.5 border-b border-border/40 flex items-center justify-between shrink-0 bg-muted/20">
                <div className="flex items-center gap-1.5 min-w-0">
                  <Clock className="w-3.5 h-3.5 text-amber-400" />
                  <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground truncate">
                    History Dock
                  </span>
                </div>
                <button
                  onClick={toggleHistoryDock}
                  className="text-muted-foreground hover:text-foreground p-1 rounded-md transition-colors cursor-pointer"
                  title="Close History Dock"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin p-1">
                <HistoryView isDocked />
              </div>
            </div>
          )}

          <div className="flex-1 min-w-0 h-full flex flex-col rounded-xl border border-border/70 bg-card/65 backdrop-blur-md overflow-hidden shadow-xs">
            <div className="px-3 py-1.5 border-b border-border/40 flex items-center justify-between shrink-0 bg-muted/20">
              <div className="flex items-center gap-1.5 min-w-0">
                <Upload className="w-3.5 h-3.5 text-sky-400" />
                <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground truncate">
                  Turbo Uploader
                </span>
                {activeUploadsCount > 0 && (
                  <Badge variant="outline" className="text-[10px] bg-sky-500/10 text-sky-400 border-sky-500/20 h-4 px-1 py-0">
                    {activeUploadsCount} Active
                  </Badge>
                )}
              </div>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin p-1">
              <UploaderView isDocked />
            </div>
          </div>

          {isSnifferDockOpen && (
            <div className="w-full lg:w-80 xl:w-96 2xl:w-[420px] shrink-0 h-full flex flex-col rounded-xl border border-border/70 bg-card/65 backdrop-blur-md overflow-hidden shadow-xs animate-in slide-in-from-right duration-200">
              <div className="px-3 py-1.5 border-b border-border/40 flex items-center justify-between shrink-0 bg-muted/20">
                <div className="flex items-center gap-1.5 min-w-0">
                  <Radio className="w-3.5 h-3.5 text-emerald-400" />
                  <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground truncate">
                    Sniffer & FDM Dock
                  </span>
                  <Badge variant="outline" className="text-[10px] bg-emerald-500/10 text-emerald-400 border-emerald-500/20 h-4 px-1 py-0">
                    Live
                  </Badge>
                </div>
                <button
                  onClick={toggleSnifferDock}
                  className="text-muted-foreground hover:text-foreground p-1 rounded-md transition-colors cursor-pointer"
                  title="Close Sniffer Dock"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin p-1">
                <SnifferView isDocked />
              </div>
            </div>
          )}
        </div>
      )}

      {activeDeck === 'uploader' && (
        <div className="flex-1 min-h-0 overflow-y-auto rounded-xl border border-border/70 bg-card/65 backdrop-blur-md p-2 shadow-xs">
          <UploaderView />
        </div>
      )}

      {activeDeck === 'sniffer' && (
        <div className="flex-1 min-h-0 overflow-y-auto rounded-xl border border-border/70 bg-card/65 backdrop-blur-md p-2 shadow-xs">
          <SnifferView />
        </div>
      )}

      {activeDeck === 'history' && (
        <div className="flex-1 min-h-0 overflow-y-auto rounded-xl border border-border/70 bg-card/65 backdrop-blur-md p-2 shadow-xs">
          <HistoryView />
        </div>
      )}
    </div>
  );
};
