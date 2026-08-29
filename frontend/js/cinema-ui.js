import { showToast } from './ui.js';
import { formatBytes, escapeHtml, cleanFileName } from './utils.js';
import { chatPicker } from './chat-picker.js';

let _cinemaVideos = [];
let _currentChatId = 'me';

export async function initCinema() {
  const btnChooseChat = document.getElementById('btnCinemaChooseChat');
  const videoSearch = document.getElementById('cinemaSearchInput');
  const btnRefresh = document.getElementById('btnCinemaRefresh');

  // 1. Destination Bar & Watched Channels Setup
  _initCinemaDestinationPicker();
  await _loadCinemaWatchedChips();

  // Change Destination button -> Opens Telegram Chat Modal
  if (btnChooseChat) {
    btnChooseChat.addEventListener('click', () => {
      chatPicker.open((selectedChat) => {
        _onCinemaChatSelected(selectedChat);
      });
    });
  }

  // Refresh
  if (btnRefresh) {
    btnRefresh.addEventListener('click', () => loadCinemaVideos(_currentChatId));
  }

  // Search Filter
  if (videoSearch) {
    videoSearch.addEventListener('input', (e) => {
      _filterCinemaGrid(e.target.value.toLowerCase().trim());
    });
  }

  // Initial Load
  loadCinemaVideos('me');
}

function _initCinemaDestinationPicker() {
  const nameEl = document.getElementById('cinemaCurrentChatName');
  const typeEl = document.getElementById('cinemaCurrentChatType');
  const iconEl = document.getElementById('cinemaCurrentChatIcon');

  if (nameEl) nameEl.textContent = 'Saved Messages (Personal Cloud)';
  if (typeEl) {
    typeEl.textContent = 'CLOUD';
    typeEl.className = 'chat-type-tag';
  }
  if (iconEl) iconEl.textContent = '☁️';
}

function _onCinemaChatSelected(chat) {
  _currentChatId = chat.id;
  const nameEl = document.getElementById('cinemaCurrentChatName');
  const typeEl = document.getElementById('cinemaCurrentChatType');
  const iconEl = document.getElementById('cinemaCurrentChatIcon');

  if (nameEl) nameEl.textContent = chat.name;
  if (typeEl) {
    typeEl.textContent = (chat.type || 'chat').toUpperCase();
    typeEl.className = `chat-type-tag type-${chat.type || 'chat'}`;
  }
  if (iconEl) {
    const icons = { saved_messages: '☁️', user: '👤', channel: '📢', supergroup: '👥', group: '👥', bot: '🤖' };
    iconEl.textContent = icons[chat.type] || '💬';
  }

  // Update active chip highlight
  document.querySelectorAll('.cinema-chip').forEach((c) => {
    const chipId = c.getAttribute('data-chat-id');
    c.classList.toggle('active', chipId == chat.id);
  });

  showToast(`🎬 Loaded archive: "${chat.name}"`, 'info');
  loadCinemaVideos(_currentChatId);
}

async function _loadCinemaWatchedChips() {
  const chipsContainer = document.getElementById('cinemaWatchedChips');
  if (!chipsContainer) return;

  chipsContainer.innerHTML = '';

  // 1. Saved Messages Chip
  const savedChip = document.createElement('button');
  savedChip.type = 'button';
  savedChip.className = 'cinema-chip active';
  savedChip.setAttribute('data-chat-id', 'me');
  savedChip.innerHTML = `<span>☁️</span><span>Saved Messages</span>`;
  savedChip.addEventListener('click', () => {
    _onCinemaChatSelected({ id: 'me', name: 'Saved Messages (Personal Cloud)', type: 'saved_messages' });
  });
  chipsContainer.appendChild(savedChip);

  // 2. Fetch watched channels from sniffer status
  try {
    const resp = await fetch('/api/sniffer/status');
    const data = await resp.json();
    const channels = data.watched_channels || [];

    channels.forEach((ch) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'cinema-chip';
      chip.setAttribute('data-chat-id', ch.id);
      const icon = ch.type === 'channel' ? '📢' : (ch.type === 'supergroup' || ch.type === 'group' ? '👥' : '💬');
      chip.innerHTML = `<span>${icon}</span><span>${escapeHtml(ch.name)}</span>`;
      chip.addEventListener('click', () => {
        _onCinemaChatSelected({ id: ch.id, name: ch.name, type: ch.type });
      });
      chipsContainer.appendChild(chip);
    });
  } catch (err) {
    console.debug('Could not load watched channels for chips:', err);
  }
}

