import React, { useState, useEffect, useRef } from 'react';
import { api } from '@/services/api';
import type { V3LinkAttempt } from '@/types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { QrCode, Lock, CheckCircle2, AlertCircle, X, Loader2, Sparkles, Smartphone } from 'lucide-react';

interface TelegramLinkModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  defaultRole?: 'primary' | 'turbo';
}

export const TelegramLinkModal: React.FC<TelegramLinkModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  defaultRole = 'primary',
}) => {
  const [role, setRole] = useState<'primary' | 'turbo'>(defaultRole);
  const [attempt, setAttempt] = useState<V3LinkAttempt | null>(null);
  const [loading, setLoading] = useState(false);
  const [twoFaPassword, setTwoFaPassword] = useState('');
  const [twoFaLoading, setTwoFaLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (isOpen) {
      setRole(defaultRole);
      setAttempt(null);
      setError(null);
      startAttempt(defaultRole);
    } else {
      stopPolling();
    }
    return () => stopPolling();
  }, [isOpen, defaultRole]);

  const stopPolling = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  };

  const startAttempt = async (targetRole: 'primary' | 'turbo') => {
    setLoading(true);
    setError(null);
    setTwoFaPassword('');
    stopPolling();

    try {
      const newAttempt = await api.v3StartLinkAttempt(targetRole);
      setAttempt(newAttempt);
      setLoading(false);
      startPolling(newAttempt.id);
    } catch (e: any) {
      setLoading(false);
      setError(e?.message || 'Failed to initialize Telegram QR link.');
    }
  };

  const startPolling = (attemptId: string) => {
    stopPolling();
    pollIntervalRef.current = setInterval(async () => {
      try {
        const updated = await api.v3GetLinkAttempt(attemptId);
        setAttempt(updated);

        if (updated.state === 'LINKED') {
          stopPolling();
          setTimeout(() => {
            onSuccess();
            onClose();
          }, 1500);
        } else if (['FAILED', 'CANCELLED', 'EXPIRED'].includes(updated.state)) {
          stopPolling();
          if (updated.error_message) {
            setError(updated.error_message);
          }
        }
      } catch (e: any) {
        stopPolling();
        setError(e?.message || 'Error tracking linking status.');
      }
    }, 1500);
  };

  const handle2FASubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!attempt || !twoFaPassword) return;

    setTwoFaLoading(true);
    setError(null);
    try {
      await api.v3Submit2FA(attempt.id, twoFaPassword);
      setTwoFaPassword('');
    } catch (e: any) {
      setError(e?.message || 'Failed to submit 2FA password.');
    } finally {
      setTwoFaLoading(false);
    }
  };

  const handleCancel = async () => {
    if (attempt?.id && !['LINKED', 'FAILED', 'CANCELLED', 'EXPIRED'].includes(attempt.state)) {
      try {
        await api.v3CancelLinkAttempt(attempt.id);
      } catch {}
    }
    stopPolling();
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md p-4 animate-in fade-in duration-200">
      <div className="relative w-full max-w-md bg-neutral-900 border border-neutral-800 rounded-2xl shadow-2xl p-6 text-white">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-neutral-800">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center">
              <QrCode className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-semibold text-lg">Link Telegram Account</h3>
              <p className="text-xs text-neutral-400">Scan QR in Telegram App: Settings → Devices</p>
            </div>
          </div>
          <button
            onClick={handleCancel}
            className="text-neutral-400 hover:text-white transition-colors p-1 rounded-lg hover:bg-neutral-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Role Toggle */}
        <div className="mt-4 flex gap-2">
          <Button
            type="button"
            variant={role === 'primary' ? 'default' : 'outline'}
            size="sm"
            className="flex-1 text-xs"
            onClick={() => {
              setRole('primary');
              startAttempt('primary');
            }}
            disabled={loading}
          >
            Primary Account
          </Button>
          <Button
            type="button"
            variant={role === 'turbo' ? 'default' : 'outline'}
            size="sm"
            className="flex-1 text-xs"
            onClick={() => {
              setRole('turbo');
              startAttempt('turbo');
            }}
            disabled={loading}
          >
            Turbo Account (Secondary)
          </Button>
        </div>

        {/* Content Body */}
        <div className="my-6 flex flex-col items-center justify-center min-h-[260px]">
          {loading && (
            <div className="flex flex-col items-center gap-3 text-neutral-400">
              <Loader2 className="w-8 h-8 animate-spin text-emerald-400" />
              <span className="text-sm">Generating secure QR code...</span>
            </div>
          )}

          {!loading && attempt?.state === 'LINKED' && (
            <div className="flex flex-col items-center gap-3 text-emerald-400 animate-in zoom-in-95 duration-200">
              <CheckCircle2 className="w-16 h-16" />
              <span className="text-lg font-medium text-white">Telegram Account Linked!</span>
              <p className="text-xs text-neutral-400">
                {attempt.username ? `@${attempt.username}` : `ID: ${attempt.telegram_user_id}`}
              </p>
            </div>
          )}

          {!loading && attempt?.state === '2FA_REQUIRED' && (
            <form onSubmit={handle2FASubmit} className="w-full flex flex-col items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center">
                <Lock className="w-6 h-6" />
              </div>
              <div className="text-center">
                <h4 className="font-semibold text-white">Two-Step Verification</h4>
                <p className="text-xs text-neutral-400 mt-1">
                  Enter your Telegram cloud password to complete linking.
                </p>
              </div>
              <input
                type="password"
                value={twoFaPassword}
                onChange={(e) => setTwoFaPassword(e.target.value)}
                placeholder="Cloud Password"
                className="w-full bg-neutral-800 border border-neutral-700 rounded-xl px-4 py-2.5 text-sm text-white placeholder-neutral-500 focus:outline-none focus:border-emerald-500"
                autoFocus
                required
              />
              <Button type="submit" disabled={twoFaLoading || !twoFaPassword} className="w-full text-xs">
                {twoFaLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                Verify & Link Account
              </Button>
            </form>
          )}

          {!loading && attempt?.qr_data_uri && ['CREATED', 'QR_PENDING', 'TELEGRAM_AUTHENTICATING'].includes(attempt.state) && (
            <div className="flex flex-col items-center gap-3">
              <div className="p-3 bg-white rounded-2xl shadow-inner">
                <img
                  src={attempt.qr_data_uri}
                  alt="Telegram QR Login"
                  className="w-48 h-48 rounded-lg"
                />
              </div>

              {attempt.state === 'TELEGRAM_AUTHENTICATING' ? (
                <div className="flex items-center gap-2 text-emerald-400 text-xs animate-pulse">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Authorizing on mobile app...</span>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-neutral-400 text-xs">
                  <Smartphone className="w-3.5 h-3.5 text-neutral-500" />
                  <span>Open Telegram → Settings → Devices → Link Desktop Device</span>
                </div>
              )}

              {attempt.expires_in > 0 && (
                <Badge variant="outline" className="text-[11px] text-neutral-400 border-neutral-700">
                  Expires in {attempt.expires_in}s
                </Badge>
              )}
            </div>
          )}

          {error && (
            <div className="mt-4 p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-xs flex items-start gap-2 w-full">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 pt-4 border-t border-neutral-800">
          <Button variant="outline" size="sm" onClick={handleCancel} className="text-xs">
            Close
          </Button>
          {attempt && ['FAILED', 'EXPIRED', 'CANCELLED'].includes(attempt.state) && (
            <Button size="sm" onClick={() => startAttempt(role)} className="text-xs">
              Try Again
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};
