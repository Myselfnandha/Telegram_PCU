import React, { Suspense } from 'react';
import { AppProvider, useApp } from '@/context/AppContext';
import { Header } from '@/components/Header';
import { NetworkWatchdogBanner } from '@/components/NetworkWatchdogBanner';
import { FloodWaitBanner } from '@/components/FloodWaitBanner';
import { AuthModal } from '@/components/AuthModal';
import { ToastContainer } from '@/components/ToastContainer';
import { Loader2 } from 'lucide-react';

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
  const { activeTab, theme } = useApp();

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
        </main>
      </div>

      <AuthModal />
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