export async function loadCinemaVideos(chatId = 'me') {
  _currentChatId = chatId;
  const grid = document.getElementById('cinemaVideoGrid');
  const countBadge = document.getElementById('cinemaVideoCount');

  if (grid) {
    grid.innerHTML = `
      <div class="cinema-loading">
        <div class="spinner" style="margin-bottom: 12px;"></div>
        <p style="font-size: 0.9rem; color: var(--text-muted);">Fetching video files from Telegram MTProto...</p>
      </div>
    `;
  }

  try {
    const resp = await fetch(`/api/media/videos/${encodeURIComponent(chatId)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    _cinemaVideos = data.videos || [];

    if (countBadge) {
      countBadge.textContent = `${_cinemaVideos.length} Videos`;
    }

    renderCinemaGrid(_cinemaVideos);
  } catch (err) {
    if (grid) {
      grid.innerHTML = `
        <div class="cinema-empty">
          <span style="font-size: 2.2rem; margin-bottom: 8px;">⚠️</span>
          <p style="font-size: 0.95rem; font-weight: 600; color: var(--text-main);">Could not load video archive</p>
          <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">${escapeHtml(err.message)}</p>
          <button class="btn-secondary" style="margin-top: 14px; padding: 6px 14px; font-size: 0.8rem;" onclick="window._reloadCinema()">
            🔄 Try Again
          </button>
        </div>
      `;
    }
  }
}

window._reloadCinema = () => loadCinemaVideos(_currentChatId);

function _detectSeriesInfo(filename) {
  const clean = cleanFileName(filename) || filename;
  // Match S01E01, S1E1, S01 EP01, Season 1 Episode 2, EP01, Part 1, etc.
  const regex = /^(.*?)(?:[\s._\-\(\[]+)(?:(s\d{1,2}|season\s*\d{1,2})[\s._\-\]\)]*)?(?:(e\d{1,3}|ep\s*\d{1,3}|episode\s*\d{1,3}|part\s*\d{1,2}))(.*)$/i;
  const m = clean.match(regex);
  if (m) {
    let sName = (m[1] || '').replace(/\.\w+$/, '').replace(/[._\-\(\)]+$/, '').trim();
    if (!sName) sName = clean.replace(/\.\w+$/, '').trim();
    const sSeason = (m[2] || 'S01').toUpperCase().replace(/\s+/g, ' ');
    const sEp = (m[3] || 'EP01').toUpperCase().replace(/\s+/g, ' ');
    return {
      isSeries: true,
      seriesName: sName,
      seasonLabel: sSeason.startsWith('S') && !sSeason.includes('EASON') ? `Season ${parseInt(sSeason.slice(1)) || 1}` : sSeason,
      epLabel: sEp,
      cleanTitle: clean
    };
  }
  return { isSeries: false, cleanTitle: clean };
}

export function renderCinemaGrid(videos) {
  const grid = document.getElementById('cinemaVideoGrid');
  if (!grid) return;

  grid.innerHTML = '';

  if (videos.length === 0) {
    grid.innerHTML = `
      <div class="cinema-empty">
        <span style="font-size: 2.4rem; margin-bottom: 8px;">🎬</span>
        <p style="font-size: 1rem; font-weight: 600; color: var(--text-main);">No video files found</p>
        <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">This channel does not contain any video documents or movies</p>
      </div>
    `;
    return;
  }

  // 1. Group TV series into JioHotstar Season Bundles
  const seriesMap = new Map();
  const standaloneList = [];

  videos.forEach((v) => {
    const sInfo = _detectSeriesInfo(v.filename);
    if (sInfo.isSeries) {
      const groupKey = `${sInfo.seriesName}__${sInfo.seasonLabel}`.toLowerCase();
      if (!seriesMap.has(groupKey)) {
        seriesMap.set(groupKey, {
          type: 'series',
          seriesName: sInfo.seriesName,
          seasonLabel: sInfo.seasonLabel,
          episodes: []
        });
      }
      seriesMap.get(groupKey).episodes.push({ ...v, ...sInfo });
    } else {
      standaloneList.push({ ...v, ...sInfo });
    }
  });

  // 2. Render JioHotstar Series Bundles (for groups with >1 episode)
  seriesMap.forEach((seriesGroup) => {
    if (seriesGroup.episodes.length > 1) {
      seriesGroup.episodes.sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true }));
      const totalBytes = seriesGroup.episodes.reduce((acc, curr) => acc + (curr.file_size || 0), 0);
      const leadThumb = seriesGroup.episodes.find((e) => e.has_thumb)?.thumb_url;

      const seriesCard = document.createElement('div');
      seriesCard.className = 'hotstar-series-card';
      seriesCard.innerHTML = `
        <div class="hotstar-series-hero">
          <div class="hotstar-series-poster">
            ${leadThumb
              ? `<img src="${leadThumb}" alt="${escapeHtml(seriesGroup.seriesName)}" loading="lazy">`
              : `<div class="cinema-hub-thumb-fallback" style="font-size: 1.8rem;">🎬</div>`
            }
          </div>
          <div class="hotstar-series-info">
            <div class="hotstar-series-tags">
              <span class="hotstar-pill">HOTSTAR BUNDLE</span>
              <span class="hotstar-pill" style="background: rgba(0, 206, 201, 0.15); color: var(--accent-secondary); border-color: rgba(0, 206, 201, 0.4);">${escapeHtml(seriesGroup.seasonLabel)}</span>
              <span class="hotstar-pill" style="background: rgba(255, 121, 63, 0.15); color: #ff793f; border-color: rgba(255, 121, 63, 0.4);">${seriesGroup.episodes.length} Episodes</span>
              <span style="font-size: 0.72rem; color: var(--text-muted); margin-left: 4px;">Total: ${formatBytes(totalBytes)}</span>
            </div>
            <h3 class="hotstar-series-title">${escapeHtml(seriesGroup.seriesName)}</h3>
            <p class="hotstar-series-sub">Continuous MTProto Turbo Streaming with instant keyframe seeking</p>
            <div class="hotstar-series-actions">
              <button class="hotstar-btn-binge btn-binge-all" type="button" title="Play entire season sequentially in VLC">
                <span>▶</span><span>Binge Season in VLC</span>
              </button>
              <button class="btn-secondary btn-binge-playlist" type="button" style="padding: 7px 12px; font-size: 0.8rem;" title="Download complete Season .m3u playlist">
                <span>📥</span><span>Season Playlist</span>
              </button>
            </div>
          </div>
        </div>
        <div class="hotstar-episode-drawer">
          <div class="hotstar-episode-track">
            ${seriesGroup.episodes.map((ep, epIdx) => {
              const dur = ep.duration ? _formatDuration(ep.duration) : '';
              return `
                <div class="hotstar-ep-card" data-ep-idx="${epIdx}">
                  <div class="hotstar-ep-thumb" title="Click to stream in VLC">
                    ${ep.has_thumb
                      ? `<img src="${ep.thumb_url}" alt="${escapeHtml(ep.cleanTitle)}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'cinema-hub-thumb-fallback\\'>🎬</div>'">`
                      : `<div class="cinema-hub-thumb-fallback">🎬</div>`
                    }
                    <span class="hotstar-ep-badge">${escapeHtml(ep.epLabel)}</span>
                    ${dur ? `<span class="video-duration-pill">${dur}</span>` : ''}
                  </div>
                  <div class="hotstar-ep-meta">
                    <span class="hotstar-ep-title" title="${escapeHtml(ep.cleanTitle)}">${escapeHtml(ep.cleanTitle)}</span>
                    <span style="font-size: 0.7rem; color: var(--text-muted);">${formatBytes(ep.file_size)}</span>
                  </div>
                  <div class="hotstar-ep-actions">
                    <button class="btn-primary btn-ep-vlc" type="button" style="flex: 1; padding: 4px 6px; font-size: 0.72rem; background: linear-gradient(135deg, #ff793f 0%, #e55039 100%); border-color: rgba(255, 121, 63, 0.4);">
                      <span>🎬</span><span>VLC</span>
                    </button>
                    <button class="btn-secondary btn-ep-fdm" type="button" style="flex: 0.9; padding: 4px 6px; font-size: 0.72rem;">
                      <span>🚀</span><span>FDM</span>
                    </button>
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;

      // Binge Season Playback in VLC
      const bingeBtn = seriesCard.querySelector('.btn-binge-all');
      if (bingeBtn) {
        bingeBtn.addEventListener('click', () => {
          showToast(`🎬 Launching VLC playlist for ${seriesGroup.episodes.length} episodes of "${seriesGroup.seriesName}"...`, 'info');
          fetch('/api/media/vlc/play_batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              title: `${seriesGroup.seriesName} - ${seriesGroup.seasonLabel}`,
              items: seriesGroup.episodes
            })
          }).then((r) => r.json()).then((res) => {
            if (res.launched) {
              showToast(`🍿 Binging ${seriesGroup.seriesName} (${seriesGroup.episodes.length} episodes) in VLC!`, 'success');
            }
          }).catch((err) => showToast(`Playback error: ${err.message}`, 'error'));
        });
      }

      // Download Complete Season M3U
      const playlistBtn = seriesCard.querySelector('.btn-binge-playlist');
      if (playlistBtn) {
        playlistBtn.addEventListener('click', async () => {
          try {
            const res = await fetch('/api/media/vlc/batch_playlist', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                title: `${seriesGroup.seriesName}_${seriesGroup.seasonLabel}`,
                items: seriesGroup.episodes
              })
            });
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${seriesGroup.seriesName}_${seriesGroup.seasonLabel}.m3u`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            showToast(`📥 Downloaded ${seriesGroup.seriesName} season playlist`, 'success');
          } catch (e) {
            showToast(`Error creating playlist: ${e.message}`, 'error');
          }
        });
      }

      // Episode Cards Actions
      seriesCard.querySelectorAll('.hotstar-ep-card').forEach((epCard, idx) => {
        const ep = seriesGroup.episodes[idx];
        const epThumb = epCard.querySelector('.hotstar-ep-thumb');
        const epVlc = epCard.querySelector('.btn-ep-vlc');
        const epFdm = epCard.querySelector('.btn-ep-fdm');
        if (epThumb) epThumb.addEventListener('click', () => playInVlc(ep));
        if (epVlc) epVlc.addEventListener('click', () => playInVlc(ep));
        if (epFdm) epFdm.addEventListener('click', () => triggerFdm(ep));
      });

      grid.appendChild(seriesCard);
    } else {
      standaloneList.push(...seriesGroup.episodes);
    }
  });

  // 3. Render Standalone Movies & Videos
  standaloneList.forEach((v, idx) => {
    const card = document.createElement('div');
    card.className = 'cinema-hub-card';
    card.id = `videoCard_${idx}`;

    const cleanTitle = cleanFileName(v.filename) || v.filename;
    const durationStr = v.duration ? _formatDuration(v.duration) : '';
    const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);

    card.innerHTML = `
      <div class="cinema-hub-thumb" title="Click to stream in VLC Player">
        ${v.has_thumb
          ? `<img src="${v.thumb_url}" class="video-thumb-img" alt="${escapeHtml(cleanTitle)}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'cinema-hub-thumb-fallback\\'>🎬</div>'">`
          : `<div class="cinema-hub-thumb-fallback">🎬</div>`
        }
        ${durationStr ? `<span class="video-duration-pill">${durationStr}</span>` : ''}
      </div>
      <div class="cinema-hub-meta">
        <span class="cinema-hub-title" title="${escapeHtml(cleanTitle)}">${escapeHtml(cleanTitle)}</span>
        <div class="cinema-hub-sub">
          <span>${formatBytes(v.file_size)}</span>
          <span>•</span>
          <span style="color: ${isMkv ? '#ff793f' : 'var(--accent-secondary)'}; font-weight: 600;">${isMkv ? 'MKV' : 'MP4'}</span>
          <span>•</span>
          <span>${v.date ? new Date(v.date * 1000).toLocaleDateString() : 'Cloud'}</span>
        </div>
      </div>
      <div class="cinema-hub-card-actions">
        <button class="btn-primary btn-card-vlc" type="button" title="Stream in VLC Media Player" style="flex: 1.2; padding: 6px 10px; font-size: 0.78rem; background: linear-gradient(135deg, #ff793f 0%, #e55039 100%); border-color: rgba(255, 121, 63, 0.4);">
          <span>🎬</span><span>VLC</span>
        </button>
        <button class="btn-secondary btn-card-fdm" type="button" title="Push to Free Download Manager" style="flex: 1; padding: 6px 8px; font-size: 0.78rem;">
          <span>🚀</span><span>FDM</span>
        </button>
        <button class="btn-secondary btn-card-copy" type="button" title="Copy HTTP Stream Link" style="flex: 0.8; padding: 6px 8px; font-size: 0.78rem;">
          <span>📋</span>
        </button>
        <button class="btn-secondary btn-card-m3u" type="button" title="Download VLC .m3u Playlist" style="flex: 0.8; padding: 6px 8px; font-size: 0.78rem;">
          <span>📥</span>
        </button>
      </div>
    `;

    // 1. Click thumbnail or VLC button -> Launches VLC
    const thumbEl = card.querySelector('.cinema-hub-thumb');
    const vlcBtn = card.querySelector('.btn-card-vlc');
    if (thumbEl) thumbEl.addEventListener('click', () => playInVlc(v));
    if (vlcBtn) vlcBtn.addEventListener('click', () => playInVlc(v));

    // 2. Click FDM button -> Push to Download Manager
    const fdmBtn = card.querySelector('.btn-card-fdm');
    if (fdmBtn) {
      fdmBtn.addEventListener('click', () => triggerFdm(v));
    }

    // 3. Click Copy button -> Copy Stream URL
    const copyBtn = card.querySelector('.btn-card-copy');
    if (copyBtn) {
      copyBtn.addEventListener('click', () => {
        const fullUrl = `${window.location.origin}${v.stream_url}`;
        navigator.clipboard.writeText(fullUrl).then(() => {
          showToast('📋 Stream URL copied to clipboard!', 'success');
        });
      });
    }

    // 4. Click M3U button -> Download Playlist
    const m3uBtn = card.querySelector('.btn-card-m3u');
    if (m3uBtn) {
      m3uBtn.addEventListener('click', () => {
        const url = `/api/media/vlc/playlist/${encodeURIComponent(v.chat_id)}/${v.message_id}/${encodeURIComponent(v.filename)}.m3u`;
        const a = document.createElement('a');
        a.href = url;
        a.download = `${v.filename}.m3u`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        showToast('📥 Downloaded VLC .m3u playlist', 'success');
      });
    }

    grid.appendChild(card);
  });
}

