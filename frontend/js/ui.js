/**
 * UI Renderer & DOM Manipulation Module.
 * Features smart non-destructive DOM reconciliation (zero screen flashing),
 * dedicated 2-Stage status lines for splitting & uploading, and live metrics.
 */

import { formatBytes, formatSpeed, formatETA, escapeHtml } from './utils.js';

export function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  
  let icon = 'ℹ️';
  if (type === 'success') icon = '✅';
  if (type === 'error') icon = '❌';
  if (type === 'warning') icon = '⚠️';

  toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function buildStatusBadgeHtml(task) {
  const isSplitting = task.status === 'splitting';
  const isPaused = task.status === 'paused';
  const isCompleted = task.status === 'completed';
  const isFailed = task.status === 'failed';
  const isCancelled = task.status === 'cancelled';

  if (isSplitting) {
    return `<span class="badge splitting" title="Auto-splitting large file for 2GB Telegram limit">✂️ SPLITTING (${task.progress ? task.progress.toFixed(0) : 0}%)</span>`;
  } else if (task.status === 'streaming') {
    return `<span class="badge uploading" style="background: rgba(0, 206, 201, 0.18); color: var(--accent-secondary); border-color: rgba(0, 206, 201, 0.4);" title="Streaming to local engine buffer">STREAMING (${task.progress ? task.progress.toFixed(0) : 0}%)</span>`;
  } else if (task.status === 'preparing') {
    return `<span class="badge uploading" title="Connecting to Telegram MTProto">PREPARING...</span>`;
  } else if (task.status === 'uploading') {
    const partInfo = task.totalParts > 1 ? ` (${task.currentPart}/${task.totalParts})` : '';
    return `<span class="badge uploading" title="Uploading to Telegram MTProto">UPLOADING${partInfo}</span>`;
  } else if (isPaused) {
    return `<span class="badge" style="background: rgba(253, 203, 110, 0.2); color: var(--status-warning);">PAUSED</span>`;
  } else if (isCompleted) {
    return `<span class="badge completed">COMPLETED</span>`;
  } else if (isFailed) {
    const errTooltip = escapeHtml(task.error || 'Upload error occurred');
    return `<span class="badge failed" title="${errTooltip}">FAILED</span>`;
  } else if (isCancelled) {
    return `<span class="badge" style="background: rgba(255,255,255,0.1); color: var(--text-dim);">CANCELLED</span>`;
  }
  return `<span class="badge" style="background: rgba(255,255,255,0.08); color: var(--text-muted);">QUEUED</span>`;
}

function buildStageLineHtml(task) {
  if (task.status === 'splitting') {
    return `<div class="card-stage-line splitting">✂️ <strong>Stage 1 of 2:</strong> Splitting into 1.9GB sequence parts (${task.progress ? task.progress.toFixed(1) : 0}%) • <em>Preparing Telegram slices</em></div>`;
  } else if (task.totalParts > 1 && task.status === 'uploading') {
    return `<div class="card-stage-line uploading">🚀 <strong>Stage 2 of 2:</strong> Uploading Part ${task.currentPart} of ${task.totalParts} to Telegram Cloud</div>`;
  } else if (task.status === 'streaming') {
    return `<div class="card-stage-line streaming">⚡ <strong>Buffering Stream:</strong> Streaming file to local engine (${task.progress ? task.progress.toFixed(1) : 0}%)</div>`;
  } else if (task.status === 'uploading') {
    return `<div class="card-stage-line uploading">🚀 <strong>Turbo MTProto Upload:</strong> Streaming to Telegram (6 Workers)</div>`;
  } else if (task.status === 'completed') {
    return `<div class="card-stage-line completed">✅ <strong>Upload Finished:</strong> Delivered to ${escapeHtml(task.chatName || 'Telegram')}</div>`;
  } else if (task.status === 'paused') {
    return `<div class="card-stage-line">⏸️ <strong>Upload Paused:</strong> Resume anytime without losing progress</div>`;
  } else if (task.status === 'failed') {
    return `<div class="card-stage-line" style="color: var(--status-danger);">❌ <strong>Error:</strong> ${escapeHtml(task.error || 'Failed to upload')}</div>`;
  }
  return `<div class="card-stage-line">⏳ <strong>Queued:</strong> Waiting for worker slot...</div>`;
}

