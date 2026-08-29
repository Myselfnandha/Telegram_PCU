/**
 * Cinema & Real-Time Video Streaming Controller.
 * Powers the dedicated Cinema tab with Channel Media Archive browser,
 * instant HTTP Range streaming playback, search filtering, and queue auto-advance.
 */

import { showToast } from './ui.js';
import { formatBytes } from './utils.js';

let _cinemaVideos = [];
let _currentPlayingIndex = -1;
let _currentChatId = 'me';

export async function initCinema() {
  const channelSelect = document.getElementById('cinemaChannelSelect');
  const videoSearch = document.getElementById('cinemaSearchInput');
  const btnRefresh = document.getElementById('btnCinemaRefresh');
  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  const btnFdm = document.getElementById('btnCinemaFdm');
  const btnCopyStream = document.getElementById('btnCinemaCopyUrl');
  const btnTheaterMode = document.getElementById('btnCinemaTheater');
  const btnNextTrack = document.getElementById('btnCinemaNext');

  if (!channelSelect || !videoPlayer) return;

  // 1. Populate channel dropdown
  await _loadCinemaChannels();

  // Channel Change
  channelSelect.addEventListener('change', (e) => {
    _currentChatId = e.target.value;
    loadCinemaVideos(_currentChatId);
  });

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

  // Next Track / Auto-Play Next
  if (btnNextTrack) {
    btnNextTrack.addEventListener('click', _playNextVideo);
  }
  videoPlayer.addEventListener('ended', _playNextVideo);

  // Download to FDM
  if (btnFdm) {
    btnFdm.addEventListener('click', async () => {
      if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) return;
      const v = _cinemaVideos[_currentPlayingIndex];
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
    });
  }

  // Copy Stream URL
  if (btnCopyStream) {
    btnCopyStream.addEventListener('click', () => {
      if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) return;
      const v = _cinemaVideos[_currentPlayingIndex];
      const fullUrl = `${window.location.origin}${v.stream_url}`;
      navigator.clipboard.writeText(fullUrl).then(() => {
        showToast('📋 Stream URL copied to clipboard!', 'success');
      });
    });
  }

  // Theater Mode Toggle
  if (btnTheaterMode) {
    btnTheaterMode.addEventListener('click', () => {
      const cinemaContainer = document.querySelector('.cinema-workspace');
      if (cinemaContainer) {
        cinemaContainer.classList.toggle('theater-fullscreen');
        const isFullscreen = cinemaContainer.classList.contains('theater-fullscreen');
        btnTheaterMode.textContent = isFullscreen ? '🗗 Standard View' : '🖥️ Theater Mode';
      }
    });
  }

  // Keyboard Shortcuts for Video Player
  document.addEventListener('keydown', (e) => {
    // Only when Cinema tab is active and not typing in an input
    const cinemaTab = document.getElementById('tabPaneCinema');
    if (!cinemaTab || !cinemaTab.classList.contains('active')) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;

    if (e.code === 'Space') {
      e.preventDefault();
      videoPlayer.paused ? videoPlayer.play() : videoPlayer.pause();
    } else if (e.code === 'ArrowRight') {
      e.preventDefault();
      videoPlayer.currentTime = Math.min(videoPlayer.duration || 0, videoPlayer.currentTime + 5);
    } else if (e.code === 'ArrowLeft') {
      e.preventDefault();
      videoPlayer.currentTime = Math.max(0, videoPlayer.currentTime - 5);
    } else if (e.code === 'KeyM') {
      e.preventDefault();
      videoPlayer.muted = !videoPlayer.muted;
    } else if (e.code === 'KeyF') {
      e.preventDefault();
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        videoPlayer.requestFullscreen?.();
      }
    }
  });

  // Initial Load
  loadCinemaVideos('me');
}

async function _loadCinemaChannels() {
  const select = document.getElementById('cinemaChannelSelect');
  if (!select) return;

  try {
    const resp = await fetch('/api/chats');
    if (!resp.ok) return;
    const chats = await resp.json();

    select.innerHTML = '';
    chats.forEach((c) => {
      const opt = document.createElement('option');
      opt.value = c.id;
      let icon = '💬';
      if (c.type === 'saved_messages') icon = '☁️';
      else if (c.type === 'channel') icon = '📢';
      else if (c.type === 'supergroup' || c.type === 'group') icon = '👥';
      else if (c.type === 'bot') icon = '🤖';

      opt.textContent = `${icon} ${c.name}`;
      select.appendChild(opt);
    });
  } catch (err) {
    console.debug('Error loading cinema channels:', err);
  }
}

