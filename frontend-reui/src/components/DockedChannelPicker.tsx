import React, { useEffect, useState } from 'react';
import type { ChatItem } from '@/types';
import { api } from '@/services/api';
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
  const [filter, setFilter] = useState<'all' | 'channel' | 'group' | 'user' | 'bot' | 'saved'>('all');

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
  }, []);

  const filteredChats = chats.filter((c) => {
    const matchesSearch =
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      (c.username && c.username.toLowerCase().includes(search.toLowerCase()));

    if (!matchesSearch) return false;

    if (filter === 'saved') return c.type === 'saved_messages';
    if (filter === 'channel') return c.type === 'channel';
    if (filter === 'group') return c.type === 'group' || c.type === 'supergroup';
    if (filter === 'user') return c.type === 'user';
    if (filter === 'bot') return c.type === 'bot';

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
        return <User className="w-3.5 h-3.5 text-amber-400" />;
      default:
        return <MessageSquare className="w-3.5 h-3.5 text-muted-foreground" />;
    }
  };

  return (
    <aside className="w-full lg:w-72 xl:w-80 shrink-0 lg:sticky lg:top-20 space-y-3 animate-in slide-in-from-left-4 duration-300">
      <div className="p-3.5 rounded-2xl border border-border/80 bg-card/90 backdrop-blur-xl shadow-lg space-y-3">
        <div className="flex items-center justify-between pb-2 border-b border-border/50">
          <div className="flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-primary" />
            <h3 className="text-xs font-semibold text-foreground">Channels & Chats</h3>
            <Badge variant="outline" className="text-[10px] h-4 px-1.5 border-primary/30 text-primary bg-primary/10">
              {chats.length}
            </Badge>
          </div>
          <div className="flex items-center gap-1">
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
                className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors cursor-pointer"
                title="Collapse channels sidebar"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search channels..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 text-xs bg-background/60"
          />
        </div>

        <div className="flex items-center gap-1 overflow-x-auto pb-1 scrollbar-none">
          {(
            [
              { id: 'all', label: 'All' },
              { id: 'channel', label: 'Channels' },
              { id: 'saved', label: 'Saved' },
              { id: 'group', label: 'Groups' },
              { id: 'user', label: 'Contacts' },
              { id: 'bot', label: 'Bots' },
            ] as const
          ).map((tab) => (
            <button
              key={tab.id}
              onClick={() => setFilter(tab.id)}
              className={`text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap transition-colors cursor-pointer ${
                filter === tab.id
                  ? 'bg-primary text-primary-foreground font-medium'
                  : 'bg-muted/50 text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="space-y-1 max-h-[calc(100vh-16rem)] overflow-y-auto pr-1 scrollbar-thin">
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
              const isSelected = String(chat.id) === String(selectedChatId);
              return (
                <button
                  key={chat.id}
                  onClick={() => onSelect(chat)}
                  className={`w-full text-left p-2 rounded-xl flex items-center justify-between gap-2.5 transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-primary/15 text-foreground border-l-2 border-primary shadow-xs'
                      : 'hover:bg-accent/50 text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="relative w-7 h-7 rounded-full bg-accent flex items-center justify-center shrink-0 overflow-hidden">
                      {chat.photo_url ? (
                        <img
                          src={chat.photo_url}
                          alt=""
                          className="w-full h-full object-cover"
                          onError={(e) => {
                            (e.currentTarget as HTMLElement).style.display = 'none';
                          }}
                        />
                      ) : (
                        getChatIcon(chat.type)
                      )}
                    </div>
                    <div className="min-w-0">
                      <div className="text-xs font-medium truncate flex items-center gap-1">
                        <span className={isSelected ? 'text-primary font-semibold' : 'text-foreground'}>
                          {chat.name}
                        </span>
                        {chat.pinned && <Pin className="w-2.5 h-2.5 text-muted-foreground rotate-45 shrink-0" />}
                        {chat.type === 'bot' && (
                          <Badge variant="outline" className="text-[8px] px-1 py-0 h-3.5 border-cyan-500/40 text-cyan-400 bg-cyan-500/10 font-semibold tracking-wide">
                            BOT
                          </Badge>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground truncate">
                        {chat.username ? `@${chat.username}` : chat.type.replace('_', ' ')}
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
  );
};
