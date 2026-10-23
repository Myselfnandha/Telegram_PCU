import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useApp } from '@/context/AppContext';
import { api } from '@/services/api';
import type { ChatItem, ChatMessage } from '@/types';
import { formatBytes, formatDuration, cleanFileName } from '@/lib/formatters';
import { scrollRightSideToTop, scrollRightSide } from '@/lib/scrollUtils';
import {
  Search,
  ChevronLeft,
  X,
  Play,
  Tv,
  Send,
  RefreshCw,
  Copy,
  Check,
  CheckCheck,
  Pin,
  MessageSquare,
  Users,
  Radio,
  Bookmark,
  Share2,
  FolderPlus,
  ArrowDown,
  Maximize2,
  ZoomIn,
  ZoomOut,
  Download,
  ExternalLink,
  FileText,
  FileArchive,
  Music,
  Image as ImageIcon
} from 'lucide-react';

interface LightboxMedia {
  type: 'photo' | 'video';
  src: string;
  thumbUrl?: string;
  title: string;
  fileSize?: number | null;
  duration?: number | null;
  chatId: string | number;
  messageId: number;
}

export const MobileTelegramDock: React.FC = () => {
  const {
    isMobileDockOpen,
    setMobileDockOpen,
    mobileDockWidth,
    setMobileDockWidth,
    setSelectedChat,
    selectedChat: globalSelectedChat,
    addToast,
    setActiveTab,
    activeTab,
  } = useApp();

  // Dialogs & Navigation State
  const [chats, setChats] = useState<ChatItem[]>([]);
  const [loadingChats, setLoadingChats] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState<'all' | 'saved' | 'channel' | 'group'>('all');

  // Active Chat & Messages State
  const [activeChat, setActiveChat] = useState<ChatItem | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [inputText, setInputText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  // In-chat Search State
  const [isSearchInChatOpen, setSearchInChatOpen] = useState(false);
  const [chatSearchQuery, setChatSearchQuery] = useState('');

  // Scroll Down Button State
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const dockAsideRef = useRef<HTMLElement>(null);

  // Lightbox / Thumbnail Preview State
  const [lightboxMedia, setLightboxMedia] = useState<LightboxMedia | null>(null);
  const [zoomLevel, setZoomLevel] = useState<number>(1);

  // Drag Resizing
  const isDraggingRef = useRef(false);

  // Live status time
  const [currentTime, setCurrentTime] = useState('');
  useEffect(() => {
    const updateTime = () => {
      const d = new Date();
      setCurrentTime(d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }));
    };
    updateTime();
    const timer = setInterval(updateTime, 30000);
    return () => clearInterval(timer);
  }, []);

  // Keyboard shortcut Ctrl+B / Cmd+B to toggle dock
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        setMobileDockOpen(!isMobileDockOpen);
      } else if (e.key === 'Escape' && lightboxMedia) {
        setLightboxMedia(null);
        setZoomLevel(1);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isMobileDockOpen, setMobileDockOpen, lightboxMedia]);

  // Overscroll in chat handler: seamlessly forwards overscroll to scroll the right side files
  useEffect(() => {
    const dockEl = dockAsideRef.current;
    if (!dockEl) return;

    const findScrollableParent = (target: HTMLElement | null, boundary: HTMLElement): HTMLElement | null => {
      let curr = target;
      while (curr && curr !== boundary && curr !== document.body) {
        const style = window.getComputedStyle(curr);
        const overflowY = style.overflowY;
        if ((overflowY === 'auto' || overflowY === 'scroll') && curr.scrollHeight > curr.clientHeight) {
          return curr;
        }
        curr = curr.parentElement;
      }
      return null;
    };

    const handleWheel = (e: WheelEvent) => {
      // Don't intercept browser pinch-zoom
      if (e.ctrlKey) return;
      // Don't intercept pure horizontal swipe gestures
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;

      const deltaY = e.deltaY;
      if (!deltaY) return;

      const scrollable = findScrollableParent(e.target as HTMLElement, dockEl);

      // If hovering over non-scrollable area of dock (header, status bar, phone chassis, inputs)
      if (!scrollable) {
        e.preventDefault();
        scrollRightSide(deltaY);
        return;
      }

      const maxScroll = scrollable.scrollHeight - scrollable.clientHeight;
      if (maxScroll <= 1) {
        // Content completely fits; full wheel goes to right side
        e.preventDefault();
        scrollRightSide(deltaY);
        return;
      }

      if (deltaY > 0) {
        // Scrolling down
        const availableDown = maxScroll - scrollable.scrollTop;
        if (availableDown <= 1) {
          // At bottom of chat/dialogs: overscroll scrolls right side
          e.preventDefault();
          scrollRightSide(deltaY);
        } else if (deltaY > availableDown) {
          // Hits bottom, remaining delta scrolls right side
          e.preventDefault();
          scrollable.scrollTop = maxScroll;
          scrollRightSide(deltaY - availableDown);
        }
      } else if (deltaY < 0) {
        // Scrolling up
        const availableUp = scrollable.scrollTop;
        if (availableUp <= 1) {
          // At top of chat/dialogs: overscroll scrolls right side
          e.preventDefault();
          scrollRightSide(deltaY);
        } else if (Math.abs(deltaY) > availableUp) {
          // Hits top, remaining delta scrolls right side
          e.preventDefault();
          scrollable.scrollTop = 0;
          scrollRightSide(deltaY + availableUp);
        }
      }
    };

    let lastTouchY = 0;
    const handleTouchStart = (e: TouchEvent) => {
      if (e.touches.length === 1) {
        lastTouchY = e.touches[0].clientY;
      }
    };

    const handleTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      const currentY = e.touches[0].clientY;
      const deltaY = lastTouchY - currentY;
      lastTouchY = currentY;

      if (Math.abs(deltaY) < 1) return;

      const scrollable = findScrollableParent(e.target as HTMLElement, dockEl);
      if (!scrollable) {
        scrollRightSide(deltaY);
        return;
      }

      const maxScroll = scrollable.scrollHeight - scrollable.clientHeight;
      if (maxScroll <= 1) {
        scrollRightSide(deltaY);
        return;
      }

      if (deltaY > 0 && scrollable.scrollTop >= maxScroll - 1) {
        scrollRightSide(deltaY);
      } else if (deltaY < 0 && scrollable.scrollTop <= 1) {
        scrollRightSide(deltaY);
      }
    };

    dockEl.addEventListener('wheel', handleWheel, { passive: false });
    dockEl.addEventListener('touchstart', handleTouchStart, { passive: true });
    dockEl.addEventListener('touchmove', handleTouchMove, { passive: true });

    return () => {
      dockEl.removeEventListener('wheel', handleWheel);
      dockEl.removeEventListener('touchstart', handleTouchStart);
      dockEl.removeEventListener('touchmove', handleTouchMove);
    };
  }, [isMobileDockOpen]);

  // Load chats on mount or dock open
  useEffect(() => {
    if (isMobileDockOpen && chats.length === 0) {
      loadChats();
    }
  }, [isMobileDockOpen]);

  const loadChats = async (force = false) => {
    setLoadingChats(true);
    try {
      const data = await api.getChats(force);
      setChats(data);
    } catch (err: any) {
      addToast({ type: 'error', message: err.message || 'Failed to load Telegram chats' });
    } finally {
      setLoadingChats(false);
    }
  };

  // Load messages when an active chat is selected
  useEffect(() => {
    if (!activeChat) {
      setMessages([]);
      return;
    }
    loadMessages(activeChat.id);
  }, [activeChat]);

  const loadMessages = async (chatId: string | number) => {
    setLoadingMessages(true);
    try {
      const res = await api.getChatMessages(chatId, 50);
      // Backend returns messages descending; reverse for conversational ascending flow
      setMessages([...res.messages].reverse());
      setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        setShowScrollBottom(false);
      }, 100);
    } catch (err: any) {
      addToast({ type: 'error', message: `Could not load messages: ${err.message}` });
    } finally {
      setLoadingMessages(false);
    }
  };

  // Handle messages scroll to show/hide scroll down button
  const handleMessagesScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollBottom(distanceFromBottom > 150);
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    setShowScrollBottom(false);
  };

  // Select chat: opens chat in mobile dock AND updates right side files list with auto-scroll
  const handleSelectChat = (chat: ChatItem) => {
    setActiveChat(chat);
    setSelectedChat(chat);
    if (activeTab !== 'cinema') {
      setActiveTab('cinema');
    }
    // Auto-scroll the files catalog on the right side
    scrollRightSideToTop(true);
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeChat || !inputText.trim() || isSending) return;

    const text = inputText.trim();
    setInputText('');
    setIsSending(true);

    try {
      await api.sendChatMessage(activeChat.id, text);
      // Append temporary optimistic message
      const optimisticMsg: ChatMessage = {
        id: Date.now(),
        date: new Date().toISOString(),
        text,
        out: true,
        sender_name: 'You',
        media: null,
      };
      setMessages((prev) => [...prev, optimisticMsg]);
      setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        setShowScrollBottom(false);
      }, 50);
    } catch (err: any) {
      addToast({ type: 'error', message: `Failed to send: ${err.message}` });
      setInputText(text); // Restore unsent text
    } finally {
      setIsSending(false);
    }
  };

  // Handle Drag Resizing
  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    isDraggingRef.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handleMouseMove = (moveEvent: MouseEvent) => {
      if (!isDraggingRef.current) return;
      const newWidth = Math.max(320, Math.min(520, moveEvent.clientX));
      setMobileDockWidth(newWidth);
    };

    const handleMouseUp = () => {
      isDraggingRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

  // Launch VLC on media click
  const handleLaunchVlc = async (chatId: string | number, messageId: number, filename?: string) => {
    try {
      await api.playInVlc({
        chat_id: String(chatId),
        message_id: messageId,
        filename: filename || 'video.mp4',
      });
      addToast({
        type: 'success',
        title: 'VLC Launched',
        message: `Playing ${cleanFileName(filename || 'video.mp4')} in VLC`,
      });
    } catch (err: any) {
      addToast({ type: 'error', message: `VLC Error: ${err.message}` });
    }
  };

  // Stream in Cinema View
  const handleStreamInCinema = (chatId: string | number, messageId: number, filename?: string) => {
    if (activeChat) {
      setSelectedChat(activeChat);
    }
    if (activeTab !== 'cinema') {
      setActiveTab('cinema');
    }
    scrollRightSideToTop(true);
    addToast({
      type: 'info',
      title: 'Opening Cinema',
      message: `Streaming ${cleanFileName(filename || 'video.mp4')}`,
    });
  };

  const handleCopyLink = (chatId: string | number, messageId: number, filename?: string) => {
    const origin = window.location.origin;
    const url = `${origin}/stream/${encodeURIComponent(String(chatId))}/${messageId}/${encodeURIComponent(filename || 'video.mp4')}`;
    navigator.clipboard.writeText(url);
    setCopiedId(messageId);
    setTimeout(() => setCopiedId(null), 2000);
    addToast({ type: 'success', message: 'Stream link copied to clipboard!' });
  };

  const handleCopyText = (text: string) => {
    navigator.clipboard.writeText(text);
    addToast({ type: 'success', message: 'Message text copied!' });
  };

  // Filtered chats list
  const filteredChats = useMemo(() => {
    return chats.filter((c) => {
      const name = c.name || '';
      const username = c.username || '';
      const query = searchQuery.trim().toLowerCase();
      const matchesSearch =
        !query ||
        name.toLowerCase().includes(query) ||
        username.toLowerCase().includes(query);

      if (!matchesSearch) return false;

      if (activeFilter === 'saved') return c.type === 'saved_messages';
      if (activeFilter === 'channel') return c.type === 'channel';
      if (activeFilter === 'group') return c.type === 'group' || c.type === 'supergroup';

      return true;
    });
  }, [chats, searchQuery, activeFilter]);

  // Filtered messages inside current active chat
  const displayedMessages = useMemo(() => {
    if (!chatSearchQuery.trim()) return messages;
    const q = chatSearchQuery.trim().toLowerCase();
    return messages.filter((m) => {
      const textMatch = m.text && m.text.toLowerCase().includes(q);
      const fileMatch = m.media?.file_name && m.media.file_name.toLowerCase().includes(q);
      const senderMatch = m.sender_name && m.sender_name.toLowerCase().includes(q);
      return textMatch || fileMatch || senderMatch;
    });
  }, [messages, chatSearchQuery]);

  // Date Divider Helper
  const getDateDivider = (isoDate?: string | null): string => {
    if (!isoDate) return '';
    const d = new Date(isoDate);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return 'Today';
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
    const sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: sameYear ? undefined : 'numeric',
    });
  };

  // Helper for document icons
  const getDocIcon = (filename?: string) => {
    const ext = (filename || '').split('.').pop()?.toLowerCase();
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext || '')) {
      return <FileArchive className="w-5 h-5 text-amber-400 shrink-0" />;
    }
    if (['mp3', 'flac', 'wav', 'ogg', 'm4a'].includes(ext || '')) {
      return <Music className="w-5 h-5 text-rose-400 shrink-0" />;
    }
    if (['mp4', 'mkv', 'avi', 'mov', 'webm'].includes(ext || '')) {
      return <Play className="w-5 h-5 text-sky-400 shrink-0" />;
    }
    return <FileText className="w-5 h-5 text-emerald-400 shrink-0" />;
  };

  // Floating button when collapsed
  if (!isMobileDockOpen) {
    return (
      <button
        onClick={() => setMobileDockOpen(true)}
        className="fixed left-0 top-24 z-40 bg-card/90 dark:bg-card/95 hover:bg-primary/20 text-foreground border border-l-0 border-border/80 hover:border-primary/50 shadow-xl rounded-r-xl px-2.5 py-3 flex flex-col items-center gap-1.5 transition-all duration-200 group backdrop-blur-md cursor-pointer select-none"
        title="Open Mobile Telegram Dock (Ctrl+B)"
      >
        <div className="w-7 h-7 rounded-lg bg-sky-500/20 text-sky-400 flex items-center justify-center group-hover:scale-110 transition-transform shadow-inner">
          <MessageSquare className="w-4 h-4" />
        </div>
        <span className="text-[10px] font-mono tracking-tighter uppercase text-muted-foreground group-hover:text-primary writing-vertical rotate-180">
          TG Mobile
        </span>
      </button>
    );
  }

  return (
    <aside
      ref={dockAsideRef}
      style={{ width: `${mobileDockWidth}px` }}
      className="sticky top-16 flex-shrink-0 h-[calc(100vh-4rem)] flex flex-col z-30 border-r border-border/70 bg-card/40 dark:bg-card/20 backdrop-blur-xl transition-[width] duration-75"
    >
      {/* Smartphone Chassis Shell */}
      <div className="flex-1 m-2 flex flex-col rounded-2xl border border-border/90 bg-background/95 dark:bg-[#0c121e]/95 shadow-2xl overflow-hidden relative">
        
        {/* Minimalist Phone Top Bar (Status indicators + Dock controls) */}
        <div className="flex items-center justify-between px-3.5 pt-2 pb-1.5 border-b border-border/40 text-[11px] font-mono text-muted-foreground bg-card/40 select-none">
          <div className="flex items-center gap-1.5 font-semibold text-foreground/90">
            <span>{currentTime || '12:00'}</span>
          </div>

          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              MTProto
            </span>
            <button
              onClick={() => setMobileDockOpen(false)}
              className="p-1 rounded-md hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
              title="Close Phone Dock (Ctrl+B)"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* View Router: Inside Chat VS Dialogs List */}
        {activeChat ? (
          /* CONVERSATION VIEW */
          <div className="flex-1 flex flex-col min-h-0 bg-background/50 relative">
            {/* Chat Header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-border/60 bg-card/60 backdrop-blur-md select-none">
              <div className="flex items-center gap-2 min-w-0">
                <button
                  onClick={() => {
                    setActiveChat(null);
                    setSearchInChatOpen(false);
                    setChatSearchQuery('');
                  }}
                  className="p-1.5 rounded-lg hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                  title="Back to chats"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>

                <div className="w-7 h-7 rounded-full bg-primary/20 flex-shrink-0 overflow-hidden flex items-center justify-center border border-primary/30">
                  {activeChat.has_avatar ? (
                    <img
                      src={`/api/chats/${activeChat.id}/avatar`}
                      alt={activeChat.name || 'Chat'}
                      loading="lazy"
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  ) : (
                    <span className="text-[11px] font-bold text-primary">
                      {(activeChat.name || '?').charAt(0).toUpperCase()}
                    </span>
                  )}
                </div>

                <div
                  onClick={() => scrollRightSideToTop(true)}
                  className="min-w-0 flex-1 cursor-pointer group/chatHeader"
                  title="Click to scroll right-side files to top"
                >
                  <h4 className="text-xs font-semibold text-foreground truncate leading-tight group-hover/chatHeader:text-primary transition-colors">
                    {activeChat.name || 'Telegram Chat'}
                  </h4>
                  <p className="text-[10px] text-muted-foreground truncate">
                    {activeChat.type === 'saved_messages'
                      ? 'Cloud Storage'
                      : activeChat.username
                      ? `@${activeChat.username}`
                      : activeChat.type}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-1">
                {/* Search inside chat button */}
                <button
                  onClick={() => setSearchInChatOpen(!isSearchInChatOpen)}
                  className={`p-1.5 rounded-lg transition-colors cursor-pointer ${
                    isSearchInChatOpen ? 'bg-primary/20 text-primary' : 'hover:bg-muted text-muted-foreground hover:text-foreground'
                  }`}
                  title="Search in this chat"
                >
                  <Search className="w-3.5 h-3.5" />
                </button>

                {/* Target Chat sync button */}
                <button
                  onClick={() => {
                    setSelectedChat(activeChat);
                    if (activeTab !== 'cinema') setActiveTab('cinema');
                    const mainEl = document.querySelector('main');
                    if (mainEl) mainEl.scrollTo({ top: 0, behavior: 'smooth' });
                    addToast({
                      type: 'success',
                      message: `Target set to ${activeChat.name || 'Chat'}`,
                    });
                  }}
                  className={`p-1.5 rounded-lg text-[10px] font-medium flex items-center gap-1 transition-colors cursor-pointer ${
                    globalSelectedChat?.id === activeChat.id
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                      : 'hover:bg-muted text-muted-foreground hover:text-foreground'
                  }`}
                  title="Sync files on right side"
                >
                  <FolderPlus className="w-3.5 h-3.5" />
                </button>

                <button
                  onClick={() => loadMessages(activeChat.id)}
                  disabled={loadingMessages}
                  className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                  title="Refresh conversation"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingMessages ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>

            {/* In-Chat Search Bar */}
            {isSearchInChatOpen && (
              <div className="p-2 border-b border-border/60 bg-muted/30 flex items-center gap-2 animate-in slide-in-from-top-2 duration-150 select-none">
                <Search className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                <input
                  type="text"
                  value={chatSearchQuery}
                  onChange={(e) => setChatSearchQuery(e.target.value)}
                  placeholder="Find in conversation..."
                  autoFocus
                  className="flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
                />
                {chatSearchQuery && (
                  <button
                    onClick={() => setChatSearchQuery('')}
                    className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
            )}

            {/* Messages Feed */}
            <div
              ref={messagesContainerRef}
              onScroll={handleMessagesScroll}
              className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0 text-xs custom-scrollbar relative"
            >
              {loadingMessages ? (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-2 py-10 select-none">
                  <RefreshCw className="w-5 h-5 animate-spin text-primary" />
                  <span className="text-xs">Fetching messages via MTProto...</span>
                </div>
              ) : displayedMessages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12 text-center px-4 select-none">
                  <MessageSquare className="w-8 h-8 mb-2 opacity-30" />
                  <p className="text-xs">
                    {chatSearchQuery ? 'No messages match your search.' : 'No recent messages in this chat.'}
                  </p>
                </div>
              ) : (
                displayedMessages.map((m, index) => {
                  const isOut = m.out;
                  const isVideo = m.media?.type === 'video';
                  const isDoc = m.media?.type === 'document';
                  const isPhoto = m.media?.type === 'photo';

                  // Date divider calculation
                  const currentDateDivider = getDateDivider(m.date);
                  const prevMessage = index > 0 ? displayedMessages[index - 1] : null;
                  const prevDateDivider = prevMessage ? getDateDivider(prevMessage.date) : '';
                  const showDateDivider = currentDateDivider && currentDateDivider !== prevDateDivider;

                  return (
                    <React.Fragment key={m.id}>
                      {/* Telegram Centered Date Divider */}
                      {showDateDivider && (
                        <div className="flex justify-center my-2 select-none">
                          <span className="px-2.5 py-0.5 rounded-full bg-muted/70 dark:bg-card/90 border border-border/50 text-[10px] font-semibold text-muted-foreground shadow-xs">
                            {currentDateDivider}
                          </span>
                        </div>
                      )}

                      <div
                        className={`flex flex-col ${isOut ? 'items-end' : 'items-start'} group/msg relative`}
                      >
                        {!isOut && m.sender_name && activeChat.type !== 'user' && (
                          <span className="text-[10px] font-medium text-primary/80 mb-0.5 ml-1 select-none">
                            {m.sender_name}
                          </span>
                        )}

                        <div
                          className={`max-w-[88%] rounded-2xl px-3 py-2 shadow-sm relative transition-all ${
                            isOut
                              ? 'bg-primary/20 dark:bg-emerald-950/40 border border-primary/30 text-foreground rounded-tr-xs'
                              : 'bg-card/90 dark:bg-card/70 border border-border/70 text-foreground rounded-tl-xs'
                          }`}
                        >
                          {/* Quick Copy Message Hover Button */}
                          {m.text && (
                            <button
                              onClick={() => handleCopyText(m.text)}
                              className="absolute top-1.5 right-1.5 opacity-0 group-hover/msg:opacity-100 p-1 rounded-md bg-background/80 hover:bg-background text-muted-foreground hover:text-foreground transition-opacity shadow-xs z-10 cursor-pointer select-none"
                              title="Copy text"
                            >
                              <Copy className="w-2.5 h-2.5" />
                            </button>
                          )}

                          {/* Video Card */}
                          {isVideo && m.media && (
                            <div className="mb-2 rounded-xl overflow-hidden border border-border/80 bg-background/80 flex flex-col select-none">
                              {/* Video Poster with Quick Play & Click-to-preview */}
                              <div
                                onClick={() =>
                                  setLightboxMedia({
                                    type: 'video',
                                    src: m.media?.stream_url || `/stream/${activeChat.id}/${m.id}/${m.media?.file_name || 'video.mp4'}`,
                                    thumbUrl: m.media?.thumb_url,
                                    title: m.media?.file_name || 'Video',
                                    fileSize: m.media?.file_size,
                                    duration: m.media?.duration,
                                    chatId: activeChat.id,
                                    messageId: m.id,
                                  })
                                }
                                className="relative aspect-video bg-black/40 flex items-center justify-center overflow-hidden group/media cursor-pointer"
                              >
                                {m.media.thumb_url && (
                                  <img
                                    src={m.media.thumb_url}
                                    alt=""
                                    loading="lazy"
                                    className="w-full h-full object-cover opacity-80 group-hover/media:opacity-95 transition-opacity"
                                    onError={(e) => {
                                      (e.currentTarget as HTMLImageElement).style.display = 'none';
                                    }}
                                  />
                                )}
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleStreamInCinema(activeChat.id, m.id, m.media?.file_name);
                                  }}
                                  className="absolute inset-0 m-auto w-10 h-10 rounded-full bg-primary/90 text-primary-foreground flex items-center justify-center shadow-lg group-hover/media:scale-110 transition-transform cursor-pointer"
                                  title="Stream in Cinema"
                                >
                                  <Play className="w-5 h-5 ml-0.5" />
                                </button>
                              </div>

                              {/* Meta & Action Toolbar */}
                              <div className="p-2 flex flex-col gap-1.5">
                                <p className="text-[11px] font-semibold text-foreground truncate select-text" title={m.media.file_name}>
                                  {cleanFileName(m.media.file_name || 'video.mp4')}
                                </p>
                                <div className="flex items-center justify-between text-[10px] text-muted-foreground select-none">
                                  <span>{formatBytes(m.media.file_size || 0)}</span>
                                  {m.media.duration && <span>{formatDuration(m.media.duration)}</span>}
                                </div>

                                <div className="flex items-center gap-1.5 pt-1 border-t border-border/40 select-none">
                                  <button
                                    onClick={() => handleStreamInCinema(activeChat.id, m.id, m.media?.file_name)}
                                    className="flex-1 py-1 px-2 rounded-md bg-primary/20 hover:bg-primary/30 text-primary text-[10px] font-medium flex items-center justify-center gap-1 transition-colors cursor-pointer"
                                  >
                                    <Play className="w-3 h-3" />
                                    Cinema
                                  </button>
                                  <button
                                    onClick={() => handleLaunchVlc(activeChat.id, m.id, m.media?.file_name)}
                                    className="flex-1 py-1 px-2 rounded-md bg-orange-500/20 hover:bg-orange-500/30 text-orange-400 text-[10px] font-medium flex items-center justify-center gap-1 transition-colors cursor-pointer"
                                    title="Launch in local VLC player"
                                  >
                                    <Tv className="w-3 h-3" />
                                    VLC
                                  </button>
                                  <button
                                    onClick={() => handleCopyLink(activeChat.id, m.id, m.media?.file_name)}
                                    className="p-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                                    title="Copy stream link"
                                  >
                                    {copiedId === m.id ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                                  </button>
                                </div>
                              </div>
                            </div>
                          )}

                          {/* Photo Card with Lightbox Preview */}
                          {isPhoto && m.media && (
                            <div className="mb-2 rounded-xl overflow-hidden border border-border/80 bg-black/30 select-none">
                              <div
                                onClick={() =>
                                  setLightboxMedia({
                                    type: 'photo',
                                    src: m.media?.thumb_url || `/stream/${activeChat.id}/${m.id}/thumb`,
                                    title: m.media?.file_name || 'Photo',
                                    fileSize: m.media?.file_size,
                                    chatId: activeChat.id,
                                    messageId: m.id,
                                  })
                                }
                                className="relative aspect-square max-h-56 overflow-hidden flex items-center justify-center cursor-pointer group/photo"
                              >
                                <img
                                  src={m.media.thumb_url || `/stream/${activeChat.id}/${m.id}/thumb`}
                                  alt=""
                                  loading="lazy"
                                  className="w-full h-full object-cover transition-transform duration-300 group-hover/photo:scale-105"
                                  onError={(e) => {
                                    (e.currentTarget as HTMLImageElement).style.display = 'none';
                                  }}
                                />
                                <div className="absolute inset-0 bg-black/20 opacity-0 group-hover/photo:opacity-100 transition-opacity flex items-center justify-center">
                                  <Maximize2 className="w-6 h-6 text-white drop-shadow-md" />
                                </div>
                              </div>
                            </div>
                          )}

                          {/* Document File Card */}
                          {isDoc && m.media && (
                            <div className="mb-1.5 p-2 rounded-xl bg-background/60 border border-border/60 flex items-center justify-between gap-2 select-none">
                              <div className="flex items-center gap-2 min-w-0">
                                {getDocIcon(m.media.file_name)}
                                <div className="min-w-0">
                                  <p className="text-[11px] font-medium truncate select-text">{m.media.file_name}</p>
                                  <span className="text-[10px] text-muted-foreground">{formatBytes(m.media.file_size || 0)}</span>
                                </div>
                              </div>
                              {m.media.download_url && (
                                <a
                                  href={m.media.download_url}
                                  download={m.media.file_name}
                                  className="p-1.5 rounded-lg bg-muted hover:bg-primary/20 text-muted-foreground hover:text-primary transition-colors cursor-pointer"
                                  title="Download File"
                                >
                                  <Download className="w-3.5 h-3.5" />
                                </a>
                              )}
                            </div>
                          )}

                          {/* Text Message Content (Selectable) */}
                          {m.text && (
                            <p className="whitespace-pre-wrap break-words leading-relaxed text-[11px] select-text select-auto cursor-text selection:bg-primary/30 selection:text-foreground">
                              {m.text}
                            </p>
                          )}

                          {/* Timestamp & Status */}
                          <div className="flex items-center justify-end gap-1 mt-1 text-[9px] text-muted-foreground/80 select-none">
                            {m.date && (
                              <span>
                                {new Date(m.date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                              </span>
                            )}
                            {isOut && <CheckCheck className="w-3 h-3 text-primary inline-block" />}
                          </div>
                        </div>
                      </div>
                    </React.Fragment>
                  );
                })
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Telegram Floating Scroll-To-Bottom Button (FAB) */}
            {showScrollBottom && (
              <button
                onClick={scrollToBottom}
                className="absolute right-4 bottom-16 z-20 w-8 h-8 rounded-full bg-card/90 dark:bg-[#111927]/95 border border-border shadow-lg text-primary hover:text-primary-foreground hover:bg-primary transition-all flex items-center justify-center animate-in zoom-in-75 backdrop-blur-md cursor-pointer group select-none"
                title="Scroll to latest messages"
              >
                <ArrowDown className="w-4 h-4 group-hover:translate-y-0.5 transition-transform" />
              </button>
            )}

            {/* Input Bar */}
            <form
              onSubmit={handleSendMessage}
              className="p-2 border-t border-border/60 bg-card/60 flex items-center gap-1.5 backdrop-blur-md select-none"
            >
              <input
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="Message..."
                className="flex-1 bg-background/80 border border-border/80 rounded-xl px-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary transition-colors select-text"
              />
              <button
                type="submit"
                disabled={!inputText.trim() || isSending}
                className="p-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:pointer-events-none transition-all shadow-sm cursor-pointer"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </form>
          </div>
        ) : (
          /* DIALOGS LIST VIEW */
          <div className="flex-1 flex flex-col min-h-0 bg-background/30 select-none">
            {/* Search Input Bar */}
            <div className="p-2.5 pb-2 border-b border-border/40">
              <div className="relative flex items-center">
                <Search className="w-3.5 h-3.5 absolute left-2.5 text-muted-foreground" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search chats, channels..."
                  className="w-full bg-muted/40 border border-border/60 rounded-xl pl-8 pr-7 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/80 transition-all"
                />
                {searchQuery && (
                  <button
                    onClick={() => setSearchQuery('')}
                    className="absolute right-2 p-0.5 rounded-full hover:bg-muted text-muted-foreground hover:text-foreground"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>

              {/* Telegram Channel / Group / Saved Category Filter Pills */}
              <div className="flex items-center gap-1 mt-2 overflow-x-auto pb-0.5 scrollbar-none">
                {(
                  [
                    { id: 'all', label: 'All' },
                    { id: 'saved', label: 'Saved' },
                    { id: 'channel', label: 'Channels' },
                    { id: 'group', label: 'Groups' },
                  ] as const
                ).map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveFilter(tab.id)}
                    className={`text-[10px] font-medium px-2 py-0.5 rounded-full transition-all cursor-pointer ${
                      activeFilter === tab.id
                        ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                        : 'bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-muted'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Dialogs Scroll List */}
            <div className="flex-1 overflow-y-auto divide-y divide-border/30 custom-scrollbar">
              {loadingChats ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
                  <RefreshCw className="w-5 h-5 animate-spin text-primary" />
                  <span className="text-xs">Syncing Telegram Dialogs...</span>
                </div>
              ) : filteredChats.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground text-center px-4">
                  <Search className="w-8 h-8 mb-2 opacity-30" />
                  <p className="text-xs">No matching dialogs found.</p>
                </div>
              ) : (
                filteredChats.map((chat) => {
                  const isSelected = globalSelectedChat?.id === chat.id;

                  return (
                    <div
                      key={chat.id}
                      onClick={() => handleSelectChat(chat)}
                      className={`px-3 py-2.5 flex items-center gap-2.5 hover:bg-muted/40 cursor-pointer transition-colors group ${
                        isSelected ? 'bg-primary/10 border-l-2 border-primary' : ''
                      }`}
                    >
                      {/* Avatar */}
                      <div className="relative w-9 h-9 rounded-full bg-primary/20 flex-shrink-0 flex items-center justify-center overflow-hidden border border-border/80">
                        {chat.type === 'saved_messages' ? (
                          <Bookmark className="w-4 h-4 text-primary" />
                        ) : chat.has_avatar ? (
                          <img
                            src={`/api/chats/${chat.id}/avatar`}
                            alt={chat.name || 'Chat'}
                            loading="lazy"
                            className="w-full h-full object-cover"
                            onError={(e) => {
                              (e.currentTarget as HTMLImageElement).style.display = 'none';
                            }}
                          />
                        ) : (
                          <span className="text-xs font-bold text-primary">
                            {(chat.name || '?').charAt(0).toUpperCase()}
                          </span>
                        )}
                        {chat.pinned && (
                          <div className="absolute bottom-0 right-0 w-3 h-3 rounded-full bg-background flex items-center justify-center">
                            <Pin className="w-2 h-2 text-primary" />
                          </div>
                        )}
                      </div>

                      {/* Chat Details */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-1">
                          <h4 className="text-xs font-semibold text-foreground truncate group-hover:text-primary transition-colors">
                            {chat.name || 'Unnamed Chat'}
                          </h4>
                          {chat.unread_count && chat.unread_count > 0 ? (
                            <span className="px-1.5 py-0.2 rounded-full bg-primary text-primary-foreground text-[9px] font-bold">
                              {chat.unread_count}
                            </span>
                          ) : null}
                        </div>

                        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground mt-0.5">
                          {chat.type === 'channel' && <Radio className="w-2.5 h-2.5 text-sky-400" />}
                          {chat.type === 'group' && <Users className="w-2.5 h-2.5 text-emerald-400" />}
                          <span className="truncate">
                            {chat.type === 'saved_messages'
                              ? 'Personal Cloud Storage'
                              : chat.username
                              ? `@${chat.username}`
                              : chat.type}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>

      {/* Draggable Splitter Handle (Right Border) */}
      <div
        onMouseDown={handleMouseDown}
        className="absolute top-0 right-0 w-2 h-full cursor-col-resize hover:bg-primary/40 active:bg-primary transition-colors group flex items-center justify-center z-50 select-none"
        title="Drag to resize dock width"
      >
        <div className="w-0.5 h-8 rounded-full bg-border group-hover:bg-primary transition-colors" />
      </div>

      {/* Telegram Thumbnail / High-Res Lightbox Modal */}
      {lightboxMedia && (
        <div
          onClick={() => {
            setLightboxMedia(null);
            setZoomLevel(1);
          }}
          className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex flex-col items-center justify-between p-4 animate-in fade-in duration-200 select-none"
        >
          {/* Lightbox Header */}
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-3xl flex items-center justify-between text-white pb-3 border-b border-white/10"
          >
            <div className="min-w-0">
              <h3 className="text-sm font-semibold truncate">{cleanFileName(lightboxMedia.title)}</h3>
              <div className="flex items-center gap-2 text-xs text-zinc-400 mt-0.5">
                {lightboxMedia.fileSize ? <span>{formatBytes(lightboxMedia.fileSize)}</span> : null}
                {lightboxMedia.duration ? <span>• {formatDuration(lightboxMedia.duration)}</span> : null}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setZoomLevel((z) => Math.min(2.5, z + 0.25))}
                className="p-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors cursor-pointer"
                title="Zoom In"
              >
                <ZoomIn className="w-4 h-4" />
              </button>
              <button
                onClick={() => setZoomLevel((z) => Math.max(0.75, z - 0.25))}
                className="p-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors cursor-pointer"
                title="Zoom Out"
              >
                <ZoomOut className="w-4 h-4" />
              </button>
              <button
                onClick={() => setZoomLevel(1)}
                className="p-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors cursor-pointer"
                title="Reset Zoom"
              >
                <Maximize2 className="w-4 h-4" />
              </button>
              <button
                onClick={() => {
                  setLightboxMedia(null);
                  setZoomLevel(1);
                }}
                className="p-1.5 rounded-lg bg-white/10 hover:bg-rose-500/80 text-white transition-colors cursor-pointer"
                title="Close (Esc)"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Lightbox Media Body */}
          <div
            onClick={(e) => e.stopPropagation()}
            className="flex-1 flex items-center justify-center p-4 overflow-hidden max-w-4xl max-h-[75vh]"
          >
            {lightboxMedia.type === 'video' ? (
              <video
                src={lightboxMedia.src}
                controls
                autoPlay
                className="max-h-full max-w-full rounded-xl shadow-2xl object-contain transition-transform duration-200"
                style={{ transform: `scale(${zoomLevel})` }}
              />
            ) : (
              <img
                src={lightboxMedia.src}
                alt={lightboxMedia.title}
                className="max-h-full max-w-full rounded-xl shadow-2xl object-contain transition-transform duration-200"
                style={{ transform: `scale(${zoomLevel})` }}
              />
            )}
          </div>

          {/* Lightbox Footer Actions */}
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-xl flex items-center justify-center gap-3 pt-3 border-t border-white/10"
          >
            {lightboxMedia.type === 'video' && (
              <>
                <button
                  onClick={() => {
                    handleStreamInCinema(lightboxMedia.chatId, lightboxMedia.messageId, lightboxMedia.title);
                    setLightboxMedia(null);
                  }}
                  className="px-3.5 py-1.5 rounded-xl bg-primary text-primary-foreground text-xs font-medium flex items-center gap-1.5 hover:opacity-90 transition-opacity cursor-pointer shadow-sm"
                >
                  <Play className="w-3.5 h-3.5" />
                  Cinema Player
                </button>
                <button
                  onClick={() => handleLaunchVlc(lightboxMedia.chatId, lightboxMedia.messageId, lightboxMedia.title)}
                  className="px-3.5 py-1.5 rounded-xl bg-orange-500 text-white text-xs font-medium flex items-center gap-1.5 hover:bg-orange-600 transition-colors cursor-pointer shadow-sm"
                >
                  <Tv className="w-3.5 h-3.5" />
                  Play in VLC
                </button>
              </>
            )}
            <a
              href={lightboxMedia.src}
              download={lightboxMedia.title}
              className="px-3.5 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white text-xs font-medium flex items-center gap-1.5 transition-colors cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              Download
            </a>
            <button
              onClick={() => handleCopyLink(lightboxMedia.chatId, lightboxMedia.messageId, lightboxMedia.title)}
              className="px-3.5 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white text-xs font-medium flex items-center gap-1.5 transition-colors cursor-pointer"
            >
              <Copy className="w-3.5 h-3.5" />
              Copy URL
            </button>
          </div>
        </div>
      )}
    </aside>
  );
};