function buildActionButtonsHtml(task) {
  const isUploading = task.status === 'uploading' || task.status === 'streaming' || task.status === 'preparing' || task.status === 'splitting';
  const isPaused = task.status === 'paused';
  const isQueued = task.status === 'queued';

  if (isUploading) {
    return `
      <button class="icon-btn" title="Pause Upload" onclick="window._app.pause('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>
      </button>
      <button class="icon-btn danger" title="Cancel Upload" onclick="window._app.cancel('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
  } else if (isPaused) {
    return `
      <button class="icon-btn" title="Resume Upload" onclick="window._app.resume('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
      </button>
      <button class="icon-btn danger" title="Cancel" onclick="window._app.cancel('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
  } else if (isQueued) {
    return `
      <button class="icon-btn danger" title="Remove from Queue" onclick="window._app.remove('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
      </button>
    `;
  } else {
    return `
      <button class="icon-btn" title="Dismiss" onclick="window._app.remove('${task.id}')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
  }
}

export function renderQueue(queue) {
  const container = document.getElementById('queueListContainer');
  const countBadge = document.getElementById('queueCountBadge');
  const emptyState = document.getElementById('queueEmptyState');
  const workspace = document.getElementById('workspaceLayout');

  if (countBadge) {
    countBadge.textContent = queue.length;
    countBadge.classList.toggle('has-items', queue.length > 0);
  }

  // Toggle dynamic 2-column split layout when queue has active items
  if (workspace) {
    if (queue.length > 0) {
      workspace.classList.add('active-split');
    } else {
      workspace.classList.remove('active-split');
    }
  }

  if (!container) return;

  if (queue.length === 0) {
    if (emptyState) emptyState.style.display = 'block';
    container.innerHTML = '';
    return;
  }

  if (emptyState) emptyState.style.display = 'none';

  // Smart targeted DOM update: Only update changed cards without rebuilding entire DOM
  const existingCardIds = new Set();

  queue.forEach((task) => {
    existingCardIds.add(task.id);
    let card = document.getElementById(`card_${task.id}`);
    const isSplitting = task.status === 'splitting';
    const isUploading = task.status === 'uploading' || task.status === 'streaming' || task.status === 'preparing';

    if (!card) {
      // Create new card DOM node
      card = document.createElement('div');
      card.className = 'queue-card';
      card.id = `card_${task.id}`;

      let thumbHtml = '';
      if (task.preview && task.preview.url) {
        thumbHtml = `<img src="${task.preview.url}" alt="Preview" />`;
      } else {
        thumbHtml = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
          </svg>
        `;
      }

      card.innerHTML = `
        <div class="card-thumbnail">${thumbHtml}</div>
        <div class="card-details">
          <div class="card-filename-row">
            <input 
              type="text" 
              class="editable-filename" 
              value="${escapeHtml(task.customFilename || task.filename)}" 
              title="Click to rename"
              ${task.status !== 'queued' ? 'readonly' : ''}
              onchange="window._app.updateFilename('${task.id}', this.value)"
            />
            <div class="badge-slot">${buildStatusBadgeHtml(task)}</div>
          </div>
          <div class="stage-slot">${buildStageLineHtml(task)}</div>
          <div class="card-meta-row">
            <span class="meta-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
              ${formatBytes(task.fileSize)}
            </span>
            <span class="meta-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
              ${escapeHtml(task.chatName || 'Telegram Chat')}
            </span>
          </div>
          <div class="progress-container">
            <div class="progress-track">
              <div 
                class="progress-bar-fill ${isSplitting ? 'splitting' : (isUploading ? 'uploading' : '')}" 
                style="width: ${Math.min(100, task.progress || 0)}%;"
              ></div>
            </div>
            <div class="progress-metrics-row">
              <div class="metrics-left"></div>
              <div class="metrics-right">${formatBytes(task.uploadedBytes || 0)} / ${formatBytes(task.fileSize)}</div>
            </div>
          </div>
        </div>
        <div class="card-actions">${buildActionButtonsHtml(task)}</div>
      `;

      container.appendChild(card);
    }

    // In-place smooth update of dynamic elements (ZERO screen flashing!)
    card.className = `queue-card status-${task.status}`;

    const badgeSlot = card.querySelector('.badge-slot');
    if (badgeSlot) badgeSlot.innerHTML = buildStatusBadgeHtml(task);

    const stageSlot = card.querySelector('.stage-slot');
    if (stageSlot) stageSlot.innerHTML = buildStageLineHtml(task);

    const barFill = card.querySelector('.progress-bar-fill');
    if (barFill) {
      barFill.style.width = `${Math.min(100, task.progress || 0)}%`;
      barFill.className = `progress-bar-fill ${isSplitting ? 'splitting' : (isUploading ? 'uploading' : '')}`;
    }

    const metricsLeft = card.querySelector('.metrics-left');
    if (metricsLeft) {
      if (isSplitting) {
        metricsLeft.innerHTML = `<span>✂️ Splitting: ${task.progress ? task.progress.toFixed(1) : 0}%</span><span>📦 1.9GB Part Buffer</span>`;
      } else if (isUploading) {
        metricsLeft.innerHTML = `<span>${task.progress ? task.progress.toFixed(1) : 0}%</span><span>⚡ ${formatSpeed(task.speed)}</span><span>⏳ ETA: ${formatETA(task.eta)}</span>`;
      } else {
        metricsLeft.innerHTML = `<span>${task.progress ? task.progress.toFixed(1) : 0}%</span>`;
      }
    }

    const metricsRight = card.querySelector('.metrics-right');
    if (metricsRight) {
      metricsRight.textContent = `${formatBytes(task.uploadedBytes || 0)} / ${formatBytes(task.fileSize)}`;
    }

    const actionsSlot = card.querySelector('.card-actions');
    if (actionsSlot) {
      actionsSlot.innerHTML = buildActionButtonsHtml(task);
    }
  });

  // Remove any stale cards from container
  Array.from(container.children).forEach((child) => {
    const id = child.id.replace('card_', '');
    if (!existingCardIds.has(id)) {
      child.remove();
    }
  });
}

