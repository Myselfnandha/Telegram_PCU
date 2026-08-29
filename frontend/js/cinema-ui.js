import { showToast } from './ui.js';
import { formatBytes, escapeHtml, cleanFileName } from './utils.js';
import { chatPicker } from './chat-picker.js';

let _cinemaVideos = [];
let _currentChatId = 'me';
let _currentChatName = 'Saved Messages (Personal Cloud)';

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
  _currentChatName = chat.name || 'Chat';
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

function _attachHoverScrubber(containerEl, videoItem) {
  if (!containerEl || !videoItem) return;

  let hoverTimer = null;
  let currentFrame = 0;
  const totalFrames = 5;

  containerEl.addEventListener('mouseenter', () => {
    let imgEl = containerEl.querySelector('img');
    const originalSrc = imgEl ? imgEl.src : null;

    currentFrame = 0;
    hoverTimer = setInterval(() => {
      currentFrame = (currentFrame + 1) % totalFrames;
      const previewUrl = `/api/media/preview/${encodeURIComponent(videoItem.chat_id)}/${videoItem.message_id}/${currentFrame}`;
      const nextImg = new Image();
      nextImg.onload = () => {
        if (hoverTimer) {
          if (!imgEl) {
            const fallback = containerEl.querySelector('.cinema-hub-thumb-fallback, .series-thumb-fallback');
            if (fallback) fallback.style.display = 'none';
            imgEl = document.createElement('img');
            imgEl.className = 'video-thumb-img';
            imgEl.alt = '';
            containerEl.prepend(imgEl);
          }
          imgEl.src = previewUrl;
          imgEl.style.display = 'block';
        }
      };
      nextImg.src = previewUrl;
    }, 420);

    const onLeave = () => {
      if (hoverTimer) {
        clearInterval(hoverTimer);
        hoverTimer = null;
      }
      containerEl.removeEventListener('mouseleave', onLeave);
      if (imgEl && originalSrc) {
        imgEl.src = originalSrc;
      }
    };
    containerEl.addEventListener('mouseleave', onLeave);
  });
}

function _extractSeriesInfo(filename) {
  const channelContext = _currentChatName ? { name: _currentChatName, username: _currentChatName } : null;
  const clean = cleanFileName(filename, channelContext) || filename;

  // Match Season and Episode patterns:
  // e.g. S01E01, S1E1, S01 EP01, Season 1 Episode 2, EP01, Episode 01, Part 1
  const regex = /^(.*?)(?:[\s._\-\(\[]+)(?:(s\d{1,2}|season\s*\d{1,2})[\s._\-\]\)]*)?(?:(e\d{1,3}|ep\s*\d{1,3}|episode\s*\d{1,3}|part\s*\d{1,2}))(.*)$/i;
  const m = clean.match(regex);
  if (m) {
    let rawName = (m[1] || '').replace(/\.\w+$/, '').replace(/[._\-\(\)]+$/, '').trim();
    if (!rawName) rawName = clean.replace(/\.\w+$/, '').trim();

    // Canonical title: strip channel noise, domain signatures, watermarks, year, and empty brackets
    let canonical = cleanFileName(rawName, channelContext)
      .replace(/\b(19\d\d|20\d\d)\b/g, ' ')
      .replace(/[\(\)\[\]\{\}]/g, ' ')
      .replace(/^(?:org|com|net|tv|hd|link|linkzz|official)\s+/i, '')
      .replace(/\s+/g, ' ')
      .trim();

    if (!canonical) canonical = 'TV Series';

    const sSeason = (m[2] || 'S01').toUpperCase().replace(/\s+/g, ' ');
    const sEp = (m[3] || 'EP01').toUpperCase().replace(/\s+/g, ' ');
    const seasonNum = parseInt(sSeason.replace(/\D/g, '')) || 1;
    const seasonLabel = `Season ${seasonNum}`;

    const cleanedTitle = clean.replace(/\(\s*\)/g, '').replace(/\[\s*\]/g, '').replace(/\s+/g, ' ').trim();

    return {
      isSeries: true,
      canonicalTitle: canonical,
      seasonNum: seasonNum,
      seasonLabel: seasonLabel,
      epLabel: sEp,
      cleanTitle: cleanedTitle
    };
  }
  const cleanedTitle = clean.replace(/\(\s*\)/g, '').replace(/\[\s*\]/g, '').replace(/\s+/g, ' ').trim();
  return { isSeries: false, cleanTitle: cleanedTitle };
}

