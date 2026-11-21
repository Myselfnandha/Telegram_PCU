import React, { useState, useEffect } from 'react';
import { Shield, KeyRound, Eye, EyeOff, CheckCircle2, Lock, AlertCircle, ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';
import { useApp } from '@/context/AppContext';

interface DeviceAuthGateModalProps {
  isOpen: boolean;
  onSuccess: () => void;
}

export const DeviceAuthGateModal: React.FC<DeviceAuthGateModalProps> = ({
  isOpen,
  onSuccess,
}) => {
  const { addToast, refreshAuth } = useApp();
  const [tokenInput, setTokenInput] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rememberDevice, setRememberDevice] = useState(true);

  useEffect(() => {
    if (isOpen) {
      const existing = api.getStoredToken();
      if (existing) {
        setTokenInput(existing);
      }
    }
  }, [isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const token = tokenInput.trim();
    if (!token) {
      setError('Please enter your API Security Key');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const res = await api.deviceLogin(token);
      if (res.success) {
        if (rememberDevice) {
          api.setStoredToken(token);
        } else {
          api.setStoredToken('');
        }

        // Reconnect WebSocket with the verified token
        socketService.reconnectWithToken(token);

        addToast({
          type: 'success',
          title: 'Device Authorized',
          message: 'Secure cloud session established.',
        });

        await refreshAuth();
        onSuccess();
      } else {
        setError(res.message || 'Authorization failed');
      }
    } catch (err: any) {
      setError(err.message || 'Invalid API Security Key. Check your server settings.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={() => {}}>
      <DialogContent
        className="sm:max-w-[420px] bg-card/95 backdrop-blur-xl border-border shadow-2xl p-6"
        showCloseButton={false}
      >
        <DialogHeader className="space-y-2 text-center items-center">
          <div className="w-12 h-12 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center text-primary shadow-inner mb-1">
            <Shield className="w-6 h-6 animate-pulse" />
          </div>
          <DialogTitle className="text-lg font-bold tracking-tight">
            Private Device Gate
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground max-w-[320px] leading-relaxed">
            This server is private. Enter the <code className="text-primary font-mono font-semibold">API_AUTH_TOKEN</code> configured in your server environment or Secret File to authorize this device.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-2">
          {error && (
            <div className="p-3 rounded-xl bg-destructive/10 border border-destructive/20 text-destructive text-xs flex items-start gap-2.5 animate-in fade-in duration-200">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div className="flex-1 font-medium">{error}</div>
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <KeyRound className="w-3.5 h-3.5 text-primary" />
              <span>Security Token</span>
            </label>
            <div className="relative">
              <Input
                type={showToken ? 'text' : 'password'}
                placeholder="Paste API_AUTH_TOKEN here..."
                value={tokenInput}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                  setTokenInput(e.target.value);
                  setError(null);
                }}
                disabled={loading}
                autoFocus
                className="pr-10 font-mono text-xs bg-background/50 border-input h-10"
              />
              <button
                type="button"
                onClick={() => setShowToken(!showToken)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer p-1"
                tabIndex={-1}
              >
                {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between text-xs text-muted-foreground py-0.5">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={rememberDevice}
                onChange={(e) => setRememberDevice(e.target.checked)}
                className="rounded border-border accent-primary cursor-pointer w-3.5 h-3.5"
              />
              <span>Remember this device</span>
            </label>
            <span className="text-[10px] text-muted-foreground/70">1 year session</span>
          </div>

          <Button
            type="submit"
            className="w-full gap-2 cursor-pointer h-10 shadow-lg shadow-primary/20"
            disabled={loading || !tokenInput.trim()}
          >
            {loading ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin" />
                <span>Verifying Key...</span>
              </>
            ) : (
              <>
                <Lock className="w-4 h-4" />
                <span>Authorize Device</span>
                <ArrowRight className="w-3.5 h-3.5 ml-auto opacity-70" />
              </>
            )}
          </Button>

          <p className="text-[10px] text-center text-muted-foreground/70 leading-relaxed pt-1">
            Tip: You can also bookmark <code className="text-foreground/80 font-mono">https://your-domain.com/?token=YOUR_KEY</code> to auto-login.
          </p>
        </form>
      </DialogContent>
    </Dialog>
  );
};
