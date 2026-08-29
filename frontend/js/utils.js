/**
 * Utility functions for formatters, byte calculations, DOM helpers, and toasts.
 */

export function formatBytes(bytes, decimals = 2) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

export function formatSpeed(bytesPerSec) {
  if (!bytesPerSec || bytesPerSec <= 0) return '0 KB/s';
  return formatBytes(bytesPerSec, 1) + '/s';
}

export function formatETA(seconds) {
  if (!seconds || seconds <= 0 || !isFinite(seconds)) return '--';
  const sec = Math.round(seconds);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return `${min}m ${remSec}s`;
  const hrs = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hrs}h ${remMin}m`;
}

export function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

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

export function getFileCategory(file) {
  const type = (file.type || '').toLowerCase();
  const name = (file.name || '').toLowerCase();

  if (type.startsWith('image/')) return 'photo';
  if (type.startsWith('video/')) return 'video';
  if (type.startsWith('audio/')) return 'audio';

  const ext = name.split('.').pop();
  if (['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(ext)) return 'photo';
  if (['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv'].includes(ext)) return 'video';
  if (['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a'].includes(ext)) return 'audio';
  if (['zip', 'rar', '7z', 'tar', 'gz', 'iso'].includes(ext)) return 'archive';
  if (['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt'].includes(ext)) return 'document';
  return 'document';
}

export function cleanFileName(raw, channelContext = null) {
  if (!raw) return '';
  const lastDot = raw.lastIndexOf('.');
  const name = lastDot !== -1 ? raw.slice(0, lastDot) : raw;
  const ext = lastDot !== -1 ? raw.slice(lastDot) : '';

  let cleaned = name;

  // 1. Strip URLs & site domains
  cleaned = cleaned.replace(/https?:\/\/\S+/gi, ' ');
  cleaned = cleaned.replace(/\b(?:t|telegram)\.me\/[\w\+\-_/]+/gi, ' ');
  cleaned = cleaned.replace(/\b(?:www\.[a-z0-9\.\-_]+|[a-z0-9\.\-_]+\.(?:com|org|net|in|yt|vip|me|to|is|cx|ms|li|co|cc|ws|site|xyz|online|live|tv))\b/gi, ' ');

  // 2. Convert underscores and dots to spaces
  cleaned = cleaned.replace(/[\._]+/g, ' ');

  // 3. Strip @ handles (e.g. @channel or @user)
  cleaned = cleaned.replace(/@\S+/g, ' ');

  // 4. Strip bracketed / brace prefixes
  cleaned = cleaned.replace(/^[\[\{][^\]\}]+[\]\}]\s*/g, ' ');

  // 5. Adaptive Channel Token Extraction
  if (channelContext) {
    const rawContext = typeof channelContext === 'string' ? channelContext : `${channelContext.name || ''} ${channelContext.username || ''}`;
    const channelTokens = rawContext
      .replace(/[\._\-@#\$%^&\*()\[\]{}|\\/]/g, ' ')
      .split(/\s+/)
      .map((t) => t.trim().toLowerCase())
      .filter((t) => t.length > 2 && !/^(the|and|for|official|channel|group|hd|cloud|saved|messages)$/i.test(t));

    if (channelTokens.length > 0) {
      const escaped = channelTokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
      cleaned = cleaned.replace(new RegExp(`\\b(?:${escaped.join('|')})\\b`, 'gi'), ' ');
    }
  }

  // 6. Common provider / release group watermark signatures
  const watermarks = [
    'tamilmovoo', 'tamildbox', 'tamilblasters', 'tamilmv', '1tamilmv',
    'tamilyogi', 'moviesda', 'bollyflix', 'katmovie', 'vegamovies',
    'rarbg', 'yify', 'psa', 'pahe', 'tn69', 'cinemavilla', 'isaimini',
    'movies4u', 'cinemahub', 'tamilrockers', 'movierulz', 'cinevood',
    'worldfree4u', 'khatrimaza', 'filmyzilla', '9xmovies', 'extramovies',
    'starflixtamil', 'starflix', 'moviesnation', 'mkvking', 'skymovies',
    'cinehub', 'filmywap', 'coolmoviez', 'todaypk', 'desiremovies'
  ];
  cleaned = cleaned.replace(new RegExp(`\\b(?:${watermarks.join('|')})\\b`, 'gi'), ' ');

  // 7. Strip media noise / codecs / audio specs
  cleaned = cleaned.replace(/\b(2160p?|4k|uhd|1080p?|720p?|480p?|360p?|1p|hdrip|bdrip|bluray|blu-ray|webrip|web-dl|web|hdtv|dvdrip|hq|x264|x265|hevc|avc|xvid|divx|10bit|8bit|aac|ac3|eac3|ddp?\d?|dts|atmos|mp3|esub|esubs|subrip|subs?|sub|multi|dual|hindi|tamil|telugu|kannada|malayalam|english|dubbed)\b/gi, ' ');

  // 8. Dynamic prefix pattern stripper (e.g. "SiteName org Movie" -> "Movie")
  cleaned = cleaned.replace(/^[a-z0-9\-_]+\s+(?:org|com|net|in|tv|vip|me|cc|site|hub|flix|movies|channel)\s+/i, ' ');

  // 9. Clean punctuation & extra whitespace
  cleaned = cleaned.replace(/[\/\\:*?"<>|\[\]\(\)\{\}\-]/g, ' ');
  cleaned = cleaned.replace(/\s+/g, ' ').trim();

  // 10. Format year (YYYY)
  const yearMatch = cleaned.match(/\b(19\d\d|20\d\d)\b/);
  if (yearMatch) {
    const year = yearMatch[1];
    const idx = cleaned.indexOf(year);
    const titlePart = cleaned.slice(0, idx).trim();
    const restPart = cleaned.slice(idx + year.length).trim();
    if (titlePart && restPart) {
      cleaned = `${titlePart} (${year}) ${restPart}`;
    } else if (titlePart) {
      cleaned = `${titlePart} (${year})`;
    } else if (restPart) {
      cleaned = `${restPart} (${year})`;
    }
  }

  cleaned = cleaned.replace(/\s+/g, ' ').trim();
  return cleaned ? `${cleaned}${ext}` : raw;
}
