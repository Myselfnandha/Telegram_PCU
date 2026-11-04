import React, { useState, useEffect, useRef } from 'react';
import { api } from '@/services/api';
import type { SecondaryAuthStatus } from '@/types';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  CheckCircle2,
  FileCode,
  KeyRound,
  Lock,
  LogOut,
  Phone,
  QrCode,
  RefreshCw,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Zap,
} from 'lucide-react';

interface SecondaryAuthModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: SecondaryAuthStatus | null;
  onSuccess: () => void;
  addToast: (toast: { type: 'success' | 'error' | 'info' | 'warning'; title: string; message: string }) => void;
}

export const SecondaryAuthModal: React.FC<SecondaryAuthModalProps> = ({
  open,
  onOpenChange,
  status,
  onSuccess,
  addToast,
}) => {
  const [tab, setTab] = useState<'qr' | 'otp' | 'session'>('qr');
  const [step, setStep] = useState<'phone' | 'code' | '2fa'>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [phoneCodeHash, setPhoneCodeHash] = useState('');
  const [password, setPassword] = useState('');
  const [sessionString, setSessionString] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // QR Code State for Secondary
  const [qrImage, setQrImage] = useState<string | null>(null);
  const [qrLoading, setQrLoading] = useState(false);
  const [qrExpired, setQrExpired] = useState(false);
  const [qrError, setQrError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const resetForm = () => {
    setTab('qr');
    setStep('phone');
    setPhone('');
    setCode('');
    setPhoneCodeHash('');
    setPassword('');
    setSessionString('');
    setError(null);
    setQrImage(null);
    setQrExpired(false);
    setQrError(null);
  };

  const clearQrTimer = () => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  const handleStartQr = async () => {
    clearQrTimer();
    setQrLoading(true);
    setQrExpired(false);
    setQrError(null);

    try {
      const res = await api.startSecondaryQrLogin();
      if (res.qr_image) {
        setQrImage(res.qr_image);
      }
      if (res.status === 'success' || res.status === 'authorized') {
        addToast({
          type: 'success',
          title: 'Secondary Account Linked!',
          message: 'Multi-Account Turbo (2x speed) is now enabled.',
        });
        onSuccess();
        onOpenChange(false);
        resetForm();
      }
    } catch (err: any) {
      setQrError(err.message || 'Failed to generate QR code');
    } finally {
      setQrLoading(false);
    }
  };

  // Secondary QR polling lifecycle
  useEffect(() => {
    if (!open || status?.authenticated || tab !== 'qr' || qrExpired) {
      clearQrTimer();
      return;
    }

    if (!qrImage && !qrLoading) {
      handleStartQr();
    }

    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await api.getSecondaryQrStatus();
        if (res.status === 'success' || res.status === 'authorized') {
          clearQrTimer();
          addToast({
            type: 'success',
            title: 'Secondary Account Linked!',
            message: `Multi-Account Turbo active. Streaming speeds doubled (2x).`,
          });
          onSuccess();
          onOpenChange(false);
          resetForm();
        } else if (res.status === '2fa_required') {
          clearQrTimer();
          addToast({
            type: 'info',
            title: '2FA Required',
            message: 'Secondary QR verified! Please enter your 2FA password.',
          });
          setTab('otp');
          setStep('2fa');
        } else if (res.status === 'expired') {
          clearQrTimer();
          setQrExpired(true);
        } else if (res.status === 'error') {
          clearQrTimer();
          setQrError(res.message || 'Secondary QR session error. Please refresh.');
        }
      } catch {
        // Silent poll error
      }
    }, 1500);

    return () => clearQrTimer();
  }, [open, status?.authenticated, tab, qrExpired, qrImage]);

  const handleSendCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phone.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const res = await api.sendSecondaryCode(phone.trim());
      setPhoneCodeHash(res.phone_code_hash);
      setStep('code');
      addToast({
        type: 'info',
        title: 'Secondary Code Sent',
        message: 'A verification code was sent to your second Telegram account devices.',
      });
    } catch (err: any) {
      setError(err.message || 'Failed to send login code');
      addToast({ type: 'error', title: 'Error', message: err.message });
    } finally {
      setLoading(false);
    }
  };

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const res = await api.signInSecondary(phone.trim(), code.trim(), phoneCodeHash);
      if (res.status === 'authorized' || res.authenticated) {
        addToast({
          type: 'success',
          title: 'Secondary Account Linked!',
          message: 'Multi-Account Turbo is now active. Streaming speeds doubled (2x).',
        });
        onSuccess();
        onOpenChange(false);
        resetForm();
      } else if (res.status === '2fa_required') {
        setStep('2fa');
      }
    } catch (err: any) {
      if (err.message && err.message.toLowerCase().includes('2fa')) {
        setStep('2fa');
      } else {
        setError(err.message || 'Verification failed. Please check the code.');
        addToast({ type: 'error', title: 'Verification Failed', message: err.message });
      }
    } finally {
      setLoading(false);
    }
  };

  const handle2FA = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const res = await api.verifySecondary2FA(password.trim());
      if (res.status === 'authorized' || res.authenticated) {
        addToast({
          type: 'success',
          title: 'Secondary Account Linked!',
          message: 'Multi-Account Turbo 2x speed pooling enabled.',
        });
        onSuccess();
        onOpenChange(false);
        resetForm();
      }
    } catch (err: any) {
      setError(err.message || 'Incorrect 2FA password');
      addToast({ type: 'error', title: '2FA Failed', message: err.message });
    } finally {
      setLoading(false);
    }
  };

  const handleImportSession = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!sessionString.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const res = await api.importSecondarySessionString(sessionString.trim());
      if (res.status === 'authorized') {
        addToast({
          type: 'success',
          title: 'Session String Imported!',
          message: 'Secondary account active. Multi-Account Turbo (2x) is now running.',
        });
        onSuccess();
        onOpenChange(false);
        resetForm();
      }
    } catch (err: any) {
      setError(err.message || 'Failed to import session string');
      addToast({ type: 'error', title: 'Import Failed', message: err.message });
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = async () => {
    setLoading(true);
    try {
      await api.logoutSecondary();
      onSuccess();
      addToast({
        type: 'info',
        title: 'Secondary Account Disconnected',
        message: 'Reverted to single account MTProto mode.',
      });
      onOpenChange(false);
      resetForm();
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(val) => {
        onOpenChange(val);
        if (!val) clearQrTimer();
      }}
    >
      <DialogContent className="sm:max-w-md bg-card/95 backdrop-blur-xl border-border shadow-2xl">
        <DialogHeader>
          <div className="flex items-center gap-2 mb-1">
            <div className="p-2 rounded-lg bg-amber-500/10 text-amber-500">
              <Zap className="w-5 h-5 fill-amber-500/20" />
            </div>
            <div>
              <DialogTitle className="text-base font-semibold text-foreground flex items-center gap-2">
                Multi-Account Turbo (2x Speed)
                <Badge variant="outline" className="text-[10px] bg-amber-500/10 text-amber-400 border-amber-500/20">
                  Speed Booster
                </Badge>
              </DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground">
                Pool two Telegram MTProto accounts to bypass per-account limits and double your throughput.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {status?.authenticated && status.user ? (
          /* Already Connected State */
          <div className="space-y-4 py-2">
            <div className="p-4 rounded-xl border border-emerald-500/20 bg-emerald-500/10 space-y-2">
              <div className="flex items-center gap-2 text-emerald-400 font-semibold text-xs">
                <CheckCircle2 className="w-4 h-4" />
                Secondary Account Connected & Pooling Active
              </div>
              <div className="text-xs text-foreground font-medium">
                {status.user.name || status.user.first_name || 'Telegram User'}
                {status.user.username && <span className="text-muted-foreground ml-1">(@{status.user.username})</span>}
              </div>
              {status.user.phone && (
                <div className="text-[11px] text-muted-foreground font-mono">{status.user.phone}</div>
              )}
              {status.user.is_premium && (
                <Badge variant="outline" className="text-[10px] bg-primary/10 text-primary border-primary/20">
                  <Sparkles className="w-3 h-3 mr-1" /> Telegram Premium (Up to 48 MB/s Combined)
                </Badge>
              )}
            </div>

            <div className="text-xs text-muted-foreground bg-accent/30 p-3 rounded-lg border border-border">
              💡 Both accounts are actively downloading alternate chunks in parallel. If one account encounters a private channel, it automatically falls back with zero interruption.
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={handleLogout}
              disabled={loading}
              className="w-full text-xs text-destructive border-destructive/20 hover:bg-destructive/10 cursor-pointer"
            >
              <LogOut className="w-3.5 h-3.5 mr-1.5" />
              Disconnect Secondary Account
            </Button>
          </div>
        ) : (
          /* Connect Account Form */
          <div className="space-y-4 py-2">
            {/* Tabs: QR Code vs Phone OTP vs Session String */}
            <div className="grid grid-cols-3 gap-1 p-1 bg-accent/40 rounded-xl border border-border">
              <button
                type="button"
                onClick={() => {
                  setTab('qr');
                  setError(null);
                }}
                className={`py-1.5 px-2 rounded-lg text-xs font-medium transition-all cursor-pointer flex items-center justify-center gap-1.5 ${
                  tab === 'qr'
                    ? 'bg-card text-foreground shadow-xs font-semibold'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <QrCode className="w-3.5 h-3.5 text-primary" />
                Scan QR
              </button>
              <button
                type="button"
                onClick={() => {
                  setTab('otp');
                  setError(null);
                  clearQrTimer();
                }}
                className={`py-1.5 px-2 rounded-lg text-xs font-medium transition-all cursor-pointer flex items-center justify-center gap-1.5 ${
                  tab === 'otp'
                    ? 'bg-card text-foreground shadow-xs font-semibold'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <Phone className="w-3.5 h-3.5 text-primary" />
                Phone OTP
              </button>
              <button
                type="button"
                onClick={() => {
                  setTab('session');
                  setError(null);
                  clearQrTimer();
                }}
                className={`py-1.5 px-2 rounded-lg text-xs font-medium transition-all cursor-pointer flex items-center justify-center gap-1.5 ${
                  tab === 'session'
                    ? 'bg-card text-foreground shadow-xs font-semibold'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <FileCode className="w-3.5 h-3.5 text-primary" />
                String
              </button>
            </div>

            {error && (
              <div className="p-3 rounded-xl bg-destructive/10 border border-destructive/20 text-xs text-destructive">
                {error}
              </div>
            )}

            {/* TAB 1: QR Code Scanner */}
            {tab === 'qr' && (
              <div className="space-y-4">
                <div className="flex flex-col items-center justify-center">
                  <div className="relative p-3.5 bg-white rounded-2xl shadow-xl border border-border flex items-center justify-center">
                    {qrLoading && !qrImage ? (
                      <div className="w-52 h-52 flex flex-col items-center justify-center gap-2.5 text-slate-800">
                        <RefreshCw className="w-7 h-7 animate-spin text-primary" />
                        <span className="text-xs font-medium">Generating QR code...</span>
                      </div>
                    ) : qrImage ? (
                      <img
                        src={qrImage}
                        alt="Secondary Account QR Login"
                        className={`w-52 h-52 object-contain rounded-lg transition-opacity duration-200 ${
                          qrExpired ? 'opacity-20 blur-[1px]' : 'opacity-100'
                        }`}
                      />
                    ) : (
                      <div className="w-52 h-52 flex flex-col items-center justify-center gap-2 text-slate-800">
                        <QrCode className="w-10 h-10 text-muted-foreground" />
                        <span className="text-xs text-muted-foreground">No QR code</span>
                      </div>
                    )}

                    {/* Expired Overlay */}
                    {qrExpired && (
                      <div className="absolute inset-0 bg-background/80 backdrop-blur-xs rounded-2xl flex flex-col items-center justify-center p-4 text-center gap-2.5">
                        <Badge variant="outline" className="bg-destructive/10 text-destructive border-destructive/20">
                          QR Expired
                        </Badge>
                        <p className="text-xs text-muted-foreground">Code expired for security.</p>
                        <Button
                          size="sm"
                          onClick={handleStartQr}
                          className="gap-1.5 cursor-pointer text-xs"
                        >
                          <RefreshCw className="w-3.5 h-3.5" />
                          Refresh Code
                        </Button>
                      </div>
                    )}
                  </div>
                </div>

                {qrError && (
                  <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-xs text-destructive text-center">
                    {qrError}
                    <Button
                      variant="link"
                      size="sm"
                      onClick={handleStartQr}
                      className="text-xs text-primary underline ml-2 p-0 h-auto cursor-pointer"
                    >
                      Try Again
                    </Button>
                  </div>
                )}

                {/* Steps instructions */}
                <div className="p-3 rounded-xl bg-accent/30 border border-border space-y-2">
                  <div className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                    <Smartphone className="w-3.5 h-3.5 text-primary" />
                    How to link secondary account:
                  </div>
                  <ol className="text-[11px] text-muted-foreground space-y-1 list-decimal list-inside pl-1 leading-relaxed">
                    <li>Open <strong>Telegram</strong> on your secondary device</li>
                    <li>Go to <strong>Settings → Devices → Link Desktop Device</strong></li>
                    <li>Scan this code to enable <strong>2x Multi-Account Turbo</strong></li>
                  </ol>
                </div>
              </div>
            )}

            {/* TAB 2: Phone OTP Form */}
            {tab === 'otp' && (
              <>
                {step === 'phone' && (
                  <form onSubmit={handleSendCode} className="space-y-3.5">
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-foreground">Secondary Phone Number</label>
                      <Input
                        type="tel"
                        placeholder="+1 234 567 8900"
                        value={phone}
                        onChange={(e) => setPhone(e.target.value)}
                        className="text-xs font-mono"
                        autoFocus
                        disabled={loading}
                      />
                      <p className="text-[11px] text-muted-foreground">
                        Include country code (e.g., +1, +91, +44).
                      </p>
                    </div>

                    <Button type="submit" disabled={loading || !phone.trim()} className="w-full text-xs cursor-pointer">
                      {loading ? 'Sending Code...' : 'Send Verification Code'}
                    </Button>
                  </form>
                )}

                {step === 'code' && (
                  <form onSubmit={handleSignIn} className="space-y-3.5">
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-foreground">Telegram Login Code</label>
                      <Input
                        type="text"
                        placeholder="12345"
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                        className="text-xs font-mono tracking-widest text-center text-base"
                        autoFocus
                        disabled={loading}
                      />
                      <p className="text-[11px] text-muted-foreground text-center">
                        Code sent to your secondary Telegram account.
                      </p>
                    </div>

                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setStep('phone')}
                        disabled={loading}
                        className="w-1/3 text-xs cursor-pointer"
                      >
                        Back
                      </Button>
                      <Button type="submit" disabled={loading || !code.trim()} className="w-2/3 text-xs cursor-pointer">
                        {loading ? 'Verifying...' : 'Sign In'}
                      </Button>
                    </div>
                  </form>
                )}

                {step === '2fa' && (
                  <form onSubmit={handle2FA} className="space-y-3.5">
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                        <Lock className="w-3.5 h-3.5 text-primary" />
                        Two-Step Verification Password
                      </label>
                      <Input
                        type="password"
                        placeholder="Enter your 2FA password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        className="text-xs"
                        autoFocus
                        disabled={loading}
                      />
                    </div>

                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setStep('code')}
                        disabled={loading}
                        className="w-1/3 text-xs cursor-pointer"
                      >
                        Back
                      </Button>
                      <Button type="submit" disabled={loading || !password.trim()} className="w-2/3 text-xs cursor-pointer">
                        {loading ? 'Authorizing...' : 'Authorize'}
                      </Button>
                    </div>
                  </form>
                )}
              </>
            )}

            {/* TAB 3: Session String Direct Paste */}
            {tab === 'session' && (
              <form onSubmit={handleImportSession} className="space-y-3.5">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                    <KeyRound className="w-3.5 h-3.5 text-primary" />
                    Telethon StringSession
                  </label>
                  <textarea
                    rows={4}
                    placeholder="1BJWap1wBu8..."
                    value={sessionString}
                    onChange={(e) => setSessionString(e.target.value)}
                    className="w-full text-[11px] font-mono p-2.5 rounded-xl border border-border bg-accent/20 text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary"
                    disabled={loading}
                  />
                  <p className="text-[11px] text-muted-foreground">
                    Paste an authorized Telethon StringSession string from any tool or bot.
                  </p>
                </div>

                <Button
                  type="submit"
                  disabled={loading || !sessionString.trim()}
                  className="w-full text-xs cursor-pointer"
                >
                  {loading ? 'Validating Session...' : 'Link Secondary Account'}
                </Button>
              </form>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
