/**
 * Cinema & Real-Time Video Streaming Controller.
 * Powers the dedicated Cinema tab with Channel Media Archive browser,
 * instant HTTP Range streaming playback, multi-audio language switching,
 * WebVTT subtitle extraction, full-duration timeline seeking, and queue auto-advance.
 */

import { showToast } from './ui.js';
import { formatBytes } from './utils.js';

let _cinemaVideos = [];
let _currentPlayingIndex = -1;
let _currentChatId = 'me';
let _currentAudioTrack = 0;
let _currentSubtitleTrack = 'off';
let _knownDuration = 0;
let _seekBaseOffset = 0;
let _isScrubbing = false;

export async function initCinema() {
  const channelSelect = document.getElementById('cinemaChannelSelect');
  const videoSearch = document.getElementById('cinemaSearchInput');
  const btnRefresh = document.getElementById('btnCinemaRefresh');
  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  const btnFdm = document.getElementById('btnCinemaFdm');
  const btnCopyStream = document.getElementById('btnCinemaCopyUrl');
  const btnTheaterMode = document.getElementById('btnCinemaTheater');
  const btnNextTrack = document.getElementById('btnCinemaNext');
  const audioSelect = document.getElementById('cinemaAudioSelect');
  const subSelect = document.getElementById('cinemaSubtitleSelect');
  const scrubber = document.getElementById('cinemaScrubber');

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

  // Audio Track Selection Change
  if (audioSelect) {
    audioSelect.addEventListener('change', (e) => {
      _currentAudioTrack = parseInt(e.target.value, 10) || 0;
      const currentPos = _seekBaseOffset + (videoPlayer.currentTime || 0);
      _reloadStreamWithParams(currentPos);
      showToast(`🔊 Audio: ${audioSelect.options[audioSelect.selectedIndex]?.text}`, 'info');
    });
  }

  // Subtitle Track Selection Change
  if (subSelect) {
    subSelect.addEventListener('change', (e) => {
      _currentSubtitleTrack = e.target.value;
      _applySubtitleTrack(_currentSubtitleTrack);
    });
  }

  // Custom Full Duration Scrubber
  if (scrubber) {
    scrubber.addEventListener('input', () => {
      _isScrubbing = true;
      const currentElem = document.getElementById('cinemaCurrentTime');
      if (currentElem) currentElem.textContent = _formatDuration(parseFloat(scrubber.value));
    });

    scrubber.addEventListener('change', () => {
      _isScrubbing = false;
      const targetSec = parseFloat(scrubber.value);
      _seekToPosition(targetSec);
    });
  }

  // Video Player Time Update Sync
  videoPlayer.addEventListener('timeupdate', () => {
    if (_isScrubbing) return;
    const currentElem = document.getElementById('cinemaCurrentTime');
    const totalElem = document.getElementById('cinemaTotalDuration');
    const scrubberElem = document.getElementById('cinemaScrubber');

    const totalDur = _knownDuration || videoPlayer.duration || 0;
    const currentPos = _seekBaseOffset + (videoPlayer.currentTime || 0);

    if (currentElem) currentElem.textContent = _formatDuration(currentPos);
    if (totalElem && totalDur > 0) totalElem.textContent = _formatDuration(totalDur);
    if (scrubberElem && totalDur > 0) {
      scrubberElem.max = totalDur;
      scrubberElem.value = currentPos;
    }
  });

  // Video Error & Codec Handling
  const errorNotice = document.getElementById('cinemaPlayerErrorNotice');
  const btnFdmFallback = document.getElementById('btnCinemaFdmFallback');
  const btnCopyFallback = document.getElementById('btnCinemaCopyFallback');

  videoPlayer.addEventListener('error', () => {
    if (_currentPlayingIndex >= 0 && _cinemaVideos[_currentPlayingIndex]) {
      const v = _cinemaVideos[_currentPlayingIndex];
      const currentSrc = videoPlayer.getAttribute('src') || '';
      if (!currentSrc.includes('/stream/') && v.stream_transmux_url) {
        console.log('Native stream unsupported. Auto-switching to real-time transmux stream...');
        videoPlayer.src = v.stream_transmux_url;
        videoPlayer.load();
        videoPlayer.play().catch(() => {});
        return;
      }
    }
    if (errorNotice) errorNotice.style.display = 'flex';
  });

  videoPlayer.addEventListener('loadeddata', () => {
    if (errorNotice) errorNotice.style.display = 'none';
  });

  videoPlayer.addEventListener('playing', () => {
    if (errorNotice) errorNotice.style.display = 'none';
  });

  if (btnFdmFallback) {
    btnFdmFallback.addEventListener('click', () => btnFdm?.click());
  }

  if (btnCopyFallback) {
    btnCopyFallback.addEventListener('click', () => btnCopyStream?.click());
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
    const cinemaTab = document.getElementById('tabPaneCinema');
    if (!cinemaTab || !cinemaTab.classList.contains('active')) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;

    if (e.code === 'Space') {
      e.preventDefault();
      videoPlayer.paused ? videoPlayer.play() : videoPlayer.pause();
    } else if (e.code === 'ArrowRight') {
      e.preventDefault();
      const newPos = Math.min((_knownDuration || videoPlayer.duration || 0), _seekBaseOffset + videoPlayer.currentTime + 10);
      _seekToPosition(newPos);
    } else if (e.code === 'ArrowLeft') {
      e.preventDefault();
      const newPos = Math.max(0, _seekBaseOffset + videoPlayer.currentTime - 10);
      _seekToPosition(newPos);
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

    let resLabel = '';
    if (v.height >= 2160 || v.width >= 3840) resLabel = '4K UHD';
    else if (v.height >= 1080 || v.width >= 1920) resLabel = '1080p';
    else if (v.height >= 720 || v.width >= 1280) resLabel = '720p';
    else if (v.height > 0) resLabel = `${v.height}p`;

    const durationStr = v.duration > 0 ? _formatDuration(v.duration) : '';

    const thumbHtml = v.has_thumb
      ? `<img class="video-thumb-img" src="${v.thumb_url}" alt="" loading="lazy" onerror="this.style.display='none'; if (this.nextElementSibling) this.nextElementSibling.style.display='flex';">
         <div class="video-thumb-fallback" style="display: none;">🎬</div>`
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

export async function selectCinemaVideo(idx, autoPlay = true) {
  if (idx < 0 || idx >= _cinemaVideos.length) return;
  _currentPlayingIndex = idx;
  _currentAudioTrack = 0;
  _currentSubtitleTrack = 'off';
  _seekBaseOffset = 0;
  const v = _cinemaVideos[idx];

  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  const standbyNotice = document.getElementById('cinemaPlayerStandbyNotice');
  const errorNotice = document.getElementById('cinemaPlayerErrorNotice');
  const titleElem = document.getElementById('cinemaNowPlayingTitle');
  const resElem = document.getElementById('cinemaNowPlayingRes');
  const sizeElem = document.getElementById('cinemaNowPlayingSize');
  const durElem = document.getElementById('cinemaNowPlayingDur');
  const timelineContainer = document.getElementById('cinemaTimelineContainer');
  const scrubber = document.getElementById('cinemaScrubber');

  if (standbyNotice) standbyNotice.style.display = 'none';
  if (errorNotice) errorNotice.style.display = 'none';
  if (videoPlayer) videoPlayer.style.display = 'block';

  if (titleElem) titleElem.textContent = v.filename;
  if (resElem) {
    let resText = 'HD Video';
    if (v.width && v.height) resText = `${v.width}x${v.height}`;
    resElem.textContent = resText;
  }
  if (sizeElem) sizeElem.textContent = formatBytes(v.file_size);
  
  _knownDuration = v.duration || 0;
  if (durElem) durElem.textContent = _knownDuration > 0 ? _formatDuration(_knownDuration) : '--:--';

  // Enable timeline scrubber
  if (timelineContainer && _knownDuration > 0) {
    timelineContainer.style.display = 'flex';
    if (scrubber) {
      scrubber.max = _knownDuration;
      scrubber.value = 0;
    }
  }

  // Highlight active card
  document.querySelectorAll('.video-card').forEach((c) => c.classList.remove('active'));
  const activeCard = document.getElementById(`videoCard_${idx}`);
  if (activeCard) activeCard.classList.add('active');

  // 1. Load video source immediately for instantaneous <50ms playback start
  const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);
  const streamUrl = (isMkv && v.stream_transmux_url) ? v.stream_transmux_url : v.stream_url;
  
  videoPlayer.src = streamUrl;
  videoPlayer.load();
  if (autoPlay) {
    videoPlayer.play().catch((err) => {
      console.debug('Autoplay notice:', err);
    });
  }

  // 2. Non-blocking lazy stream probe (runs in background without contending for MTProto download)
  setTimeout(() => {
    _probeAndPopulateTracks(v.chat_id, v.message_id);
  }, 800);
}

async function _probeAndPopulateTracks(chatId, messageId) {
  const audioGroup = document.getElementById('cinemaAudioGroup');
  const subGroup = document.getElementById('cinemaSubtitleGroup');
  const audioSelect = document.getElementById('cinemaAudioSelect');
  const subSelect = document.getElementById('cinemaSubtitleSelect');

  if (audioGroup) audioGroup.style.display = 'none';
  if (subGroup) subGroup.style.display = 'none';

  try {
    const resp = await fetch(`/api/media/streams/${encodeURIComponent(chatId)}/${messageId}`);
    if (!resp.ok) return;
    const info = await resp.json();

    // Populate Audio Tracks
    if (audioSelect && info.audio_tracks && info.audio_tracks.length > 0) {
      audioSelect.innerHTML = '';
      info.audio_tracks.forEach((t) => {
        const opt = document.createElement('option');
        opt.value = t.index;
        opt.textContent = t.title;
        audioSelect.appendChild(opt);
      });
      if (audioGroup) audioGroup.style.display = 'flex';
    }

    // Populate Subtitle Tracks
    if (subSelect && info.subtitle_tracks && info.subtitle_tracks.length > 0) {
      subSelect.innerHTML = '<option value="off">Off</option>';
      info.subtitle_tracks.forEach((s) => {
        const opt = document.createElement('option');
        opt.value = s.vtt_url;
        opt.textContent = s.title;
        subSelect.appendChild(opt);
      });
      if (subGroup) subGroup.style.display = 'flex';
    }
  } catch (e) {
    console.debug('Probe streams notice:', e);
  }
}

function _reloadStreamWithParams(startSeconds = 0) {
  if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) return;
  const v = _cinemaVideos[_currentPlayingIndex];
  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  if (!videoPlayer) return;

  _seekBaseOffset = startSeconds;
  const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);
  
  if (isMkv) {
    const baseUrl = v.stream_transmux_url.split('?')[0];
    videoPlayer.src = `${baseUrl}?audio=${_currentAudioTrack}&ss=${startSeconds}`;
  } else {
    videoPlayer.currentTime = startSeconds;
  }

  videoPlayer.load();
  videoPlayer.play().catch(() => {});
}

