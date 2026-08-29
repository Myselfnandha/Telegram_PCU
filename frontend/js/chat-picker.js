/**
 * Telegram Chat Picker Module.
 * Searchable modal for selecting destination chat/group/channel or Saved Messages.
 */

import { escapeHtml } from './utils.js';

class ChatPicker {
  constructor() {
    this.modal = null;
    this.searchInput = null;
    this.listContainer = null;
    this.chats = [];
    // Default immediately to Saved Messages so uploads always work instantly!
    this.selectedChat = {
      id: 'me',
      name: 'Saved Messages (Personal Cloud)',
      type: 'saved_messages'
    };
    this.activeFilter = 'all';
    this.onSelectCallback = null;
    this.storageKey = 'tg_selected_chat';
    this.isLoading = false;
  }

  init(onSelect) {
    this.onSelectCallback = onSelect;
    this.modal = document.getElementById('chatModal');
    this.searchInput = document.getElementById('chatSearchInput');
    this.listContainer = document.getElementById('chatListContainer');

    const openBtn = document.getElementById('btnChooseChat');
    const closeBtn = document.getElementById('btnCloseChatModal');

    if (openBtn) openBtn.addEventListener('click', () => this.open());
    if (closeBtn) closeBtn.addEventListener('click', () => this.close());

    if (this.modal) {
      this.modal.addEventListener('click', (e) => {
        if (e.target === this.modal) this.close();
      });
    }

    if (this.searchInput) {
      this.searchInput.addEventListener('input', (e) => {
        this.renderList(e.target.value);
      });
    }

    // Category filter tabs setup
    const filterChips = document.querySelectorAll('#chatModalFilterTabs .filter-chip');
    filterChips.forEach((chip) => {
      chip.addEventListener('click', () => {
        filterChips.forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        this.activeFilter = chip.getAttribute('data-filter') || 'all';
        this.renderList(this.searchInput ? this.searchInput.value : '');
      });
    });

    // Try to load cached chat from localStorage, or use default Saved Messages
    this.loadSavedSelection();

    // Fetch initial chat list in background without blocking
    setTimeout(() => this.fetchChats(), 100);
  }

  loadSavedSelection() {
    try {
      const saved = localStorage.getItem(this.storageKey);
      if (saved) {
        this.selectedChat = JSON.parse(saved);
      }
    } catch (e) {
      console.warn('Could not load saved chat from localStorage:', e);
    }
    this.updateTriggerUI(this.selectedChat);
    if (this.onSelectCallback) this.onSelectCallback(this.selectedChat);
  }

