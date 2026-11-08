import React, { createContext, useCallback, useMemo, useContext, useEffect, useRef, useState } from 'react';
import type { AuthStatus, ChatItem, TabType, TelemetryStats, ThemeType, UploadTask } from '@/types';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';

export interface ToastMessage {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  title?: string;
  message: string;
  count?: number;
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
  telemetry?: TelemetryStats | null;
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
    if (saved && ['dashboard', 'uploader', 'sniffer', 'cinema', 'history', 'settings'].includes(saved)) {
      return saved;
    }
    return 'cinema';
  });

  const setActiveTab = useCallback((tab: TabType) => {
    setActiveTabState(tab);
    try {
      localStorage.setItem('tg_active_tab', tab);
    } catch {}
  }, []);

  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [isAuthModalOpen, setAuthModalOpen] = useState(false);
  const [selectedChat, setSelectedChatState] = useState<ChatItem>(() => {
    try {
      const saved = localStorage.getItem('tg_selected_chat');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed && typeof parsed === 'object' && parsed.id) {
          return parsed;
        }
      }
    } catch {}
    return DEFAULT_CHAT;
  });
  const [isSocketConnected, setIsSocketConnected] = useState(false);
  const [floodWait, setFloodWait] = useState<{ active: boolean; seconds: number; message?: string } | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [uploadQueue, setUploadQueue] = useState<UploadTask[]>([]);
  const [theme, setThemeState] = useState<ThemeType>(() => {
    const saved = localStorage.getItem('tg_theme');
    if (saved === 'light' || saved === 'dark' || saved === 'cyber-emerald' || saved === 'crimson-neon') return saved as ThemeType;
    return 'cyber-emerald';
  });

  const toastHistoryRef = useRef<Map<string, { time: number; id: string; count: number; baseMessage: string }>>(new Map());

  const setTheme = useCallback((nextTheme: ThemeType) => {
    setThemeState(nextTheme);
    try {
      localStorage.setItem('tg_theme', nextTheme);
    } catch {}
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback((toast: Omit<ToastMessage, 'id'>) => {
    const now = Date.now();
    const key = `${toast.type}:${toast.title || ''}:${toast.message}`;
    const existing = toastHistoryRef.current.get(key);

    if (existing && now - existing.time < 2500) {
      existing.count += 1;
      existing.time = now;
      setToasts((prev) =>
        prev.map((t) =>
          t.id === existing.id
            ? {
                ...t,
                count: existing.count,
                message: `${existing.baseMessage} (${existing.count}x)`,
              }
            : t
        )
      );
      return;
    }

    if (toastHistoryRef.current.size > 40) {
      for (const [k, v] of toastHistoryRef.current.entries()) {
        if (now - v.time > 10000) {
          toastHistoryRef.current.delete(k);
        }
      }
    }

    const id = Math.random().toString(36).substring(2, 9);
    toastHistoryRef.current.set(key, {
      time: now,
      id,
      count: 1,
      baseMessage: toast.message,
    });

    setToasts((prev) => {
      const trimmed = prev.length >= 2 ? prev.slice(prev.length - 1) : prev;
      return [...trimmed, { ...toast, id, count: 1 }];
    });

    setTimeout(() => {
      removeToast(id);
    }, 4500);
  }, [removeToast]);

  const setSelectedChat = (chat: ChatItem) => {
    setSelectedChatState(chat);
    try {
      localStorage.setItem('tg_selected_chat', JSON.stringify(chat));
    } catch {}
  };

  const toggleTheme = useCallback(() => {
    const nextTheme: ThemeType =
      theme === 'cyber-emerald'
        ? 'crimson-neon'
        : theme === 'crimson-neon'
        ? 'dark'
        : theme === 'dark'
        ? 'light'
        : 'cyber-emerald';
    setTheme(nextTheme);
  }, [theme, setTheme]);

  useMemo(() => {
    const root = document.documentElement;
    root.classList.remove('cyber-emerald', 'crimson-neon');
    if (theme === 'cyber-emerald') {
      root.classList.add('dark', 'cyber-emerald');
    } else if (theme === 'crimson-neon') {
      root.classList.add('dark', 'crimson-neon');
    } else if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  }, [theme]);



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
        telemetry: null,
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
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useTelemetry = () => {
  const [telemetry, setTelemetry] = useState<TelemetryStats | null>(null);
  useEffect(() => {
    const unsub = socketService.onTelemetry(setTelemetry);
    return () => {
      unsub();
    };
  }, []);
  return telemetry;
};

export const useApp = () => {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
};
