import { showToast } from './ui.js';
import { formatBytes, escapeHtml } from './utils.js';
import { chatPicker } from './chat-picker.js';

let _cinemaVideos = [];
let _currentPlayingIndex = -1;
let _currentChatId = 'me';
let _currentAudioTrack = 0;
let _currentSubtitleTrack = 'off';
let _knownDuration = 0;
let _seekBaseOffset = 0;
let _isScrubbing = false;

export async function initCinema() {
  const btnChooseChat = document.getElementById('btnCinemaChooseChat');
  const videoSearch = document.getElementById('cinemaSearchInput');
  const btnRefresh = document.getElementById('btnCinemaRefresh');
  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  const btnFdm = document.getElementById('btnCinemaFdm');
  const btnCopyStream = document.getElementById('btnCinemaCopyUrl');
  const btnNextTrack = document.getElementById('btnCinemaNext');
  const audioSelect = document.getElementById('cinemaAudioSelect');
  const subSelect = document.getElementById('cinemaSubtitleSelect');
  const scrubber = document.getElementById('cinemaScrubber');

  if (!videoPlayer) return;

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

  const btnVlc = document.getElementById('btnCinemaVlc');
  const btnVlcFallback = document.getElementById('btnCinemaVlcFallback');

  async function _triggerVlcStream() {
    if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) {
      showToast('Please select a video from the archive first', 'info');
      return;
    }
    const v = _cinemaVideos[_currentPlayingIndex];
    showToast(`🎬 Launching VLC for "${v.filename}"...`, 'info');
    try {
      const resp = await fetch('/api/media/vlc/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: v.chat_id,
          message_id: v.message_id,
          filename: v.filename,
          stream_url: v.stream_url
        })
      });
      const data = await resp.json();
      if (data.launched) {
        showToast(`🎬 VLC launched for "${v.filename}"!`, 'success');
      } else if (data.playlist_url) {
        const a = document.createElement('a');
        a.href = data.playlist_url;
        a.download = `${v.filename}.m3u`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        showToast('🎬 Generated VLC .m3u playlist. Opening stream...', 'success');
      }
    } catch (e) {
      showToast(`VLC launch notice: ${e.message}`, 'warning');
    }
  }

  if (btnVlc) {
    btnVlc.addEventListener('click', _triggerVlcStream);
  }

  if (btnVlcFallback) {
    btnVlcFallback.addEventListener('click', _triggerVlcStream);
  }

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

  // Keyboard Shortcuts for Video Player
  document.addEventListener('keydown', (e) => {
    const cinemaTab = document.getElementById('tabPaneCinema');
    if (!cinemaTab || !cinemaTab.classList.contains('active')) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;

    if (e.code === 'Space' || e.code === 'KeyK') {
      e.preventDefault();
      videoPlayer.paused ? videoPlayer.play() : videoPlayer.pause();
    } else if (e.code === 'ArrowRight' || e.code === 'KeyL') {
      e.preventDefault();
      const currentPos = _seekBaseOffset + (videoPlayer.currentTime || 0);
      const totalDur = _knownDuration || videoPlayer.duration || (currentPos + 60);
      const newPos = Math.min(totalDur, currentPos + 10);
      _seekToPosition(newPos);
    } else if (e.code === 'ArrowLeft' || e.code === 'KeyJ') {
      e.preventDefault();
      const currentPos = _seekBaseOffset + (videoPlayer.currentTime || 0);
      const newPos = Math.max(0, currentPos - 10);
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
    } else if (e.code === 'KeyV') {
      e.preventDefault();
      _triggerVlcStream();
    }
  });

  // Initial Load
  loadCinemaVideos('me');
}

function _initCinemaDestinationPicker() {
  const nameEl = document.getElementById('cinemaCurrentChatName');
  const typeEl = document.getElementById('cinemaCurrentChatType');
  const iconEl = document.getElementById('cinemaCurrentChatIcon');
  
  if (nameEl) nameEl.textContent = 'Saved Messages (Personal Cloud)';
  if (typeEl) typeEl.textContent = 'CLOUD';
  if (iconEl) iconEl.textContent = '☁️';
}

function _onCinemaChatSelected(chat) {
  if (!chat) return;
  _currentChatId = chat.id;

  const nameEl = document.getElementById('cinemaCurrentChatName');
  const typeEl = document.getElementById('cinemaCurrentChatType');
  const iconEl = document.getElementById('cinemaCurrentChatIcon');

  let icon = '💬';
  if (chat.type === 'saved_messages') icon = '☁️';
  else if (chat.type === 'channel') icon = '📢';
  else if (chat.type === 'supergroup' || chat.type === 'group') icon = '👥';
  else if (chat.type === 'bot') icon = '🤖';

  if (iconEl) iconEl.textContent = icon;
  if (nameEl) nameEl.textContent = chat.name || 'Chat';
  if (typeEl) {
    const label = chat.type === 'saved_messages' ? 'CLOUD' : (chat.type || 'CHAT').replace('_', ' ').toUpperCase();
    typeEl.textContent = label;
  }

  // Update active chip state
  const chipContainer = document.getElementById('cinemaWatchedChips');
  if (chipContainer) {
    chipContainer.querySelectorAll('.cinema-chip').forEach((el) => {
      if (el.getAttribute('data-chat-id') === String(chat.id)) {
        el.classList.add('active');
      } else {
        el.classList.remove('active');
      }
    });
  }

  showToast(`🎬 Source Channel: "${chat.name}"`, 'info');
  loadCinemaVideos(_currentChatId);
}

