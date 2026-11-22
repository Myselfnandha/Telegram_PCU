import React, { useEffect, useState } from 'react';
import { api } from '@/services/api';
import type { V3DeviceInfo } from '@/types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Laptop,
  Smartphone,
  ShieldCheck,
  ShieldAlert,
  Trash2,
  CheckCircle,
  Loader2,
  KeyRound,
} from 'lucide-react';

export const TrustedDevicesCard: React.FC = () => {
  const [devices, setDevices] = useState<V3DeviceInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [challengeCode, setChallengeCode] = useState<{ [deviceId: string]: string }>({});

  const loadDevices = async () => {
    setLoading(true);
    try {
      const res = await api.v3GetDevices();
      setDevices(res);
      try {
        const pending = await api.v3GetPendingDevices();
        const codeMap: { [id: string]: string } = {};
        for (const p of pending) {
          if (p.challenge_code) {
            codeMap[p.id] = p.challenge_code;
          }
        }
        setChallengeCode((prev) => ({ ...codeMap, ...prev }));
      } catch {}
    } catch {} finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDevices();
  }, []);

  const handleApprove = async (deviceId: string) => {
    setActionLoading(deviceId);
    try {
      const code = challengeCode[deviceId];
      await api.v3ApproveDevice(deviceId, code);
      await loadDevices();
    } catch (e: any) {
      alert(e?.message || 'Failed to approve device.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleRevoke = async (deviceId: string) => {
    if (!confirm('Are you sure you want to revoke this device? All sessions on this device will be immediately terminated.')) {
      return;
    }
    setActionLoading(deviceId);
    try {
      await api.v3RevokeDevice(deviceId);
      await loadDevices();
    } catch (e: any) {
      alert(e?.message || 'Failed to revoke device.');
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-xl">
      <div className="flex items-center justify-between pb-4 border-b border-neutral-800">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-purple-500/10 text-purple-400 flex items-center justify-center">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-semibold text-white">Trusted Devices</h3>
            <p className="text-xs text-neutral-400">
              Approved phones, browsers, and PCs with persistent access to your workspace.
            </p>
          </div>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {loading && (
          <div className="flex items-center justify-center py-8 text-neutral-500 text-xs">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            Loading devices...
          </div>
        )}

        {!loading && devices.length === 0 && (
          <div className="text-center py-8 text-neutral-500 text-xs">
            No devices registered.
          </div>
        )}

        {!loading &&
          devices.map((dev) => (
            <div
              key={dev.id}
              className="flex flex-col sm:flex-row sm:items-center justify-between p-3.5 bg-neutral-800/60 border border-neutral-700/60 rounded-xl gap-3"
            >
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-neutral-700/80 flex items-center justify-center text-neutral-300">
                  {dev.display_name.toLowerCase().includes('mobile') || dev.display_name.toLowerCase().includes('phone') ? (
                    <Smartphone className="w-4 h-4" />
                  ) : (
                    <Laptop className="w-4 h-4" />
                  )}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm text-white">{dev.display_name}</span>
                    {dev.is_current && (
                      <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-500/40 text-[10px]">
                        This Device
                      </Badge>
                    )}
                    {dev.verified ? (
                      <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/40 text-[10px] gap-1">
                        <CheckCircle className="w-2.5 h-2.5" />
                        Verified
                      </Badge>
                    ) : (
                      <Badge className="bg-amber-500/20 text-amber-400 border-amber-500/40 text-[10px] gap-1">
                        <ShieldAlert className="w-2.5 h-2.5" />
                        Pending Approval
                      </Badge>
                    )}
                  </div>
                  <p className="text-[11px] text-neutral-500 mt-0.5">
                    Added: {new Date(dev.created_at).toLocaleDateString()}
                    {dev.last_seen_at && ` • Last seen: ${new Date(dev.last_seen_at).toLocaleTimeString()}`}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 self-end sm:self-auto">
                {!dev.verified && (
                  <div className="flex items-center gap-1.5">
                    <input
                      type="text"
                      placeholder="6-digit code"
                      maxLength={6}
                      value={challengeCode[dev.id] || ''}
                      onChange={(e) =>
                        setChallengeCode((prev) => ({ ...prev, [dev.id]: e.target.value }))
                      }
                      className="w-24 bg-neutral-800 border border-neutral-700 rounded-lg px-2 py-1 text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-purple-500 text-center"
                    />
                    <Button
                      size="sm"
                      onClick={() => handleApprove(dev.id)}
                      disabled={actionLoading === dev.id}
                      className="text-xs"
                    >
                      Approve
                    </Button>
                  </div>
                )}

                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleRevoke(dev.id)}
                  disabled={actionLoading === dev.id}
                  className="text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 p-2"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          ))}
      </div>
    </div>
  );
};