function _filterCinemaGrid(query) {
  if (!query) {
    renderCinemaGrid(_cinemaVideos);
    return;
  }
  const filtered = _cinemaVideos.filter((v) => v.filename.toLowerCase().includes(query));
  renderCinemaGrid(filtered);
}

export async function playInVlc(v, playerType = 'auto') {
  const isMpv = playerType === 'mpv';
  showToast(`🎬 Launching ${isMpv ? 'MPV' : 'VLC'} for "${v.filename}"...`, 'info');
  try {
    const resp = await fetch('/api/media/vlc/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: v.chat_id,
        message_id: v.message_id,
        filename: v.filename,
        stream_url: v.stream_url,
        player: playerType
      })
    });
    const data = await resp.json();
    if (data.launched) {
      showToast(`🎬 Media Player streaming "${v.filename}"!`, 'success');
    } else if (data.playlist_url) {
      const a = document.createElement('a');
      a.href = data.playlist_url;
      a.download = `${v.filename}.m3u`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      showToast('🎬 Opening stream via .m3u playlist...', 'success');
    }
  } catch (e) {
    showToast(`Player launch notice: ${e.message}`, 'warning');
  }
}

export async function triggerFdm(v) {
  try {
    const resp = await fetch(`/api/proxy/trigger?chat_id=${encodeURIComponent(v.chat_id)}&message_id=${v.message_id}`, { method: 'POST' });
    const data = await resp.json();
    if (data.success) {
      showToast(`🚀 Dispatched "${v.filename}" to ${data.manager.toUpperCase()}`, 'success');
    } else {
      showToast(`⚠️ Could not auto-launch manager. Copied stream link.`, 'warning');
    }
  } catch (err) {
    showToast(`Download trigger error: ${err.message}`, 'error');
  }
}

function _formatDuration(seconds) {
  if (!seconds || isNaN(seconds) || seconds <= 0) return '00:00';
  const sec = Math.floor(seconds);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}
