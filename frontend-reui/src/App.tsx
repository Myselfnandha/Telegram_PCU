import React, { Suspense, useState, useEffect } from 'react';
import { AppProvider, useApp } from '@/context/AppContext';
import { Header } from '@/components/Header';
import { NetworkWatchdogBanner } from '@/components/NetworkWatchdogBanner';
import { FloodWaitBanner } from '@/components/FloodWaitBanner';
import { AuthModal } from '@/components/AuthModal';
import { DeviceAuthGateModal } from '@/components/DeviceAuthGateModal';
import { MultiTenantAuthModal } from '@/components/v3/MultiTenantAuthModal';
import { ToastContainer } from '@/components/ToastContainer';
import { Loader2, ShieldCheck, User } from 'lucide-react';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';

const ManagementDashboardView = React.lazy(() =>
  import('@/views/ManagementDashboardView').then((m) => ({ default: m.ManagementDashboardView }))
);
const CinemaView = React.lazy(() => import('@/views/CinemaView').then((m) => ({ default: m.CinemaView })));
const SettingsView = React.lazy(() => import('@/views/SettingsView').then((m) => ({ default: m.SettingsView })));

const ViewLoadingFallback: React.FC = () => (
  <div
    role="status"
    aria-live="polite"
    className="w-full min-h-[50vh] flex flex-col items-center justify-center gap-3 p-8"
  >
    <div className="relative flex items-center justify-center">
      <div className="w-10 h-10 rounded-2xl bg-primary/10 border border-primary/20 animate-pulse flex items-center justify-center">
        <Loader2 className="w-5 h-5 text-primary animate-spin" />
      </div>
    </div>
    <p className="text-xs font-mono text-muted-foreground animate-pulse">Loading view...</p>
  </div>
);

const MainLayout: React.FC = () => {
  const {
    activeTab,
    theme,
    v3User,
    isMultiTenantModalOpen,
    setMultiTenantModalOpen,
    refreshV3User,
    isAuthLoading,
  } = useApp();
  const [isDeviceGateOpen, setIsDeviceGateOpen] = useState(false);

  useEffect(() => {
    // 1. Check legacy device auth only if user is authenticated with v3
    if (!v3User) return;

    const checkDeviceAuth = async () => {
      try {
        const storedToken = api.getStoredToken();
        if (storedToken) {
          try {
            const loginRes = await api.deviceLogin(storedToken);
            if (loginRes.success) {
              socketService.reconnectWithToken(storedToken);
              setIsDeviceGateOpen(false);
              return;
            }
          } catch {
            api.setStoredToken('');
          }
        }

        const status = await api.getDeviceAuthStatus();
        if (status.auth_required && !status.authenticated) {
          setIsDeviceGateOpen(true);
        }
      } catch {
        // If 401 or network error, let listener handle it
      }
    };
    checkDeviceAuth();

    // 2. Listen for 401s across the application
    const unsubAuth = api.onAuthRequired(() => {
      setIsDeviceGateOpen(true);
    });

    return () => {
      unsubAuth();
    };
  }, [v3User]);

  if (isAuthLoading) {
    return (
      <div className="h-screen w-screen bg-background text-foreground flex flex-col items-center justify-center gap-3">
        <Loader2 className="w-8 h-8 text-primary animate-spin" />
        <p className="text-xs font-mono text-muted-foreground animate-pulse">Initializing TG Power Suite...</p>
      </div>
    );
  }

  return (
    <div className="h-screen max-h-screen bg-background text-foreground flex flex-col antialiased selection:bg-primary/20 selection:text-primary overflow-hidden">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-2 focus:bg-primary focus:text-primary-foreground focus:rounded-xl focus:shadow-xl focus:outline-none focus:ring-2 focus:ring-ring font-medium text-xs transition-all"
      >
        Skip to main content
      </a>

      <div
        aria-hidden="true"
        className={`fixed inset-0 pointer-events-none transition-opacity duration-700 ${
          theme === 'cyber-emerald'
            ? 'cyber-grid opacity-100'
            : theme === 'crimson-neon'
            ? 'crimson-grid opacity-100'
            : 'bg-[radial-gradient(ellipse_80%_80%_at_50%_-20%,rgba(56,189,248,0.06),rgba(255,255,255,0))] dark:bg-[radial-gradient(ellipse_80%_80%_at_50%_-20%,rgba(56,189,248,0.08),rgba(0,0,0,0))]'
        }`}
      />

      <Header />
      <NetworkWatchdogBanner />
      <FloodWaitBanner />

      <div className="flex-1 min-h-0 flex w-full relative overflow-hidden">
        <main
          id="main-content"
          tabIndex={-1}
          aria-label="Main Workspace Content"
          className="flex-1 min-h-0 min-w-0 w-full relative focus:outline-none flex flex-col overflow-hidden"
        >
          {!v3User ? (
            <div className="flex-1 min-h-0 flex flex-col items-center justify-center gap-4 text-center p-6 animate-in fade-in">
              <div className="w-14 h-14 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center text-primary shadow-lg shadow-primary/5">
                <ShieldCheck className="w-7 h-7" />
              </div>
              <div className="space-y-1.5 max-w-sm">
                <h2 className="text-xl font-bold tracking-tight">Identity Authentication Required</h2>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Sign in or register an account to access private Telegram channels, encrypted turbo cloud media, and device controls.
                </p>
              </div>
              <button
                onClick={() => setMultiTenantModalOpen(true)}
                className="mt-2 inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-primary text-primary-foreground text-xs font-semibold shadow-md hover:opacity-95 transition-all cursor-pointer"
              >
                <User className="w-4 h-4" />
                <span>Sign In / Register</span>
              </button>
            </div>
          ) : (
            <Suspense fallback={<ViewLoadingFallback />}>
              {activeTab === 'dashboard' && (
                <div className="flex-1 min-h-0 h-full flex flex-col overflow-hidden">
                  <ManagementDashboardView />
                </div>
              )}
              {activeTab === 'uploader' && (
                <div className="flex-1 min-h-0 h-full flex flex-col overflow-hidden">
                  <ManagementDashboardView initialSubTab="uploader" />
                </div>
              )}
              {activeTab === 'sniffer' && (
                <div className="flex-1 min-h-0 h-full flex flex-col overflow-hidden">
                  <ManagementDashboardView initialSubTab="sniffer" />
                </div>
              )}
              {activeTab === 'history' && (
                <div className="flex-1 min-h-0 h-full flex flex-col overflow-hidden">
                  <ManagementDashboardView initialSubTab="history" />
                </div>
              )}
              {activeTab === 'cinema' && <CinemaView />}
              {activeTab === 'settings' && (
                <div className="flex-1 min-h-0 overflow-y-auto">
                  <SettingsView />
                </div>
              )}
            </Suspense>
          )}
        </main>
      </div>

      <MultiTenantAuthModal
        isOpen={!v3User || isMultiTenantModalOpen}
        canClose={Boolean(v3User)}
        onClose={() => setMultiTenantModalOpen(false)}
        onSuccess={async () => {
          await refreshV3User();
        }}
      />
      <AuthModal />
      <DeviceAuthGateModal
        isOpen={isDeviceGateOpen}
        onSuccess={() => setIsDeviceGateOpen(false)}
      />
      <ToastContainer />
    </div>
  );
};

export default function App() {
  return (
    <AppProvider>
      <MainLayout />
    </AppProvider>
  );
}
