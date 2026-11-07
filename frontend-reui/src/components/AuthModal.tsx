import React, { useState, useEffect, useRef } from 'react';
import { useApp } from '@/context/AppContext';
import { api } from '@/services/api';
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
  KeyRound,
  Lock,
  LogOut,
  Phone,
  QrCode,
  RefreshCw,
  ShieldCheck,
  Smartphone,
} from 'lucide-react';

export const AuthModal: React.FC = () => {
  const { isAuthModalOpen, setAuthModalOpen, authStatus, refreshAuth, addToast } = useApp();

  const [loginMethod, setLoginMethod] = useState<'qr' | 'phone'>('qr');
  const [step, setStep] = useState<'phone' | 'code' | '2fa'>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [phoneCodeHash, setPhoneCodeHash] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // QR Code State
  const [qrImage, setQrImage] = useState<string | null>(null);
  const [qrLoading, setQrLoading] = useState(false);
  const [qrExpired, setQrExpired] = useState(false);
  const [qrError, setQrError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const resetForm = () => {
    setLoginMethod('qr');
    setStep('phone');
    setPhone('');
    setCode('');
    setPhoneCodeHash('');
    setPassword('');
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

  // Start QR Code generation
  const handleStartQr = async () => {
    clearQrTimer();
    setQrLoading(true);
    setQrExpired(false);
    setQrError(null);

    try {
      const res = await api.startQrLogin();
      if (res.qr_image) {
        setQrImage(res.qr_image);
      }
      if (res.status === 'success' || res.status === 'authorized') {
        addToast({
          type: 'success',
          title: 'Authorized',
          message: 'Welcome back to Telegram!',
        });
        await refreshAuth();
        setAuthModalOpen(false);
        resetForm();
      }
    } catch (err: any) {
      setQrError(err.message || 'Failed to generate QR code');
    } finally {
      setQrLoading(false);
    }
  };

  // QR polling lifecycle
  useEffect(() => {
    if (!isAuthModalOpen || authStatus?.authenticated || loginMethod !== 'qr' || qrExpired) {
      clearQrTimer();
      return;
    }

    // Auto-fetch QR code if not loaded yet
    if (!qrImage && !qrLoading) {
      handleStartQr();
    }

    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await api.getQrStatus();
        if (res.status === 'success' || res.status === 'authorized') {
          clearQrTimer();
          addToast({
            type: 'success',
            title: 'Authorized',
            message: `Welcome, ${res.user?.first_name || 'Telegram User'}!`,
          });
          await refreshAuth();
          setAuthModalOpen(false);
          resetForm();
        } else if (res.status === '2fa_required') {
          clearQrTimer();
          addToast({
            type: 'info',
            title: '2FA Required',
            message: 'QR verified! Please enter your Two-Step Verification Cloud Password.',
          });
          setLoginMethod('phone');
          setStep('2fa');
        } else if (res.status === 'expired') {
          clearQrTimer();
          setQrExpired(true);
        } else if (res.status === 'error') {
          clearQrTimer();
          setQrError(res.message || 'QR session error. Please refresh.');
        }
      } catch {
        // Silent poll error
      }
    }, 1500);

    return () => clearQrTimer();
  }, [isAuthModalOpen, authStatus?.authenticated, loginMethod, qrExpired, qrImage]);

  const handleSendCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phone.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const res = await api.sendCode(phone.trim());
      setPhoneCodeHash(res.phone_code_hash);
      setStep('code');
      addToast({
        type: 'info',
        title: 'Code Sent',
        message: 'A Telegram confirmation code was sent to your active devices.',
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
      const res = await api.signIn(phone.trim(), code.trim(), phoneCodeHash);
      if (res.authenticated) {
        addToast({
          type: 'success',
          title: 'Authorized',
          message: `Welcome, ${res.first_name || 'Telegram User'}!`,
        });
        await refreshAuth();
        setAuthModalOpen(false);
        resetForm();
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
      const res = await api.verify2FA(password.trim());
      if (res.authenticated) {
        addToast({
          type: 'success',
          title: 'Authorized',
          message: `Welcome back, ${res.first_name || 'User'}!`,
        });
        await refreshAuth();
        setAuthModalOpen(false);
        resetForm();
      }
    } catch (err: any) {
      setError(err.message || 'Incorrect 2FA password');
      addToast({ type: 'error', title: '2FA Failed', message: err.message });
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = async () => {
    setLoading(true);
    try {
      await api.logout();
      await refreshAuth();
      addToast({ type: 'info', title: 'Logged Out', message: 'Signed out of Telegram session.' });
      setAuthModalOpen(false);
      resetForm();
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog
      open={isAuthModalOpen}
      onOpenChange={(open) => {
        setAuthModalOpen(open);
        if (!open) clearQrTimer();
      }}
    >
      <DialogContent className="sm:max-w-md bg-card/95 backdrop-blur-xl border-border shadow-2xl">
        <DialogHeader>
          <div className="flex items-center gap-2 mb-1">
            <div className="p-2 rounded-lg bg-primary/10 text-primary">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <DialogTitle className="text-lg font-semibold">
              {authStatus?.authenticated ? 'Telegram Account' : 'Connect Telegram MTProto'}
            </DialogTitle>
          </div>
          <DialogDescription className="text-xs text-muted-foreground">
            {authStatus?.authenticated
              ? 'Your active MTProto session has direct access to cloud storage, channels, and video streaming.'
              : 'Sign in to access your channels, upload up to 4GB files, and stream high-speed video.'}
          </DialogDescription>
        </DialogHeader>

        {authStatus?.authenticated ? (
          <div className="space-y-4 py-2">
            <div className="p-4 rounded-xl border border-border bg-accent/30 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Status</span>
                <Badge variant="outline" className="bg-emerald-500/10 text-emerald-500 border-emerald-500/20 gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Authorized
                </Badge>
              </div>

              <div className="flex items-center gap-3 pt-2">
                <div className="w-10 h-10 rounded-full bg-primary/20 flex items-center justify-center font-bold text-primary">
                  {authStatus.first_name ? authStatus.first_name.charAt(0).toUpperCase() : 'U'}
                </div>
                <div>
                  <div className="text-sm font-semibold text-foreground">
                    {authStatus.first_name || 'Telegram User'}
                  </div>
                  {authStatus.username && (
                    <div className="text-xs text-muted-foreground">@{authStatus.username}</div>
                  )}
                </div>
              </div>

              {authStatus.phone && (
                <div className="text-xs text-muted-foreground pt-1 flex items-center gap-1.5">
                  <Phone className="w-3.5 h-3.5" />
                  <span>{authStatus.phone}</span>
                </div>
              )}
            </div>

            <Button
              variant="destructive"
              className="w-full gap-2 cursor-pointer"
              onClick={handleLogout}
              disabled={loading}
            >
              <LogOut className="w-4 h-4" />
              <span>{loading ? 'Logging out...' : 'Log Out from Telegram'}</span>
            </Button>
          </div>
        ) : (
          <div className="py-2 space-y-4">
            {/* Login Mode Tabs: Scan QR vs Phone OTP */}
            {step !== '2fa' && (
              <div className="grid grid-cols-2 gap-1.5 p-1 bg-accent/40 rounded-xl border border-border">
                <button
                  type="button"
                  onClick={() => {
                    setLoginMethod('qr');
                    setError(null);
                  }}
                  className={`py-2 px-3 rounded-lg text-xs font-medium transition-all cursor-pointer flex items-center justify-center gap-2 ${
                    loginMethod === 'qr'
                      ? 'bg-card text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <QrCode className="w-4 h-4 text-primary" />
                  Quick QR Scan
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setLoginMethod('phone');
                    setError(null);
                    clearQrTimer();
                  }}
                  className={`py-2 px-3 rounded-lg text-xs font-medium transition-all cursor-pointer flex items-center justify-center gap-2 ${
                    loginMethod === 'phone'
                      ? 'bg-card text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Phone className="w-4 h-4 text-primary" />
                  Phone Number
                </button>
              </div>
            )}

            {error && (
              <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-xs text-destructive">
                {error}
              </div>
            )}

            {/* TAB 1: QR Code Login */}
            {loginMethod === 'qr' && step !== '2fa' && (
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
                        alt="Telegram QR Login"
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
                        <p className="text-xs text-muted-foreground">This login code has expired for security.</p>
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
                    How to log in with Telegram:
                  </div>
                  <ol className="text-[11px] text-muted-foreground space-y-1 list-decimal list-inside pl-1 leading-relaxed">
                    <li>Open <strong>Telegram</strong> on your phone</li>
                    <li>Go to <strong>Settings → Devices → Link Desktop Device</strong></li>
                    <li>Point your camera at the QR code above</li>
                  </ol>
                </div>
              </div>
            )}

            {/* TAB 2: Phone Number Form */}
            {loginMethod === 'phone' && step === 'phone' && (
              <form onSubmit={handleSendCode} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-foreground">Phone Number</label>
                  <div className="relative">
                    <Phone className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      type="tel"
                      placeholder="+1234567890"
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      className="pl-9 h-10"
                      required
                      autoFocus
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Include country code (e.g. +1 for US, +91 for India).
                  </p>
                </div>
                <Button type="submit" className="w-full h-10 cursor-pointer" disabled={loading}>
                  {loading ? 'Sending code...' : 'Send Verification Code'}
                </Button>
              </form>
            )}

            {loginMethod === 'phone' && step === 'code' && (
              <form onSubmit={handleSignIn} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-foreground">Verification Code</label>
                  <div className="relative">
                    <KeyRound className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      type="text"
                      placeholder="12345"
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                      className="pl-9 h-10 tracking-widest font-mono"
                      required
                      autoFocus
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Enter the code sent to your active Telegram application.
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    className="flex-1 cursor-pointer"
                    onClick={() => setStep('phone')}
                    disabled={loading}
                  >
                    Back
                  </Button>
                  <Button type="submit" className="flex-1 cursor-pointer" disabled={loading}>
                    {loading ? 'Signing in...' : 'Sign In'}
                  </Button>
                </div>
              </form>
            )}

            {step === '2fa' && (
              <form onSubmit={handle2FA} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-foreground">2FA Cloud Password</label>
                  <div className="relative">
                    <Lock className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      type="password"
                      placeholder="Enter your 2FA password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="pl-9 h-10"
                      required
                      autoFocus
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Your Telegram account is protected with Two-Step Verification.
                  </p>
                </div>
                <Button type="submit" className="w-full h-10 cursor-pointer" disabled={loading}>
                  {loading ? 'Verifying...' : 'Unlock Account'}
                </Button>
              </form>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