function _seekToPosition(targetSeconds) {
  if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) return;
  const v = _cinemaVideos[_currentPlayingIndex];
  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  if (!videoPlayer) return;

  const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);
  if (isMkv) {
    _reloadStreamWithParams(targetSeconds);
  } else {
    videoPlayer.currentTime = targetSeconds;
  }
}

function _applySubtitleTrack(vttUrl) {
  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  if (!videoPlayer) return;

  // Remove existing subtitle tracks
  const oldTracks = videoPlayer.querySelectorAll('track');
  oldTracks.forEach((t) => t.remove());

  if (vttUrl !== 'off') {
    const track = document.createElement('track');
    track.kind = 'subtitles';
    track.label = 'Subtitles';
    track.srclang = 'en';
    track.src = vttUrl;
    track.default = true;
    videoPlayer.appendChild(track);

    setTimeout(() => {
      if (videoPlayer.textTracks && videoPlayer.textTracks.length > 0) {
        for (let i = 0; i < videoPlayer.textTracks.length; i++) {
          videoPlayer.textTracks[i].mode = 'showing';
        }
      }
    }, 200);

    showToast('💬 Subtitles enabled', 'success');
  } else {
    if (videoPlayer.textTracks) {
      for (let i = 0; i < videoPlayer.textTracks.length; i++) {
        videoPlayer.textTracks[i].mode = 'disabled';
      }
    }
    showToast('💬 Subtitles disabled', 'info');
  }
}

function _playNextVideo() {
  if (_cinemaVideos.length === 0) return;
  const nextIdx = (_currentPlayingIndex + 1) % _cinemaVideos.length;
  selectCinemaVideo(nextIdx, true);
  showToast(`⏭️ Now Playing: ${_cinemaVideos[nextIdx].filename}`, 'info');
}

function _formatDuration(seconds) {
  if (!seconds || isNaN(seconds)) return '00:00';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}
