import React, { createContext, useContext, useEffect, useState } from 'react';
import type { AuthStatus, ChatItem, TabType, TelemetryStats, ThemeType, UploadTask } from '@/types';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';

export interface ToastMessage {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  title?: string;
  message: string;
}

interface AppContextValue {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  authStatus: AuthStatus | null;
  refreshAuth: () => Promise<void>;
  isAuthModalOpen: boolean;
  setAuthModalOpen: (open: boolean) => void;
  selectedChat: ChatItem;
  setSelectedChat: (chat: ChatItem) => void;
  telemetry: TelemetryStats | null;
  isSocketConnected: boolean;
  floodWait: { active: boolean; seconds: number; message?: string } | null;
  theme: ThemeType;
  setTheme: (theme: ThemeType) => void;
  toggleTheme: () => void;
  toasts: ToastMessage[];
  addToast: (toast: Omit<ToastMessage, 'id'>) => void;
  removeToast: (id: string) => void;
  uploadQueue: UploadTask[];
  setUploadQueue: React.Dispatch<React.SetStateAction<UploadTask[]>>;
  isMobileDockOpen: boolean;
  setMobileDockOpen: (open: boolean) => void;
  toggleMobileDock: () => void;
  mobileDockWidth: number;
  setMobileDockWidth: (w: number) => void;
}

const DEFAULT_CHAT: ChatItem = {
  id: 'me',
  name: 'Saved Messages (Personal Cloud)',
  type: 'saved_messages',
};