  saveSelection(chat) {
    this.selectedChat = chat;
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(chat));
    } catch (e) {}
    this.updateTriggerUI(chat);
    if (this.onSelectCallback) this.onSelectCallback(chat);
  }

  updateTriggerUI(chat) {
    const nameEl = document.getElementById('currentChatName');
    const typeEl = document.getElementById('currentChatType');
    const avatarWrapper = document.getElementById('currentChatAvatarWrapper');

    if (nameEl && chat) {
      nameEl.textContent = chat.name || 'Saved Messages';
    }
    if (typeEl && chat) {
      const typeLabel = chat.type === 'saved_messages' ? 'CLOUD' : (chat.type || 'CHAT').replace('_', ' ').toUpperCase();
      typeEl.textContent = typeLabel;
    }
    if (avatarWrapper && chat) {
      if (chat.type === 'saved_messages') {
        avatarWrapper.innerHTML = `
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
          </svg>
        `;
      } else {
        const initial = (chat.name || 'C').charAt(0).toUpperCase();
        const avatarUrl = `/api/chats/${encodeURIComponent(chat.id)}/avatar`;
        avatarWrapper.innerHTML = `
          <img src="${avatarUrl}" alt="" style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%; display: block;" onerror="this.outerHTML='<span style=\\'font-weight:700; font-size:1rem; color:var(--accent-secondary);\\'>${escapeHtml(initial)}</span>'">
        `;
      }
    }
  }

  async fetchChats(force = false) {
    if (this.isLoading) return;
    this.isLoading = true;
    try {
      const url = force ? '/api/chats?force_refresh=true' : '/api/chats';
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to fetch chats');
      this.chats = await res.json();

      // If user had Saved Messages, update the ID with the real user ID if needed
      if (this.selectedChat && this.selectedChat.type === 'saved_messages' && this.chats.length > 0) {
        this.selectedChat = this.chats[0];
      }

      if (this.modal && this.modal.classList.contains('open')) {
        this.renderList(this.searchInput ? this.searchInput.value : '');
      }
    } catch (e) {
      console.error('Error loading chats:', e);
    } finally {
      this.isLoading = false;
    }
  }

  open(customCallback = null) {
    this.customCallback = customCallback;
    if (!this.modal) return;
    this.modal.classList.add('open');
    if (this.searchInput) {
      this.searchInput.value = '';
      setTimeout(() => {
        this.searchInput.focus();
        this.searchInput.select();
      }, 50);
    }
    if (this.chats.length === 0) {
      this.renderLoading();
      this.fetchChats();
    } else {
      this.renderList();
    }
  }

  renderLoading() {
    if (!this.listContainer) return;
    this.listContainer.innerHTML = `
      <div style="padding: 32px; text-align: center; color: var(--text-muted);">
        <div style="display: inline-block; width: 24px; height: 24px; border: 2px solid var(--accent-primary); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; margin-bottom: 12px;"></div>
        <p>Loading Telegram chats...</p>
      </div>
    `;
  }

  close() {
    this.customCallback = null;
    if (!this.modal) return;
    this.modal.classList.remove('open');
  }

  renderList(filter = '') {
    if (!this.listContainer) return;
    const q = filter.trim().toLowerCase();

    const filtered = this.chats.filter((c) => {
      // Category filter check
      if (this.activeFilter && this.activeFilter !== 'all') {
        const cType = (c.type || '').toLowerCase();
        if (this.activeFilter === 'channel' && cType !== 'channel') return false;
        if (this.activeFilter === 'group' && !cType.includes('group') && cType !== 'megagroup') return false;
        if (this.activeFilter === 'bot' && cType !== 'bot') return false;
        if (this.activeFilter === 'user' && cType !== 'user' && cType !== 'saved_messages') return false;
      }

      if (!q) return true;
      const nameMatch = (c.name || '').toLowerCase().includes(q);
      const userMatch = c.username ? c.username.toLowerCase().includes(q) : false;
      return nameMatch || userMatch;
    });

    if (filtered.length === 0) {
      this.listContainer.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--text-muted);">
          No ${this.activeFilter !== 'all' ? this.activeFilter + 's' : 'chats'} found matching "${escapeHtml(filter)}"
        </div>
      `;
      return;
    }

    this.listContainer.innerHTML = filtered.map((c) => {
      const isSelected = this.selectedChat && this.selectedChat.id === c.id;
      const initial = (c.name || 'C').charAt(0).toUpperCase();
      const typeLabel = c.type === 'saved_messages' ? 'Cloud' : c.type.toUpperCase();

      const avatarContent = c.type === 'saved_messages'
        ? `<span style="font-size: 1.15rem;">☁️</span>`
        : `<img src="/api/chats/${encodeURIComponent(c.id)}/avatar" alt="" style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%; display: block;" onerror="this.outerHTML='<span>${escapeHtml(initial)}</span>'">`;

      return `
        <div class="chat-option-item ${isSelected ? 'selected' : ''}" data-id="${c.id}">
          <div class="chat-avatar" style="overflow: hidden; border-radius: 50%; display: flex; align-items: center; justify-content: center;">
            ${avatarContent}
          </div>
          <div class="chat-meta">
            <div class="chat-meta-name">${escapeHtml(c.name)}</div>
            <div class="chat-meta-sub">
              ${c.username ? '@' + escapeHtml(c.username) + ' • ' : ''}
              <span class="chat-type-tag">${escapeHtml(typeLabel)}</span>
            </div>
          </div>
          ${isSelected ? '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#00cec9" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>' : ''}
        </div>
      `;
    }).join('');

    // Attach click events
    this.listContainer.querySelectorAll('.chat-option-item').forEach((el) => {
      el.addEventListener('click', () => {
        const rawId = el.getAttribute('data-id');
        const chat = this.chats.find((c) => String(c.id) === String(rawId));
        if (chat) {
          if (this.customCallback) {
            const cb = this.customCallback;
            this.customCallback = null;
            cb(chat);
          } else {
            this.saveSelection(chat);
          }
          this.close();
        }
      });
    });
  }

  getSelectedChat() {
    return this.selectedChat;
  }
}

export const chatPicker = new ChatPicker();