export function renderCinemaGrid(videos) {
  const moviesGrid = document.getElementById('cinemaVideoGrid');
  const seriesPane = document.getElementById('cinemaSeriesPane');
  const seriesList = document.getElementById('cinemaSeriesList');
  const moviesCountBadge = document.getElementById('cinemaMoviesCount');
  const seriesCountBadge = document.getElementById('cinemaSeriesCount');

  if (!moviesGrid) return;

  moviesGrid.innerHTML = '';
  if (seriesList) seriesList.innerHTML = '';

  if (videos.length === 0) {
    if (seriesPane) seriesPane.classList.add('hidden');
    moviesGrid.innerHTML = `
      <div class="cinema-empty">
        <span style="font-size: 2.4rem; margin-bottom: 8px;">🎬</span>
        <p style="font-size: 1rem; font-weight: 600; color: var(--text-main);">No video files found</p>
        <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">This channel does not contain any video documents or movies</p>
      </div>
    `;
    if (moviesCountBadge) moviesCountBadge.textContent = '0 Movies';
    if (seriesCountBadge) seriesCountBadge.textContent = '0 Series';
    return;
  }

  // 1. Group TV series into Unified Show Bundles
  const rawShowsMap = new Map();
  const standaloneList = [];
  const channelContext = _currentChatName ? { name: _currentChatName, username: _currentChatName } : null;

  videos.forEach((v) => {
    const sInfo = _extractSeriesInfo(v.filename);
    if (sInfo.isSeries) {
      const key = sInfo.canonicalTitle.toLowerCase();
      if (!rawShowsMap.has(key)) {
        rawShowsMap.set(key, {
          canonicalTitle: sInfo.canonicalTitle,
          episodes: []
        });
      }
      rawShowsMap.get(key).episodes.push({ ...v, ...sInfo });
    } else {
      standaloneList.push({ ...v, ...sInfo });
    }
  });

  // 2. Fuzzy merge shows sharing substring titles (e.g. "Lost in Space" vs "Space")
  const mergedShowsMap = new Map();
  const rawKeys = Array.from(rawShowsMap.keys()).sort((a, b) => b.length - a.length);

  rawKeys.forEach((key) => {
    const showData = rawShowsMap.get(key);
    if (!showData) return;

    let mergedIntoExisting = false;
    for (const [existingKey, existingData] of mergedShowsMap.entries()) {
      if (existingKey === key || existingKey.includes(key) || key.includes(existingKey)) {
        existingData.episodes.push(...showData.episodes);
        if (showData.canonicalTitle.length > existingData.canonicalTitle.length) {
          existingData.canonicalTitle = showData.canonicalTitle;
        }
        mergedIntoExisting = true;
        break;
      }
    }

    if (!mergedIntoExisting) {
      mergedShowsMap.set(key, showData);
    }
  });

  // 3. Build Unified Shows with Multi-Season Maps
  const validShows = [];
  mergedShowsMap.forEach((show) => {
    if (show.episodes.length > 1) {
      const seasonsMap = new Map();
      show.episodes.forEach((ep) => {
        const sLabel = ep.seasonLabel || 'Season 1';
        if (!seasonsMap.has(sLabel)) {
          seasonsMap.set(sLabel, []);
        }
        seasonsMap.get(sLabel).push(ep);
      });

      // Sort episodes inside each season numerically
      seasonsMap.forEach((eps) => {
        eps.sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true }));
      });

      // Sort seasons numerically (Season 1, Season 2, Season 3...)
      const sortedSeasonKeys = Array.from(seasonsMap.keys()).sort((a, b) => {
        const numA = parseInt(a.replace(/\D/g, '')) || 1;
        const numB = parseInt(b.replace(/\D/g, '')) || 1;
        return numA - numB;
      });

      const totalBytes = show.episodes.reduce((acc, curr) => acc + (curr.file_size || 0), 0);
      const leadThumb = show.episodes.find((e) => e.has_thumb)?.thumb_url;

      validShows.push({
        showTitle: show.canonicalTitle,
        totalEpisodes: show.episodes.length,
        totalBytes: totalBytes,
        leadThumb: leadThumb,
        seasonsMap: seasonsMap,
        seasonKeys: sortedSeasonKeys,
        activeSeason: sortedSeasonKeys[0] || 'Season 1'
      });
    } else {
      standaloneList.push(...show.episodes);
    }
  });

  // Update Section Counters
  if (moviesCountBadge) moviesCountBadge.textContent = `${standaloneList.length} Movies`;
  if (seriesCountBadge) seriesCountBadge.textContent = `${validShows.length} Shows`;

  // 4. Right Pane: Render Unified Show Showcase Cards
  if (seriesPane && seriesList) {
    if (validShows.length === 0) {
      seriesPane.classList.add('hidden');
    } else {
      seriesPane.classList.remove('hidden');
      validShows.forEach((show, showIdx) => {
        let currentSeason = show.activeSeason;

        const showCard = document.createElement('div');
        showCard.className = 'series-showcase-card';

        const hasMultipleSeasons = show.seasonKeys.length > 1;

        showCard.innerHTML = `
          <div class="series-showcase-hero">
            <div class="series-showcase-poster">
              ${show.leadThumb
                ? `<img src="${show.leadThumb}" class="series-poster-img" alt="" loading="lazy" onerror="this.outerHTML='<div class=\\'series-thumb-fallback\\'>🎬</div>'">`
                : `<div class="series-thumb-fallback">🎬</div>`
              }
            </div>
            <div class="series-showcase-info">
              <div class="series-showcase-tags">
                <span class="series-tag-pill active-season-tag" style="background: rgba(0, 206, 201, 0.15); color: var(--accent-secondary); border: 1px solid rgba(0, 206, 201, 0.4);">${escapeHtml(currentSeason)}</span>
                ${hasMultipleSeasons ? `<span class="series-tag-pill" style="background: rgba(108, 92, 231, 0.2); color: var(--accent-primary); border: 1px solid rgba(108, 92, 231, 0.4);">${show.seasonKeys.length} Seasons</span>` : ''}
                <span class="series-tag-pill" style="background: rgba(255, 121, 63, 0.15); color: #ff793f; border: 1px solid rgba(255, 121, 63, 0.4);">${show.totalEpisodes} Episodes Total</span>
                <span style="font-size: 0.68rem; color: var(--text-muted);">${formatBytes(show.totalBytes)}</span>
              </div>
              <h4 class="series-showcase-title" title="${escapeHtml(show.showTitle)}">${escapeHtml(show.showTitle)}</h4>
              <div class="series-showcase-actions">
                <button class="series-btn-binge btn-binge-all" type="button" title="Play full active season sequentially in VLC">
                  <span>▶</span><span class="binge-btn-label">Binge ${escapeHtml(currentSeason)}</span>
                </button>
                <button class="btn-secondary btn-binge-playlist" type="button" style="padding: 5px 9px; font-size: 0.74rem;" title="Download Season .m3u playlist">
                  <span>📥</span><span>Playlist</span>
                </button>
                <button class="btn-secondary btn-toggle-episodes" type="button" style="padding: 5px 9px; font-size: 0.74rem; margin-left: auto;">
                  <span class="ep-toggle-label">▾ Episodes</span>
                </button>
              </div>
            </div>
          </div>

          ${hasMultipleSeasons ? `
            <div class="series-season-tabs">
              ${show.seasonKeys.map((sKey) => {
                const sEps = show.seasonsMap.get(sKey) || [];
                const isActive = sKey === currentSeason;
                return `
                  <button class="series-season-tab ${isActive ? 'active' : ''}" type="button" data-season="${escapeHtml(sKey)}">
                    ${escapeHtml(sKey)} (${sEps.length} EPs)
                  </button>
                `;
              }).join('')}
            </div>
          ` : ''}

          <div class="series-episode-drawer">
            <div class="series-episode-track" id="episodeTrack_${showIdx}">
              <!-- Rendered via updateSeasonTrack -->
            </div>
          </div>
        `;

        // Function to update the episode track for a chosen season
        const updateSeasonTrack = (seasonName) => {
          currentSeason = seasonName;
          const track = showCard.querySelector(`#episodeTrack_${showIdx}`);
          const seasonTag = showCard.querySelector('.active-season-tag');
          const bingeLabel = showCard.querySelector('.binge-btn-label');

          if (seasonTag) seasonTag.textContent = seasonName;
          if (bingeLabel) bingeLabel.textContent = `Binge ${seasonName}`;

          const episodes = show.seasonsMap.get(seasonName) || [];
          if (track) {
            track.innerHTML = episodes.map((ep, epIdx) => {
              const dur = ep.duration ? _formatDuration(ep.duration) : '';
              return `
                <div class="series-ep-card" data-ep-idx="${epIdx}">
                  <div class="series-ep-thumb" title="Hover to preview frames, Click to stream in VLC">
                    ${ep.has_thumb
                      ? `<img src="${ep.thumb_url}" class="ep-thumb-img" alt="" loading="lazy" onerror="this.outerHTML='<div class=\\'series-thumb-fallback\\'>🎬</div>'">`
                      : `<div class="series-thumb-fallback">🎬</div>`
                    }
                    <span class="series-ep-badge">${escapeHtml(ep.epLabel)}</span>
                    ${dur ? `<span class="video-duration-pill">${dur}</span>` : ''}
                  </div>
                  <div class="series-ep-meta">
                    <span class="series-ep-title" title="${escapeHtml(ep.cleanTitle)}">${escapeHtml(ep.cleanTitle)}</span>
                    <span style="font-size: 0.68rem; color: var(--text-muted);">${formatBytes(ep.file_size)}</span>
                  </div>
                  <div class="series-ep-actions">
                    <button class="btn-primary btn-ep-vlc" type="button" style="flex: 1; padding: 4px 6px; font-size: 0.7rem; background: linear-gradient(135deg, #ff793f 0%, #e55039 100%); border-color: rgba(255, 121, 63, 0.4);">
                      <span>🎬</span><span>VLC</span>
                    </button>
                    <button class="btn-secondary btn-ep-fdm" type="button" style="flex: 0.85; padding: 4px 6px; font-size: 0.7rem;">
                      <span>🚀</span><span>FDM</span>
                    </button>
                  </div>
                </div>
              `;
            }).join('');

            // Attach Episode Actions & Hover Scrubber
            track.querySelectorAll('.series-ep-card').forEach((epCard, idx) => {
              const ep = episodes[idx];
              const epThumb = epCard.querySelector('.series-ep-thumb');
              const epVlc = epCard.querySelector('.btn-ep-vlc');
              const epFdm = epCard.querySelector('.btn-ep-fdm');
              if (epThumb) _attachHoverScrubber(epThumb, ep);
              if (epThumb) epThumb.addEventListener('click', () => playInVlc(ep, 'auto', epCard));
              if (epVlc) epVlc.addEventListener('click', () => playInVlc(ep, 'auto', epCard));
              if (epFdm) epFdm.addEventListener('click', () => triggerFdm(ep));
            });
          }
        };

        // Initial Episode Track rendering
        updateSeasonTrack(currentSeason);

        // Season Tab Switchers
        showCard.querySelectorAll('.series-season-tab').forEach((tabBtn) => {
          tabBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const sName = tabBtn.getAttribute('data-season');
            showCard.querySelectorAll('.series-season-tab').forEach((t) => t.classList.remove('active'));
            tabBtn.classList.add('active');
            updateSeasonTrack(sName);
            if (!showCard.classList.contains('expanded')) {
              showCard.classList.add('expanded');
              const lbl = showCard.querySelector('.ep-toggle-label');
              if (lbl) lbl.textContent = '▴ Hide';
            }
          });
        });

        // Toggle Episode Drawer
        const toggleBtn = showCard.querySelector('.btn-toggle-episodes');
        if (toggleBtn) {
          toggleBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            showCard.classList.toggle('expanded');
            const isExp = showCard.classList.contains('expanded');
            const lbl = toggleBtn.querySelector('.ep-toggle-label');
            if (lbl) lbl.textContent = isExp ? '▴ Hide' : '▾ Episodes';
          });
        }

        // Binge Active Season Playback in VLC (Silent Instant Launch with Micro Indicator)
        const bingeBtn = showCard.querySelector('.btn-binge-all');
        if (bingeBtn) {
          bingeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            bingeBtn.classList.add('playing-pulse');
            setTimeout(() => bingeBtn.classList.remove('playing-pulse'), 1200);

            const eps = show.seasonsMap.get(currentSeason) || [];
            fetch('/api/media/vlc/play_batch', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                title: `${show.showTitle} - ${currentSeason}`,
                items: eps
              })
            }).catch((err) => showToast(`Playback error: ${err.message}`, 'error'));
          });
        }

        // Download Complete Season M3U
        const playlistBtn = showCard.querySelector('.btn-binge-playlist');
        if (playlistBtn) {
          playlistBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const eps = show.seasonsMap.get(currentSeason) || [];
            try {
              const res = await fetch('/api/media/vlc/batch_playlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  title: `${show.showTitle}_${currentSeason}`,
                  items: eps
                })
              });
              const blob = await res.blob();
              const url = window.URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `${show.showTitle}_${currentSeason}.m3u`;
              document.body.appendChild(a);
              a.click();
              a.remove();
              showToast(`📥 Downloaded ${show.showTitle} season playlist`, 'success');
            } catch (err) {
              showToast(`Error creating playlist: ${err.message}`, 'error');
            }
          });
        }

        seriesList.appendChild(showCard);
      });
    }
  }

  // 3. Left Pane: Render Standalone Movies & Videos
  if (standaloneList.length === 0 && validSeriesGroups.length > 0) {
    moviesGrid.innerHTML = `
      <div class="cinema-empty" style="padding: 32px 12px;">
        <span style="font-size: 1.8rem; margin-bottom: 6px;">📺</span>
        <p style="font-size: 0.9rem; font-weight: 600; color: var(--text-main);">All videos are TV series</p>
        <p style="font-size: 0.78rem; color: var(--text-muted); margin-top: 2px;">Browse seasons in the TV Series tab on the right</p>
      </div>
    `;
    return;
  }

  standaloneList.forEach((v, idx) => {
    const card = document.createElement('div');
    card.className = 'cinema-hub-card';
    card.id = `videoCard_${idx}`;

    const cleanTitle = cleanFileName(v.filename) || v.filename;
    const durationStr = v.duration ? _formatDuration(v.duration) : '';
    const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);

    card.innerHTML = `
      <div class="cinema-hub-thumb" title="Hover to preview frames, Click to stream in VLC">
        ${v.has_thumb
          ? `<img src="${v.thumb_url}" class="video-thumb-img" alt="" loading="lazy" onerror="this.outerHTML='<div class=\\'cinema-hub-thumb-fallback\\'>🎬</div>'">`
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

    // 1. Hover Scrubber & Play Click
    const thumbEl = card.querySelector('.cinema-hub-thumb');
    const vlcBtn = card.querySelector('.btn-card-vlc');
    if (thumbEl) _attachHoverScrubber(thumbEl, v);
    if (thumbEl) thumbEl.addEventListener('click', () => playInVlc(v, 'auto', card));
    if (vlcBtn) vlcBtn.addEventListener('click', () => playInVlc(v, 'auto', card));

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

    moviesGrid.appendChild(card);
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

export async function playInVlc(v, playerType = 'auto', element = null) {
  if (element) {
    element.classList.add('playing-pulse');
    setTimeout(() => element.classList.remove('playing-pulse'), 1200);
  }

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
    if (!data.launched && data.playlist_url) {
      const a = document.createElement('a');
      a.href = data.playlist_url;
      a.download = `${v.filename}.m3u`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
  } catch (e) {
    showToast(`Player launch error: ${e.message}`, 'warning');
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
