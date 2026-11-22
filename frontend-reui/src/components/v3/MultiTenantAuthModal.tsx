import React, { useState } from 'react';
import { api } from '@/services/api';
import { Button } from '@/components/ui/button';
import { ShieldCheck, Lock, User, KeyRound, AlertCircle, CheckCircle2, X, Loader2, Copy } from 'lucide-react';

interface MultiTenantAuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export const MultiTenantAuthModal: React.FC<MultiTenantAuthModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
}) => {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [deviceName, setDeviceName] = useState('Current Browser');
  const [bootstrapToken, setBootstrapToken] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [approvalCode, setApprovalCode] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      if (mode === 'register') {
        const res = await api.v3Register({
          username,
          password,
          device_name: deviceName,
          bootstrap_token: bootstrapToken || undefined,
        });
        if (res.recovery_codes && res.recovery_codes.length > 0) {
          setRecoveryCodes(res.recovery_codes);
        } else {
          onSuccess();
          onClose();
        }
      } else {
        const res = await api.v3Login({
          username,
          password,
          device_name: deviceName,
        });
        if (!res.device_verified && res.approval_code) {
          setApprovalCode(res.approval_code);
        } else {
          onSuccess();
          onClose();
        }
      }
    } catch (err: any) {
      setError(err?.message || 'Authentication failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md p-4 animate-in fade-in duration-200">
      <div className="relative w-full max-w-md bg-neutral-900 border border-neutral-800 rounded-2xl shadow-2xl p-6 text-white">
        <div className="flex items-center justify-between pb-4 border-b border-neutral-800">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center">
              <ShieldCheck className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-semibold text-lg">
                {recoveryCodes.length > 0
                  ? 'Save Recovery Codes'
                  : approvalCode
                  ? 'Device Approval Required'
                  : mode === 'login'
                  ? 'Sign In to TG Power Suite'
                  : 'Create Identity'}
              </h3>
              <p className="text-xs text-neutral-400">TG Power Suite v3.0 Multi-Tenant Access</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-400 hover:text-white p-1 rounded-lg hover:bg-neutral-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Recovery codes display */}
        {recoveryCodes.length > 0 && (
          <div className="my-6 space-y-4">
            <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl text-amber-400 text-xs">
              Save these recovery codes securely. They will never be shown again and can be used to recover your account.
            </div>
            <div className="grid grid-cols-2 gap-2 bg-neutral-950 p-4 rounded-xl border border-neutral-800 font-mono text-xs text-emerald-400">
              {recoveryCodes.map((code, idx) => (
                <div key={idx} className="p-1.5 bg-neutral-900 rounded text-center border border-neutral-800">
                  {code}
                </div>
              ))}
            </div>
            <Button
              className="w-full text-xs"
              onClick={() => {
                navigator.clipboard.writeText(recoveryCodes.join('\n'));
                onSuccess();
                onClose();
              }}
            >
              <Copy className="w-3.5 h-3.5 mr-1.5" />
              Copy Codes & Continue
            </Button>
          </div>
        )}

        {/* Untrusted Device Approval Code */}
        {approvalCode && recoveryCodes.length === 0 && (
          <div className="my-6 space-y-4 text-center">
            <div className="p-4 bg-purple-500/10 border border-purple-500/30 rounded-xl text-purple-300 text-xs text-left">
              This device is not yet verified. Please approve it from an active trusted session or enter this code:
            </div>
            <div className="p-4 bg-neutral-950 rounded-xl border border-neutral-800 text-2xl font-mono tracking-widest text-purple-400 font-bold">
              {approvalCode}
            </div>
            <Button
              className="w-full text-xs"
              onClick={() => {
                onSuccess();
                onClose();
              }}
            >
              Got it
            </Button>
          </div>
        )}

        {/* Form Body */}
        {recoveryCodes.length === 0 && !approvalCode && (
          <form onSubmit={handleSubmit} className="my-5 space-y-3.5">
            <div className="flex bg-neutral-800 p-1 rounded-xl">
              <button
                type="button"
                onClick={() => setMode('login')}
                className={`flex-1 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                  mode === 'login' ? 'bg-neutral-700 text-white shadow' : 'text-neutral-400 hover:text-white'
                }`}
              >
                Sign In
              </button>
              <button
                type="button"
                onClick={() => setMode('register')}
                className={`flex-1 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                  mode === 'register' ? 'bg-neutral-700 text-white shadow' : 'text-neutral-400 hover:text-white'
                }`}
              >
                Register
              </button>
            </div>

            <div className="space-y-1">
              <label className="text-xs text-neutral-400">Username</label>
              <div className="relative">
                <User className="w-4 h-4 text-neutral-500 absolute left-3 top-3" />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Username"
                  className="w-full bg-neutral-800 border border-neutral-700 rounded-xl pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500"
                  required
                />
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-xs text-neutral-400">Password</label>
              <div className="relative">
                <Lock className="w-4 h-4 text-neutral-500 absolute left-3 top-3" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Password"
                  className="w-full bg-neutral-800 border border-neutral-700 rounded-xl pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500"
                  required
                />
              </div>
            </div>

            {mode === 'register' && (
              <>
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400">Device Name</label>
                  <input
                    type="text"
                    value={deviceName}
                    onChange={(e) => setDeviceName(e.target.value)}
                    placeholder="e.g. My Laptop"
                    className="w-full bg-neutral-800 border border-neutral-700 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400">Admin Bootstrap Token (Optional)</label>
                  <input
                    type="password"
                    value={bootstrapToken}
                    onChange={(e) => setBootstrapToken(e.target.value)}
                    placeholder="Leave empty unless configuring first admin"
                    className="w-full bg-neutral-800 border border-neutral-700 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500"
                  />
                </div>
              </>
            )}

            {error && (
              <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <Button type="submit" disabled={loading} className="w-full text-xs mt-2">
              {loading && <Loader2 className="w-4 h-4 animate-spin mr-1.5" />}
              {mode === 'login' ? 'Sign In' : 'Create Identity'}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
};