let _cachedHistory = [];
let _historyFilterStatus = 'all';
let _historySearchQuery = '';

export function initHistoryControls() {
  const searchInput = document.getElementById('historySearchInput');
  const statusFilter = document.getElementById('historyStatusFilter');
  const btnExportCSV = document.getElementById('btnExportCSV');
  const btnExportJSON = document.getElementById('btnExportJSON');
  const btnClearHistory = document.getElementById('btnClearHistoryBtn');
  const btnRefreshHistory = document.getElementById('btnRefreshHistory');

  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      _historySearchQuery = (e.target.value || '').toLowerCase().trim();
      renderFilteredHistory();
    });
  }

  if (statusFilter) {
    statusFilter.addEventListener('change', (e) => {
      _historyFilterStatus = e.target.value;
      renderFilteredHistory();
    });
  }

  if (btnExportCSV) {
    btnExportCSV.addEventListener('click', () => exportHistoryCSV());
  }

  if (btnExportJSON) {
    btnExportJSON.addEventListener('click', () => exportHistoryJSON());
  }

  if (btnClearHistory) {
    btnClearHistory.addEventListener('click', () => {
      if (window._app && window._app.clearHistory) {
        window._app.clearHistory();
      }
    });
  }

  if (btnRefreshHistory) {
    btnRefreshHistory.addEventListener('click', () => {
      loadHistory();
      showToast('History refreshed', 'info');
    });
  }
}