async function _loadCinemaWatchedChips() {
  const chipContainer = document.getElementById('cinemaWatchedChips');
  if (!chipContainer) return;

  const watchedItems = [
    { id: 'me', name: 'Saved Messages', type: 'saved_messages' }
  ];

  try {
    const [chatsResp, snifferResp] = await Promise.allSettled([
      fetch('/api/chats'),
      fetch('/api/sniffer/status')
    ]);

    let allChats = [];
    if (chatsResp.status === 'fulfilled' && chatsResp.value.ok) {
      allChats = await chatsResp.value.json();
    }

    let watchedIds = [];
    if (snifferResp.status === 'fulfilled' && snifferResp.value.ok) {
      const snifferData = await snifferResp.value.json();
      watchedIds = snifferData.watched_channels || [];
    }

    // Add watched channels
    watchedIds.forEach((wId) => {
      if (String(wId) === 'me') return;
      const matched = allChats.find((c) => String(c.id) === String(wId));
      if (matched) {
        watchedItems.push(matched);
      } else {
        watchedItems.push({ id: wId, name: `Channel ${wId}`, type: 'channel' });
      }
    });

    // Also add top channels from dialogs
    allChats.forEach((c) => {
      if (watchedItems.some((w) => String(w.id) === String(c.id))) return;
      if (watchedItems.length < 7 && (c.type === 'channel' || c.type === 'supergroup')) {
        watchedItems.push(c);
      }
    });

  } catch (err) {
    console.debug('Error loading watched chips for Cinema:', err);
  }

  chipContainer.innerHTML = watchedItems.map((c) => {
    const isActive = String(_currentChatId) === String(c.id);
    let icon = '💬';
    if (c.type === 'saved_messages') icon = '☁️';
    else if (c.type === 'channel') icon = '📢';
    else if (c.type === 'supergroup' || c.type === 'group') icon = '👥';

    return `
      <button type="button" class="cinema-chip ${isActive ? 'active' : ''}" data-chat-id="${escapeHtml(String(c.id))}" style="display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: var(--radius-full); font-size: 0.76rem; font-weight: 600; cursor: pointer; white-space: nowrap; border: 1px solid var(--border-glass); background: var(--bg-card); color: var(--text-muted); transition: all 0.2s; flex-shrink: 0;">
        <span>${icon}</span>
        <span>${escapeHtml(c.name)}</span>
      </button>
    `;
  }).join('');

  // Attach chip click listeners
  chipContainer.querySelectorAll('.cinema-chip').forEach((btn) => {
    btn.addEventListener('click', () => {
      const chatId = btn.getAttribute('data-chat-id');
      const item = watchedItems.find((w) => String(w.id) === String(chatId));
      if (item) {
        _onCinemaChatSelected(item);
      }
    });
  });
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
        <span class="video-card-title" title="${escapeHtml(v.filename)}">${escapeHtml(v.filename)}</span>
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

  // 1. Load video source immediately for instantaneous <50ms playback start
  const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);
  const streamUrl = (isMkv && v.stream_transmux_url) ? v.stream_transmux_url : v.stream_url;

  // Show custom timeline scrubber only for transmuxed streams that need server-side seeking
  if (timelineContainer) {
    if (isMkv && _knownDuration > 0) {
      timelineContainer.style.display = 'flex';
      if (scrubber) {
        scrubber.max = _knownDuration;
        scrubber.value = 0;
      }
    } else {
      timelineContainer.style.display = 'none';
    }
  }

  // Highlight active card
  document.querySelectorAll('.video-card').forEach((c) => c.classList.remove('active'));
  const activeCard = document.getElementById(`videoCard_${idx}`);
  if (activeCard) activeCard.classList.add('active');
  
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
  
  if (isMkv && v.stream_transmux_url) {
    const baseUrl = v.stream_transmux_url.split('?')[0];
    videoPlayer.src = `${baseUrl}?audio=${_currentAudioTrack}&ss=${startSeconds}`;
    videoPlayer.load();
    videoPlayer.play().catch(() => {});
  } else {
    try {
      videoPlayer.currentTime = startSeconds;
    } catch (e) {
      console.debug('Native seek error:', e);
    }
  }
}

function _seekToPosition(targetSeconds) {
  if (_currentPlayingIndex < 0 || !_cinemaVideos[_currentPlayingIndex]) return;
  const v = _cinemaVideos[_currentPlayingIndex];
  const videoPlayer = document.getElementById('cinemaVideoPlayer');
  if (!videoPlayer) return;

  const isMkv = v.is_mkv || /\.(mkv|avi|ts|flv|wmv|vob)$/i.test(v.filename);
  if (isMkv && v.stream_transmux_url) {
    _reloadStreamWithParams(targetSeconds);
  } else {
    _seekBaseOffset = 0;
    try {
      videoPlayer.currentTime = targetSeconds;
    } catch (e) {
      console.debug('Native seek error:', e);
    }
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