export async function loadCinemaVideos(chatId) {
  const grid = document.getElementById('cinemaVideoGrid');
  const countBadge = document.getElementById('cinemaVideoCount');
  if (!grid) return;

  grid.innerHTML = `
    <div class="cinema-loading">
      <div class="spinner" style="width: 28px; height: 28px; margin: 0 auto 12px auto;"></div>
      <span>Fetching video archive from Telegram Cloud...</span>
    </div>
  `;

  try {
    const resp = await fetch(`/api/media/videos/${encodeURIComponent(chatId)}?limit=60`);
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    }
    const data = await resp.json();
    _cinemaVideos = data.videos || [];

    if (countBadge) {
      countBadge.textContent = `${_cinemaVideos.length} Videos`;
    }

    renderCinemaGrid(_cinemaVideos);

    // Auto-select first video if player is idle
    if (_cinemaVideos.length > 0 && _currentPlayingIndex < 0) {
      selectCinemaVideo(0, false);
    }
  } catch (err) {
    grid.innerHTML = `
      <div class="cinema-empty">
        <span style="font-size: 2.2rem; margin-bottom: 8px;">⚠️</span>
        <span style="font-weight: 600; color: var(--text-main);">Could not load video archive</span>
        <span style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">${err.message}</span>
      </div>
    `;
  }
}

function renderCinemaGrid(videos) {
  const grid = document.getElementById('cinemaVideoGrid');
  if (!grid) return;

  if (videos.length === 0) {
    grid.innerHTML = `
      <div class="cinema-empty">
        <span style="font-size: 2.2rem; margin-bottom: 8px;">🎬</span>
        <span style="font-weight: 600; color: var(--text-main);">No video media found in this chat</span>
        <span style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">Upload or forward video files to watch them here instantly</span>
      </div>
    `;
    return;
  }

  grid.innerHTML = '';
  videos.forEach((v, idx) => {
    const card = document.createElement('div');
    card.className = `video-card glass-panel ${_currentPlayingIndex === idx ? 'active' : ''}`;
    card.id = `videoCard_${idx}`;

    // Resolution pill
    let resLabel = '';
    if (v.height >= 2160 || v.width >= 3840) resLabel = '4K UHD';
    else if (v.height >= 1080 || v.width >= 1920) resLabel = '1080p';
    else if (v.height >= 720 || v.width >= 1280) resLabel = '720p';
    else if (v.height > 0) resLabel = `${v.height}p`;

    const durationStr = v.duration > 0 ? _formatDuration(v.duration) : '';

    const thumbHtml = v.has_thumb
      ? `<img class="video-thumb-img" src="${v.thumb_url}" alt="Thumbnail" loading="lazy">`
      : `<div class="video-thumb-fallback">🎬</div>`;

    card.innerHTML = `
      <div class="video-thumb-container">
        ${thumbHtml}
        ${resLabel ? `<span class="video-res-pill">${resLabel}</span>` : ''}
        ${durationStr ? `<span class="video-duration-pill">${durationStr}</span>` : ''}
        <div class="video-play-overlay">▶</div>
      </div>
      <div class="video-meta">
        <span class="video-card-title" title="${v.filename}">${v.filename}</span>
        <div class="video-card-sub">
          <span>${formatBytes(v.file_size)}</span>
          <span>•</span>
          <span>${v.date ? new Date(v.date * 1000).toLocaleDateString() : 'Cloud'}</span>
        </div>
      </div>
    `;

    card.addEventListener('click', () => {
      selectCinemaVideo(idx, true);
    });

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

export function selectCinemaVideo(idx, autoPlay = true) {
  if (idx < 0 || idx >= _cinemaVideos.length) return;
  _currentPlayingIndex = idx;
  const v = _cinemaVideos[idx];

  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  const titleElem = document.getElementById('cinemaNowPlayingTitle');
  const resElem = document.getElementById('cinemaNowPlayingRes');
  const sizeElem = document.getElementById('cinemaNowPlayingSize');
  const durElem = document.getElementById('cinemaNowPlayingDur');

  if (titleElem) titleElem.textContent = v.filename;
  if (resElem) {
    let resText = 'HD Video';
    if (v.width && v.height) resText = `${v.width}x${v.height}`;
    resElem.textContent = resText;
  }
  if (sizeElem) sizeElem.textContent = formatBytes(v.file_size);
  if (durElem) durElem.textContent = v.duration > 0 ? _formatDuration(v.duration) : '--:--';

  // Highlight active card
  document.querySelectorAll('.video-card').forEach((c) => c.classList.remove('active'));
  const activeCard = document.getElementById(`videoCard_${idx}`);
  if (activeCard) activeCard.classList.add('active');

  // Load stream
  if (videoPlayer) {
    videoPlayer.src = v.stream_url;
    videoPlayer.load();
    if (autoPlay) {
      videoPlayer.play().catch((err) => {
        console.debug('Autoplay hindered:', err);
      });
    }
  }
}

function _playNextVideo() {
  if (_cinemaVideos.length === 0) return;
  const nextIdx = (_currentPlayingIndex + 1) % _cinemaVideos.length;
  selectCinemaVideo(nextIdx, true);
  showToast(`⏭️ Now Playing: ${_cinemaVideos[nextIdx].filename}`, 'info');
}

function _formatDuration(seconds) {
  if (!seconds) return '00:00';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}