function renderFilteredHistory() {
  const fullContainer = document.getElementById('fullHistoryContainer');
  const badge = document.getElementById('historyTotalBadge');
  if (!fullContainer) return;

  let filtered = _cachedHistory;

  if (_historyFilterStatus !== 'all') {
    filtered = filtered.filter(item => item.status === _historyFilterStatus);
  }

  if (_historySearchQuery) {
    filtered = filtered.filter(item => {
      const fn = (item.filename || '').toLowerCase();
      const cn = (item.chat_name || '').toLowerCase();
      const cid = String(item.chat_id || '').toLowerCase();
      const dt = (item.created_at || '').toLowerCase();
      return fn.includes(_historySearchQuery) || cn.includes(_historySearchQuery) || cid.includes(_historySearchQuery) || dt.includes(_historySearchQuery);
    });
  }

  if (badge) {
    badge.textContent = filtered.length;
  }

  if (filtered.length === 0) {
    fullContainer.innerHTML = `
      <div class="history-empty">
        <p>No matching transfers found.</p>
      </div>
    `;
    return;
  }

  fullContainer.innerHTML = filtered.map((item) => {
    const isOk = item.status === 'completed';
    const isFail = item.status === 'failed';
    const partsBadge = item.parts_count > 1 ? `<span class="history-parts-tag">📦 ${item.parts_count} Parts</span>` : '';
    
    let statusLabel = '⚡ UPLOADING';
    if (isOk) statusLabel = '✓ COMPLETED';
    else if (isFail) statusLabel = '✕ FAILED';

    return `
      <div class="history-item ${isOk ? 'status-ok' : (isFail ? 'status-err' : 'status-pending')}">
        <div class="history-item-top">
          <div class="history-file-info">
            <span class="history-file-icon">📄</span>
            <span class="history-filename" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
          </div>
          <span class="badge ${isOk ? 'completed' : (isFail ? 'failed' : 'uploading')}">
            ${statusLabel}
          </span>
        </div>
        <div class="history-item-sub">
          <span class="history-sub-meta">💾 ${formatBytes(item.file_size)}</span>
          <span class="history-dot">•</span>
          <span class="history-sub-meta" title="${escapeHtml(item.chat_name || item.chat_id)}">💬 ${escapeHtml(item.chat_name || item.chat_id)}</span>
          ${partsBadge ? `<span class="history-dot">•</span>` + partsBadge : ''}
          <span class="history-dot">•</span>
          <span class="history-date">${escapeHtml(item.created_at || '')}</span>
        </div>
      </div>
    `;
  }).join('');
}

export function exportHistoryCSV() {
  if (_cachedHistory.length === 0) {
    showToast('No history records to export', 'warning');
    return;
  }

  const headers = ['ID', 'Filename', 'File Size (Bytes)', 'Chat ID', 'Chat Name', 'Status', 'Parts Count', 'Created At'];
  const rows = _cachedHistory.map(item => [
    `"${item.id || ''}"`,
    `"${(item.filename || '').replace(/"/g, '""')}"`,
    item.file_size || 0,
    `"${item.chat_id || ''}"`,
    `"${(item.chat_name || '').replace(/"/g, '""')}"`,
    `"${item.status || ''}"`,
    item.parts_count || 1,
    `"${item.created_at || ''}"`
  ]);

  const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows.map(e => e.join(','))].join('\n');
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement('a');
  link.setAttribute('href', encodedUri);
  link.setAttribute('download', `tg_power_suite_history_${Date.now()}.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  showToast('Transfer history exported as CSV ✓', 'success');
}

export function exportHistoryJSON() {
  if (_cachedHistory.length === 0) {
    showToast('No history records to export', 'warning');
    return;
  }

  const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(_cachedHistory, null, 2));
  const link = document.createElement('a');
  link.setAttribute('href', dataStr);
  link.setAttribute('download', `tg_power_suite_history_${Date.now()}.json`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  showToast('Transfer history exported as JSON ✓', 'success');
}

export async function loadHistory() {
  const container = document.getElementById('historyListContainer');
  const fullContainer = document.getElementById('fullHistoryContainer');
  const badge = document.getElementById('historyTotalBadge');
  if (!container && !fullContainer) return;

  try {
    const res = await fetch('/api/history');
    if (!res.ok) return;
    const history = await res.json();
    _cachedHistory = history || [];

    if (badge) {
      badge.textContent = _cachedHistory.length;
    }

    renderFilteredHistory();

    // Side list for uploader right pane
    if (container) {
      if (_cachedHistory.length === 0) {
        container.innerHTML = `<div class="history-empty"><p>No uploads recorded yet.</p></div>`;
      } else {
        container.innerHTML = _cachedHistory.slice(0, 5).map((item) => {
          const isOk = item.status === 'completed';
          return `
            <div class="history-item ${isOk ? 'status-ok' : 'status-err'}">
              <div class="history-item-top">
                <div class="history-file-info">
                  <span class="history-file-icon">📄</span>
                  <span class="history-filename" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
                </div>
                <span class="badge ${isOk ? 'completed' : 'failed'}">
                  ${isOk ? '✓ SENT' : '✕ FAILED'}
                </span>
              </div>
              <div class="history-item-sub">
                <span class="history-sub-meta">💾 ${formatBytes(item.file_size)}</span>
                <span class="history-dot">•</span>
                <span class="history-date">${escapeHtml(item.created_at || '')}</span>
              </div>
            </div>
          `;
        }).join('');
      }
    }
  } catch (e) {
    console.error('Error loading history:', e);
  }
}

