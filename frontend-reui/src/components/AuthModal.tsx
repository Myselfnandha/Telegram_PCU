import React, { useState } from 'react';
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
import { CheckCircle2, KeyRound, Lock, LogOut, Phone, ShieldCheck, UserCheck } from 'lucide-react';

export const AuthModal: React.FC = () => {
  const { isAuthModalOpen, setAuthModalOpen, authStatus, refreshAuth, addToast } = useApp();

  const [step, setStep] = useState<'phone' | 'code' | '2fa'>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [phoneCodeHash, setPhoneCodeHash] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resetForm = () => {
    setStep('phone');
    setPhone('');
    setCode('');
    setPhoneCodeHash('');
    setPassword('');
    setError(null);
  };

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
    <Dialog open={isAuthModalOpen} onOpenChange={setAuthModalOpen}>
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
          <div className="py-2">
            {error && (
              <div className="p-3 mb-4 rounded-lg bg-destructive/10 border border-destructive/20 text-xs text-destructive">
                {error}
              </div>
            )}

            {step === 'phone' && (
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

            {step === 'code' && (
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
