import React, { useEffect, useState } from 'react';
import { api } from '@/services/api';
import type { V3TurboStatus, V3TurboWorker } from '@/types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Zap,
  Activity,
  Gauge,
  Clock,
  CheckCircle2,
  AlertTriangle,
  RotateCcw,
  Loader2,
  Server,
  Layers,
} from 'lucide-react';

export const MultiTurboCard: React.FC = () => {
  const [turboStatus, setTurboStatus] = useState<V3TurboStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [changingMode, setChangingMode] = useState<string | null>(null);

  const loadStatus = async () => {
    try {
      const res = await api.v3GetTurboStatus();
      setTurboStatus(res);
    } catch {
      // Ignore if not logged in
    }
  };

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 4000);
    return () => clearInterval(interval);
  }, []);

  const handleSetMode = async (mode: 'off' | 'balanced' | 'adaptive' | 'maximum') => {
    setChangingMode(mode);
    try {
      await api.v3SetTurboMode(mode);
      await loadStatus();
    } catch (e: any) {
      alert(e?.message || 'Failed to update Turbo mode.');
    } finally {
      setChangingMode(null);
    }
  };

  const getModeColor = (mode: string) => {
    switch (mode) {
      case 'maximum':
        return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      case 'adaptive':
        return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'balanced':
        return 'bg-blue-500/20 text-blue-400 border-blue-500/40';
      default:
        return 'bg-neutral-800 text-neutral-400 border-neutral-700';
    }
  };

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-xl space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-neutral-800 gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center">
            <Zap className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-semibold text-white">Adaptive Multi-Turbo</h3>
              {turboStatus && (
                <Badge className={`text-[10px] uppercase tracking-wider ${getModeColor(turboStatus.mode)}`}>
                  {turboStatus.mode}
                </Badge>
              )}
            </div>
            <p className="text-xs text-neutral-400">
              Parallel multi-account MTProto downloads with dynamic EWMA latency routing.
            </p>
          </div>
        </div>

        {/* Mode Selector Buttons */}
        <div className="flex items-center bg-neutral-950 p-1 rounded-xl border border-neutral-800 self-start sm:self-auto">
          {(['off', 'balanced', 'adaptive', 'maximum'] as const).map((m) => (
            <button
              key={m}
              onClick={() => handleSetMode(m)}
              disabled={changingMode !== null}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg capitalize transition-all ${
                turboStatus?.mode === m
                  ? 'bg-neutral-800 text-white shadow-sm'
                  : 'text-neutral-400 hover:text-neutral-200'
              }`}
            >
              {changingMode === m ? <Loader2 className="w-3.5 h-3.5 animate-spin inline" /> : m}
            </button>
          ))}
        </div>
      </div>

      {/* Workers Grid */}
      <div className="space-y-3">
        <div className="flex items-center justify-between text-xs text-neutral-400 px-1">
          <span className="flex items-center gap-1.5 font-medium">
            <Server className="w-3.5 h-3.5 text-neutral-500" />
            Active Workers ({turboStatus?.workers.length || 0}/3)
          </span>
          <span className="text-[11px] text-neutral-500">Auto-balanced via EWMA latency</span>
        </div>

        {(!turboStatus || turboStatus.workers.length === 0) && (
          <div className="p-6 border border-neutral-800 rounded-xl bg-neutral-950/40 text-center text-xs text-neutral-500">
            No Turbo workers active. Link Telegram accounts in Account Manager to enable parallel acceleration.
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {turboStatus?.workers.map((worker) => (
            <div
              key={worker.account_id}
              className="p-4 bg-neutral-800/50 border border-neutral-700/60 rounded-xl space-y-3 relative overflow-hidden"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-white font-semibold">
                    ID: {worker.telegram_user_id}
                  </span>
                  <Badge
                    className={
                      worker.role === 'primary'
                        ? 'bg-blue-500/20 text-blue-400 border-blue-500/40 text-[9px]'
                        : 'bg-purple-500/20 text-purple-400 border-purple-500/40 text-[9px]'
                    }
                  >
                    {worker.role}
                  </Badge>
                </div>
                {worker.is_cooling_down ? (
                  <Badge className="bg-red-500/20 text-red-400 border-red-500/40 text-[9px] gap-1 animate-pulse">
                    <AlertTriangle className="w-2.5 h-2.5" />
                    Cooldown {Math.round(worker.cooldown_remaining_s)}s
                  </Badge>
                ) : (
                  <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-500/40 text-[9px] gap-1">
                    <CheckCircle2 className="w-2.5 h-2.5" />
                    Ready
                  </Badge>
                )}
              </div>

              <div className="grid grid-cols-2 gap-2 text-[11px] pt-1 border-t border-neutral-750">
                <div className="bg-neutral-900/80 p-2 rounded-lg">
                  <div className="text-neutral-500 flex items-center gap-1">
                    <Clock className="w-3 h-3" /> Latency
                  </div>
                  <div className="font-mono text-white font-medium mt-0.5">
                    {worker.ewma_latency_ms} ms
                  </div>
                </div>
                <div className="bg-neutral-900/80 p-2 rounded-lg">
                  <div className="text-neutral-500 flex items-center gap-1">
                    <Gauge className="w-3 h-3" /> Speed
                  </div>
                  <div className="font-mono text-emerald-400 font-medium mt-0.5">
                    {worker.ewma_throughput_mbs} MB/s
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between text-[10px] text-neutral-400 pt-1">
                <span>In-flight: {worker.active_in_flight} chunks</span>
                <span>Fetched: {(worker.total_bytes_fetched / (1024 * 1024)).toFixed(1)} MB</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
