import React, { useEffect, useState } from 'react';
import { api } from '@/services/api';
import type { V3TelegramAccount } from '@/types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { TelegramLinkModal } from '@/components/v3/TelegramLinkModal';
import {
  Users,
  Plus,
  Trash2,
  Star,
  ShieldAlert,
  CheckCircle2,
  Loader2,
  Zap,
} from 'lucide-react';

export const AccountManagerCard: React.FC = () => {
  const [accounts, setAccounts] = useState<V3TelegramAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [linkModalOpen, setLinkModalOpen] = useState(false);
  const [linkRole, setLinkRole] = useState<'primary' | 'turbo'>('primary');
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const loadAccounts = async () => {
    setLoading(true);
    try {
      const res = await api.v3GetAccounts();
      setAccounts(res);
    } catch {} finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAccounts();
  }, []);

  const handleUnlink = async (accountId: string) => {
    if (!confirm('Are you sure you want to unlink this Telegram account? Its encrypted session will be deleted.')) {
      return;
    }
    setActionLoading(accountId);
    try {
      await api.v3UnlinkAccount(accountId);
      await loadAccounts();
    } catch (e: any) {
      alert(e?.message || 'Failed to unlink account.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleSetPrimary = async (accountId: string) => {
    setActionLoading(accountId);
    try {
      await api.v3SetPrimaryAccount(accountId);
      await loadAccounts();
    } catch (e: any) {
      alert(e?.message || 'Failed to set primary account.');
    } finally {
      setActionLoading(null);
    }
  };

  const hasPrimary = accounts.some((a) => a.role === 'primary' && a.status === 'linked');
  const canAddMore = accounts.length < 3;

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-xl">
      <div className="flex items-center justify-between pb-4 border-b border-neutral-800">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-sky-500/10 text-sky-400 flex items-center justify-center">
            <Users className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-semibold text-white">Linked Telegram Accounts</h3>
            <p className="text-xs text-neutral-400">
              Manage primary MTProto identity and secondary Turbo acceleration accounts (max 3).
            </p>
          </div>
        </div>

        {canAddMore && (
          <Button
            size="sm"
            onClick={() => {
              setLinkRole(hasPrimary ? 'turbo' : 'primary');
              setLinkModalOpen(true);
            }}
            className="text-xs gap-1.5"
          >
            <Plus className="w-4 h-4" />
            Link Account
          </Button>
        )}
      </div>

      <div className="mt-4 space-y-3">
        {loading && (
          <div className="flex items-center justify-center py-8 text-neutral-500 text-xs">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            Loading accounts...
          </div>
        )}

        {!loading && accounts.length === 0 && (
          <div className="text-center py-8 text-neutral-500 text-xs">
            No Telegram accounts linked yet. Click "Link Account" above to get started.
          </div>
        )}

        {!loading &&
          accounts.map((acc) => (
            <div
              key={acc.id}
              className="flex items-center justify-between p-3.5 bg-neutral-800/60 border border-neutral-700/60 rounded-xl hover:border-neutral-600 transition-colors"
            >
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-neutral-700/80 flex items-center justify-center text-sm font-semibold text-white">
                  {(acc.first_name || acc.username || 'TG')[0].toUpperCase()}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm text-white">
                      {acc.first_name || acc.username || `User ${acc.telegram_user_id}`}
                    </span>
                    {acc.username && (
                      <span className="text-xs text-neutral-400">@{acc.username}</span>
                    )}
                    {acc.role === 'primary' ? (
                      <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-500/40 text-[10px]">
                        Primary
                      </Badge>
                    ) : (
                      <Badge className="bg-amber-500/20 text-amber-400 border-amber-500/40 text-[10px] gap-1">
                        <Zap className="w-2.5 h-2.5" />
                        Turbo
                      </Badge>
                    )}
                    {acc.status === 'relink_required' && (
                      <Badge className="bg-rose-500/20 text-rose-400 border-rose-500/40 text-[10px] gap-1">
                        <ShieldAlert className="w-2.5 h-2.5" />
                        Relink Required
                      </Badge>
                    )}
                  </div>
                  <p className="text-[11px] text-neutral-500 mt-0.5">
                    ID: {acc.telegram_user_id} • Linked: {new Date(acc.created_at).toLocaleDateString()}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                {acc.role === 'turbo' && acc.status === 'linked' && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleSetPrimary(acc.id)}
                    disabled={actionLoading === acc.id}
                    className="text-xs text-neutral-300 hover:text-white"
                  >
                    <Star className="w-3.5 h-3.5 mr-1" />
                    Make Primary
                  </Button>
                )}
                {acc.status === 'relink_required' && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setLinkRole(acc.role);
                      setLinkModalOpen(true);
                    }}
                    className="text-xs text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
                  >
                    Relink Now
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleUnlink(acc.id)}
                  disabled={actionLoading === acc.id}
                  className="text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 p-2"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          ))}
      </div>

      <TelegramLinkModal
        isOpen={linkModalOpen}
        onClose={() => setLinkModalOpen(false)}
        onSuccess={loadAccounts}
        defaultRole={linkRole}
      />
    </div>
  );
};
