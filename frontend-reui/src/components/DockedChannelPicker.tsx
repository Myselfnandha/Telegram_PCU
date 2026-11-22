import React, { useEffect, useState, useRef } from 'react';
import type { ChatItem } from '@/types';
import { api } from '@/services/api';
import { socketService } from '@/services/socket';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Bookmark,
  Bot,
  Check,
  ChevronLeft,
  Hash,
  MessageSquare,
  Pin,
  RefreshCw,
  Search,
  User,
  Users,
} from 'lucide-react';

interface DockedChannelPickerProps {
  selectedChatId?: string | number;
  onSelect: (chat: ChatItem) => void;
  onClose?: () => void;
}

export const DockedChannelPicker: React.FC<DockedChannelPickerProps> = ({
  selectedChatId,
  onSelect,
  onClose,
}) => {
  const [chats, setChats] = useState<ChatItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'channel' | 'user' | 'group' | 'bot' | 'saved' | 'contact'>('all');
  const asideRef = useRef<HTMLElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const triggerElementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    // Only engage focus trapping/restoration when rendered as mobile/tablet overlay (< 1024px)
    const isMobileOverlay = typeof window !== 'undefined' && window.innerWidth < 1024 && Boolean(onClose);
    if (!isMobileOverlay) return;

    triggerElementRef.current = (document.activeElement && document.activeElement !== document.body)
      ? (document.activeElement as HTMLElement)
      : document.querySelector<HTMLElement>('button[aria-label="Open Channels & Chats Sidebar"]');

    // Focus initial element inside drawer
    const timer = setTimeout(() => {
      if (searchInputRef.current) {
        searchInputRef.current.focus();
      } else if (asideRef.current) {
        const firstFocusable = asideRef.current.querySelector<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        firstFocusable?.focus();
      }
    }, 50);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose?.();
        return;
      }

      if (e.key === 'Tab' && asideRef.current) {
        const focusable = Array.from(asideRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter((el) => el.offsetParent !== null || el.getClientRects().length > 0);
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (!asideRef.current.contains(document.activeElement)) {
          e.preventDefault();
          first.focus();
          return;
        }

        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('keydown', handleKeyDown);
      setTimeout(() => {
        const btn = (triggerElementRef.current && triggerElementRef.current.isConnected)
          ? triggerElementRef.current
          : document.querySelector<HTMLElement>('button[aria-label="Open Channels & Chats Sidebar"]');
        btn?.focus();
      }, 80);
    };
  }, [onClose]);

  const loadChats = async (forceRefresh = false) => {
    setLoading(true);
    try {
      const data = await api.getChats(forceRefresh);
      setChats(data);
    } catch (e) {
      console.debug('Failed to fetch chats:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadChats(false);

    const unsubDialogs = socketService.onDialogUpdate((data) => {
      if (data && Array.isArray(data.dialogs) && data.dialogs.length > 0) {
        setChats(data.dialogs);
      } else {
        loadChats(true);
      }
    });

    return () => {
      unsubDialogs();
    };
  }, []);

  const filteredChats = chats.filter((c) => {
    const matchesSearch =
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      (c.username && c.username.toLowerCase().includes(search.toLowerCase()));

    if (!matchesSearch) return false;

    if (filter === 'all') {
      if (search.trim()) return true;
      return c.type !== 'contact' && !c.is_contact;
    }
    if (filter === 'saved') return c.type === 'saved_messages';
    if (filter === 'channel') return c.type === 'channel' || c.type === 'supergroup';
    if (filter === 'user') return c.type === 'user';
    if (filter === 'group') return c.type === 'group' || c.type === 'supergroup';
    if (filter === 'bot') return c.type === 'bot';
    if (filter === 'contact') return c.type === 'contact' || Boolean(c.is_contact);

    return true;
  });

  const getChatIcon = (type: ChatItem['type']) => {
    switch (type) {
      case 'saved_messages':
        return <Bookmark className="w-3.5 h-3.5 text-sky-400" />;
      case 'channel':
        return <Hash className="w-3.5 h-3.5 text-emerald-400" />;
      case 'supergroup':
      case 'group':
        return <Users className="w-3.5 h-3.5 text-violet-400" />;
      case 'bot':
        return <Bot className="w-3.5 h-3.5 text-cyan-400" />;
      case 'user':
        return <MessageSquare className="w-3.5 h-3.5 text-amber-400" />;
      case 'contact':
        return <User className="w-3.5 h-3.5 text-blue-400" />;
      default:
        return <MessageSquare className="w-3.5 h-3.5 text-muted-foreground" />;
    }
  };

  return (
    <>
      {/* Mobile/Tablet Backdrop */}
      {onClose && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-xs z-40 lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        ref={asideRef}
        role={typeof window !== 'undefined' && window.innerWidth < 1024 && onClose ? 'dialog' : undefined}
        aria-modal={typeof window !== 'undefined' && window.innerWidth < 1024 && onClose ? 'true' : undefined}
        aria-label="Channels & Chats Drawer"
        className="fixed inset-y-0 left-0 z-50 w-80 max-w-[85vw] p-2 bg-background/95 backdrop-blur-xl shadow-2xl lg:static lg:z-auto lg:w-[18%] lg:min-w-[190px] lg:max-w-[260px] w-[18%] min-w-[190px] max-w-[260px] lg:p-0 lg:bg-transparent lg:shadow-none shrink-0 h-full flex flex-col min-h-0 animate-in slide-in-from-left-3 duration-200"
      >
        <div className="h-full flex flex-col min-h-0 p-2 rounded-xl border border-border/70 bg-card/85 backdrop-blur-xl shadow-xs space-y-1.5">
          {/* Header Bar */}
          <div className="flex items-center justify-between pb-1.5 border-b border-border/50 shrink-0 px-0.5">
            <div className="flex items-center gap-1.5 min-w-0">
              <MessageSquare className="w-3.5 h-3.5 text-primary shrink-0" />
              <h3 className="text-xs font-semibold text-foreground truncate">Channels & Chats</h3>
              <Badge variant="outline" className="text-[10px] h-4 px-1.5 border-primary/30 text-primary bg-primary/10 font-mono shrink-0">
                {filteredChats.length}
              </Badge>
            </div>
            <div className="flex items-center gap-0.5 shrink-0">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => loadChats(true)}
                disabled={loading}
                className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground cursor-pointer"
                title="Refresh dialogs"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
              {onClose && (
                <button
                  onClick={onClose}
                  className="p-1.5 min-w-[36px] min-h-[36px] sm:min-w-[28px] sm:min-h-[28px] flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors cursor-pointer"
                  title="Collapse channels sidebar"
                  aria-label="Close channels sidebar"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          <div className="relative shrink-0">
            <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={searchInputRef}
              placeholder="Search chats & channels..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-7 h-7 text-xs bg-background/60"
            />
          </div>

          <div className="flex items-center gap-1 overflow-x-auto pb-0.5 scrollbar-none shrink-0">
            {(
              [
                { id: 'all', label: 'All' },
                { id: 'channel', label: 'Channels' },
                { id: 'user', label: 'Chats' },
                { id: 'group', label: 'Groups' },
                { id: 'bot', label: 'Bots' },
                { id: 'saved', label: 'Saved' },
                { id: 'contact', label: 'Contacts' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                onClick={() => setFilter(tab.id)}
                className={`text-xs px-3 py-1.5 min-h-[36px] rounded-full whitespace-nowrap transition-colors cursor-pointer flex items-center justify-center ${
                  filter === tab.id
                    ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                    : 'bg-muted/50 text-muted-foreground hover:text-foreground hover:bg-muted font-medium'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto space-y-1 pr-1 scrollbar-thin">
            {loading && chats.length === 0 ? (
              <div className="p-8 text-center text-xs text-muted-foreground">
                <RefreshCw className="w-4 h-4 mx-auto mb-2 animate-spin text-primary" />
                Loading channels...
              </div>
            ) : filteredChats.length === 0 ? (
              <div className="p-6 text-center text-xs text-muted-foreground">
                No matching channels found.
              </div>
            ) : (
              filteredChats.map((chat) => {
                const isSelected =
                  String(chat.id) === String(selectedChatId) ||
                  ((selectedChatId === 'me' || selectedChatId === 'self') && chat.type === 'saved_messages') ||
                  String(chat.id).replace(/^-100/, '').replace(/^-/, '') === String(selectedChatId).replace(/^-100/, '').replace(/^-/, '');
                return (
                  <button
                    key={chat.id}
                    onClick={() => onSelect(chat)}
                    className={`w-full text-left p-1.5 rounded-lg flex items-center justify-between gap-2 transition-all cursor-pointer ${
                      isSelected
                        ? 'bg-primary/15 text-foreground border-l-2 border-primary shadow-2xs'
                        : 'hover:bg-accent/50 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <div className="relative w-7 h-7 rounded-full bg-accent flex items-center justify-center shrink-0 overflow-hidden">
                        {getChatIcon(chat.type)}
                        {chat.photo_url && (
                          <img
                            src={chat.photo_url}
                            alt=""
                            className="absolute inset-0 w-full h-full object-cover"
                            onError={(e) => {
                              (e.currentTarget as HTMLElement).style.display = 'none';
                            }}
                          />
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="text-xs font-medium truncate flex items-center gap-1">
                          <span className={isSelected ? 'text-primary font-semibold' : 'text-foreground'}>
                            {chat.name}
                          </span>
                          {chat.pinned && <Pin className="w-2.5 h-2.5 text-muted-foreground rotate-45 shrink-0" />}
                          {chat.type === 'bot' && (
                            <Badge variant="outline" className="text-[10px] px-1 py-0 h-3.5 border-cyan-500/40 text-cyan-400 bg-cyan-500/10 font-semibold tracking-wide">
                              BOT
                            </Badge>
                          )}
                        </div>
                        <div className="text-[11px] text-muted-foreground truncate">
                          {chat.username ? `@${chat.username}` : chat.type === 'user' ? 'Direct Chat' : chat.type === 'contact' ? 'Contact' : chat.type.replace('_', ' ')}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      {chat.unread_count && chat.unread_count > 0 ? (
                        <Badge variant="secondary" className="text-[9px] h-4 px-1">
                          {chat.unread_count}
                        </Badge>
                      ) : null}
                      {isSelected && <Check className="w-3.5 h-3.5 text-primary" />}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      </aside>
    </>
  );
};
