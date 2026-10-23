import React, { useEffect, useState } from 'react';
import type { ChatItem } from '@/types';
import { api } from '@/services/api';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Bookmark,
  Bot,
  Check,
  Hash,
  MessageSquare,
  Pin,
  RefreshCw,
  Search,
  User,
  Users,
} from 'lucide-react';

interface ChatPickerModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (chat: ChatItem) => void;
  selectedChatId?: string | number;
}

export const ChatPickerModal: React.FC<ChatPickerModalProps> = ({
  open,
  onOpenChange,
  onSelect,
  selectedChatId,
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
    if (open) {
      loadChats(false);
    }
  }, [open]);

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
        return <Bookmark className="w-4 h-4 text-sky-400" />;
      case 'channel':
        return <Hash className="w-4 h-4 text-emerald-400" />;
      case 'supergroup':
      case 'group':
        return <Users className="w-4 h-4 text-violet-400" />;
      case 'bot':
        return <Bot className="w-4 h-4 text-cyan-400" />;
      case 'user':
        return <User className="w-4 h-4 text-amber-400" />;
      default:
        return <MessageSquare className="w-4 h-4 text-muted-foreground" />;
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg bg-card/95 backdrop-blur-xl border-border p-0 gap-0 overflow-hidden shadow-2xl">
        <DialogHeader className="p-4 border-b border-border">
          <div className="flex items-center justify-between">
            <DialogTitle className="text-base font-semibold flex items-center gap-2">
              <MessageSquare className="w-4 h-4 text-primary" />
              <span>Select Destination Chat</span>
            </DialogTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => loadChats(true)}
              disabled={loading}
              className="h-8 px-2 text-xs text-muted-foreground hover:text-foreground cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>

          <div className="relative mt-3">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search channels, groups, contacts, bots..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9 h-9 text-xs"
              autoFocus
            />
          </div>

          <div className="flex items-center gap-1.5 mt-2.5 overflow-x-auto pb-1 scrollbar-none">
            {(
              [
                { id: 'all', label: 'All' },
                { id: 'saved', label: 'Saved Messages' },
                { id: 'channel', label: 'Channels' },
                { id: 'group', label: 'Groups' },
                { id: 'user', label: 'Contacts' },
                { id: 'bot', label: 'Bots' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                onClick={() => setFilter(tab.id)}
                className={`text-xs px-2.5 py-1 rounded-full whitespace-nowrap transition-colors cursor-pointer ${
                  filter === tab.id
                    ? 'bg-primary text-primary-foreground font-medium'
                    : 'bg-muted/50 text-muted-foreground hover:text-foreground hover:bg-muted'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </DialogHeader>

        <div className="max-h-[360px] overflow-y-auto divide-y divide-border/40 p-1">
          {loading && chats.length === 0 ? (
            <div className="p-8 text-center text-xs text-muted-foreground">
              <RefreshCw className="w-5 h-5 mx-auto mb-2 animate-spin text-primary" />
              Syncing Telegram dialogs...
            </div>
          ) : filteredChats.length === 0 ? (
            <div className="p-8 text-center text-xs text-muted-foreground">
              No matching chats or channels found.
            </div>
          ) : (
            filteredChats.map((chat) => {
              const isSelected = String(chat.id) === String(selectedChatId);
              return (
                <button
                  key={chat.id}
                  onClick={() => {
                    onSelect(chat);
                    onOpenChange(false);
                  }}
                  className={`w-full text-left p-2.5 rounded-lg flex items-center justify-between gap-3 hover:bg-accent/50 transition-colors cursor-pointer ${
                    isSelected ? 'bg-primary/10 border-l-2 border-primary' : ''
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="relative w-8 h-8 rounded-full bg-accent flex items-center justify-center shrink-0 overflow-hidden">
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
                      <div className="text-xs font-semibold text-foreground truncate flex items-center gap-1.5">
                        <span>{chat.name}</span>
                        {chat.pinned && <Pin className="w-3 h-3 text-muted-foreground rotate-45" />}
                        {chat.type === 'bot' && (
                          <Badge variant="outline" className="text-[9px] px-1 py-0 h-4 border-cyan-500/40 text-cyan-400 bg-cyan-500/10 font-semibold tracking-wide">
                            BOT
                          </Badge>
                        )}
                      </div>
                      <div className="text-[11px] text-muted-foreground truncate">
                        {chat.username ? `@${chat.username}` : chat.type.replace('_', ' ')}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {chat.unread_count && chat.unread_count > 0 ? (
                      <Badge variant="secondary" className="text-[10px] h-5 px-1.5">
                        {chat.unread_count}
                      </Badge>
                    ) : null}
                    {isSelected && <Check className="w-4 h-4 text-primary" />}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