const AppContext = createContext<AppContextValue | undefined>(undefined);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activeTab, setActiveTabState] = useState<TabType>(() => {
    const defaultSet = localStorage.getItem('tg_default_cinema_migrated');
    if (!defaultSet) {
      localStorage.setItem('tg_default_cinema_migrated', 'true');
      localStorage.setItem('tg_active_tab', 'cinema');
      return 'cinema';
    }
    const saved = localStorage.getItem('tg_active_tab') as TabType;
    if (saved && ['uploader', 'sniffer', 'cinema', 'history', 'settings'].includes(saved)) {
      return saved;
    }
    return 'cinema';
  });

  const setActiveTab = (tab: TabType) => {
    setActiveTabState(tab);
    try {
      localStorage.setItem('tg_active_tab', tab);
    } catch {}
  };
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [isAuthModalOpen, setAuthModalOpen] = useState(false);
  const [selectedChat, setSelectedChatState] = useState<ChatItem>(DEFAULT_CHAT);
  const [telemetry, setTelemetry] = useState<TelemetryStats | null>(null);
  const [isSocketConnected, setIsSocketConnected] = useState(false);
  const [floodWait, setFloodWait] = useState<{ active: boolean; seconds: number; message?: string } | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [uploadQueue, setUploadQueue] = useState<UploadTask[]>([]);
  const [theme, setThemeState] = useState<ThemeType>(() => {
    const saved = localStorage.getItem('tg_theme');
    if (saved === 'light' || saved === 'dark' || saved === 'cyber-emerald') return saved as ThemeType;
    return 'cyber-emerald'; // Default premier theme: Midnight Emerald / Cyber Core
  });

  const setTheme = (nextTheme: ThemeType) => {
    setThemeState(nextTheme);
    try {
      localStorage.setItem('tg_theme', nextTheme);
    } catch {}
  };

  const addToast = (toast: Omit<ToastMessage, 'id'>) => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { ...toast, id }]);
    setTimeout(() => {
      removeToast(id);
    }, 4500);
  };

  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  const setSelectedChat = (chat: ChatItem) => {
    setSelectedChatState(chat);
    try {
      localStorage.setItem('tg_selected_chat', JSON.stringify(chat));
    } catch {}
  };

  const [isMobileDockOpen, setIsMobileDockOpenState] = useState<boolean>(() => {
    try {
      return localStorage.getItem('tg_dock_open') === 'true';
    } catch {
      return false;
    }
  });

  const [mobileDockWidth, setMobileDockWidthState] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem('tg_dock_width'));
      if (saved >= 320 && saved <= 520) return saved;
      return 380;
    } catch {
      return 380;
    }
  });

  const setMobileDockOpen = (open: boolean) => {
    setIsMobileDockOpenState(open);
    try {
      localStorage.setItem('tg_dock_open', String(open));
    } catch {}
  };

  const toggleMobileDock = () => {
    setIsMobileDockOpenState((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('tg_dock_open', String(next));
      } catch {}
      return next;
    });
  };

  const setMobileDockWidth = (w: number) => {
    const clamped = Math.max(320, Math.min(520, w));
    setMobileDockWidthState(clamped);
    try {
      localStorage.setItem('tg_dock_width', String(clamped));
    } catch {}
  };

  const toggleTheme = () => {
    // Cycle: cyber-emerald -> dark -> light -> cyber-emerald
    const nextTheme: ThemeType =
      theme === 'cyber-emerald' ? 'dark' : theme === 'dark' ? 'light' : 'cyber-emerald';
    setTheme(nextTheme);
  };

  // Synchronize HTML dark and cyber-emerald classes
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'cyber-emerald') {
      root.classList.add('dark', 'cyber-emerald');
    } else if (theme === 'dark') {
      root.classList.add('dark');
      root.classList.remove('cyber-emerald');
    } else {
      root.classList.remove('dark', 'cyber-emerald');
    }
  }, [theme]);

  // Load saved selected chat
  useEffect(() => {
    try {
      const saved = localStorage.getItem('tg_selected_chat');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed && typeof parsed === 'object' && parsed.id) {
          setSelectedChatState(parsed);
        }
      }
    } catch {}
  }, []);

  const refreshAuth = async () => {
    try {
      const status = await api.getAuthStatus();
      setAuthStatus(status);
    } catch (e) {
      console.debug('Auth check failed:', e);
      setAuthStatus({ authenticated: false });
    }
  };

  // Socket & initial data load
  useEffect(() => {
    socketService.init();

    const unsubConn = socketService.onConnectionChange(setIsSocketConnected);
    const unsubTelemetry = socketService.onTelemetry(setTelemetry);
    const unsubFloodWait = socketService.onFloodWait((fw) => {
      setFloodWait(fw.active ? fw : null);
    });

    // Listen for queue updates from socket
    const unsubQueue = socketService.onQueueSnapshot((tasks) => {
      if (Array.isArray(tasks) && tasks.length > 0) {
        setUploadQueue((prevQueue) => {
          // Merge socket tasks with existing local queue items
          const taskMap = new Map(prevQueue.map((t) => [t.id, t]));
          tasks.forEach((st: any) => {
            const existing = taskMap.get(st.task_id || st.id);
            if (existing) {
              existing.status = st.status || existing.status;
              existing.progress = st.progress !== undefined ? st.progress : existing.progress;
              existing.speed = st.speed || existing.speed;
              existing.eta = st.eta || existing.eta;
              existing.currentPart = st.current_part || existing.currentPart;
              existing.totalParts = st.total_parts || existing.totalParts;
            }
          });
          return Array.from(taskMap.values());
        });
      }
    });

    const unsubProgress = socketService.onUploadProgress((data) => {
      if (!data || !data.task_id) return;
      setUploadQueue((prevQueue) =>
        prevQueue.map((t) => {
          if (t.id === data.task_id) {
            return {
              ...t,
              progress: data.progress ?? t.progress,
              uploadedBytes: data.uploaded_bytes ?? t.uploadedBytes,
              fileSize: data.total_bytes ?? t.fileSize,
              speed: data.speed ?? t.speed,
              eta: data.eta ?? t.eta,
              currentPart: data.current_part ?? t.currentPart,
              totalParts: data.total_parts ?? t.totalParts,
              status: data.status ?? 'uploading',
            };
          }
          return t;
        })
      );
    });

    refreshAuth();

    return () => {
      unsubConn();
      unsubTelemetry();
      unsubFloodWait();
      unsubQueue();
      unsubProgress();
    };
  }, []);

  return (
    <AppContext.Provider
      value={{
        activeTab,
        setActiveTab,
        authStatus,
        refreshAuth,
        isAuthModalOpen,
        setAuthModalOpen,
        selectedChat,
        setSelectedChat,
        telemetry,
        isSocketConnected,
        floodWait,
        theme,
        setTheme,
        toggleTheme,
        toasts,
        addToast,
        removeToast,
        uploadQueue,
        setUploadQueue,
        isMobileDockOpen,
        setMobileDockOpen,
        toggleMobileDock,
        mobileDockWidth,
        setMobileDockWidth,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useApp = () => {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
};
