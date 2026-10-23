import React, { useEffect, useState } from 'react';
import { useApp } from '@/context/AppContext';
import { Hourglass, ShieldAlert } from 'lucide-react';

export const FloodWaitBanner: React.FC = () => {
  const { floodWait } = useApp();
  const [remainingSeconds, setRemainingSeconds] = useState(0);

  useEffect(() => {
    if (floodWait && floodWait.active && floodWait.seconds > 0) {
      setRemainingSeconds(floodWait.seconds);
      const timer = setInterval(() => {
        setRemainingSeconds((prev) => {
          if (prev <= 1) {
            clearInterval(timer);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
      return () => clearInterval(timer);
    } else {
      setRemainingSeconds(0);
    }
  }, [floodWait]);

  if (!floodWait || !floodWait.active || remainingSeconds <= 0) {
    return null;
  }

  return (
    <div className="relative mx-4 mt-3 mb-1 p-3.5 rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-500 backdrop-blur-md shadow-lg flex items-center justify-between gap-4 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="relative flex items-center justify-center p-2 rounded-lg bg-amber-500/20">
          <Hourglass className="w-5 h-5 animate-pulse text-amber-400" />
          <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-amber-500"></span>
          </span>
        </div>
        <div>
          <div className="text-sm font-semibold text-amber-200 flex items-center gap-2">
            <span>Telegram MTProto Cooldown Active</span>
            <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-amber-500/30 text-amber-300">
              FloodWait
            </span>
          </div>
          <div className="text-xs text-amber-300/80">
            Transfers safely paused to protect your account. Auto-resuming in{' '}
            <span className="font-mono font-bold text-amber-200">{remainingSeconds}s</span>...
          </div>
        </div>
      </div>
      <div className="hidden sm:flex items-center text-xs text-amber-300/70 gap-1.5">
        <ShieldAlert className="w-4 h-4" />
        <span>Safe Mode Active</span>
      </div>
    </div>
  );
};
