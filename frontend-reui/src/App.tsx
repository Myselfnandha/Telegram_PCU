import React, { Suspense } from 'react';
import { AppProvider, useApp } from '@/context/AppContext';
import { Header } from '@/components/Header';
import { FloodWaitBanner } from '@/components/FloodWaitBanner';
import { AuthModal } from '@/components/AuthModal';
import { ToastContainer } from '@/components/ToastContainer';
import { MobileTelegramDock } from '@/components/MobileTelegramDock';
import { VlcRemoteDock } from '@/components/VlcRemoteDock';
import { Loader2 } from 'lucide-react';

// Code-split top-level views via dynamic import() for instant initial paint
const UploaderView = React.lazy(() => import('@/views/UploaderView').then((m) => ({ default: m.UploaderView })));
const SnifferView = React.lazy(() => import('@/views/SnifferView').then((m) => ({ default: m.SnifferView })));
const CinemaView = React.lazy(() => import('@/views/CinemaView').then((m) => ({ default: m.CinemaView })));
const HistoryView = React.lazy(() => import('@/views/HistoryView').then((m) => ({ default: m.HistoryView })));
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
    <div className="min-h-screen bg-background text-foreground flex flex-col antialiased selection:bg-primary/20 selection:text-primary">
      {/* Accessible Skip Link for Keyboard & Screen Reader Users */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-2 focus:bg-primary focus:text-primary-foreground focus:rounded-xl focus:shadow-xl focus:outline-none focus:ring-2 focus:ring-ring font-medium text-xs transition-all"
      >
        Skip to main content
      </a>

      {/* Dynamic ambient background glow & cyber grid */}
      <div
        aria-hidden="true"
        className={`fixed inset-0 pointer-events-none transition-opacity duration-700 ${
          theme === 'cyber-emerald'
            ? 'cyber-grid opacity-100'
            : 'bg-[radial-gradient(ellipse_80%_80%_at_50%_-20%,rgba(56,189,248,0.06),rgba(255,255,255,0))] dark:bg-[radial-gradient(ellipse_80%_80%_at_50%_-20%,rgba(56,189,248,0.08),rgba(0,0,0,0))]'
        }`}
      />

      <Header />
      <FloodWaitBanner />

      <div className="flex-1 flex w-full relative">
        <MobileTelegramDock />
        <main
          id="main-content"
          tabIndex={-1}
          aria-label="Main Workspace Content"
          className="flex-1 min-w-0 w-full pb-16 relative focus:outline-none"
        >
          <Suspense fallback={<ViewLoadingFallback />}>
            {activeTab === 'uploader' && <UploaderView />}
            {activeTab === 'sniffer' && <SnifferView />}
            {activeTab === 'cinema' && <CinemaView />}
            {activeTab === 'history' && <HistoryView />}
            {activeTab === 'settings' && <SettingsView />}
          </Suspense>
        </main>
      </div>

      <AuthModal />
      <ToastContainer />
      <VlcRemoteDock />
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
