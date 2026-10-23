export function formatBytes(bytes?: number, decimals = 2): string {
  if (!bytes || bytes <= 0 || isNaN(bytes)) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const clampedIdx = Math.min(i, sizes.length - 1);
  return `${parseFloat((bytes / Math.pow(k, clampedIdx)).toFixed(dm))} ${sizes[clampedIdx]}`;
}

export function formatSpeed(bytesPerSec?: number): string {
  if (!bytesPerSec || bytesPerSec <= 0) return '0 KB/s';
  return `${formatBytes(bytesPerSec, 1)}/s`;
}

export function formatETA(seconds?: number): string {
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

export function formatDuration(seconds?: number): string {
  if (!seconds || seconds <= 0 || !isFinite(seconds)) return '0:00';
  const total = Math.floor(seconds);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function getFileCategory(filename?: string): 'video' | 'audio' | 'photo' | 'archive' | 'document' {
  if (!filename) return 'document';
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv', 'm4v', 'ts'].includes(ext)) return 'video';
  if (['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a', 'opus'].includes(ext)) return 'audio';
  if (['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'svg'].includes(ext)) return 'photo';
  if (['zip', 'rar', '7z', 'tar', 'gz', 'iso'].includes(ext)) return 'archive';
  return 'document';
}

export function cleanFileName(name?: string): string {
  if (!name) return 'File';
  return parseMediaInfo(name).cleanTitle;
}

export interface ParsedMediaInfo {
  cleanTitle: string;
  year?: string;
  seasonEpisode?: string;
  resolution?: string;
  audio?: string;
  rawName: string;
}

export function parseMediaInfo(filename?: string): ParsedMediaInfo {
  if (!filename) return { cleanTitle: 'Untitled Video', rawName: '' };
  
  // Strip file extension
  let working = filename.replace(/\.(mkv|mp4|mov|avi|webm|flv|m4v|ts)$/i, '');

  // Extract Resolution
  let resolution = '';
  const resMatch = working.match(/\b(2160p|4k|1080p|720p|480p|uhd|fhd|hd|hdr)\b/i);
  if (resMatch) {
    resolution = resMatch[0].toUpperCase();
    working = working.replace(resMatch[0], ' ');
  }

  // Extract Year (19xx or 20xx) - strip enclosing brackets/parentheses
  let year = '';
  const yearMatch = working.match(/(?:[\(\[\{])?\b(19\d{2}|20\d{2})\b(?:[\)\]\}])?/);
  if (yearMatch) {
    year = yearMatch[1] || yearMatch[0].replace(/[^\d]/g, '');
    working = working.replace(yearMatch[0], ' ');
  }

  // Extract Season / Episode patterns (e.g. S01E09, S03 EP 45 46, S01 EP 01, S02-E04)
  let seasonEpisode = '';
  const seMatch = working.match(/\b(s\d{1,2}\s*(?:ep?|\-)?\s*\d{1,2}(?:\s*[\-\–\s]\s*(?:ep)?\s*\d{1,2})?)\b/i);
  if (seMatch) {
    seasonEpisode = seMatch[0].toUpperCase().replace(/\s+/g, ' ');
    working = working.replace(seMatch[0], ' ');
  }

  // Extract Audio / Language tags
  let audio = '';
  const audioMatch = working.match(/\b(tamil|hindi|telugu|malayalam|kannada|english|multi(?:\s*audio)?|dual(?:\s*audio)?)\b/i);
  if (audioMatch) {
    audio = audioMatch[0].toUpperCase();
    working = working.replace(audioMatch[0], ' ');
  }

  // Strip Telegram spam release channels and common rip/scene tags
  const spamKeywords = [
    'VIP\\s*LinkzZ?',
    'SKLink',
    'PrakyTV',
    'MovieeManHD',
    'ONLYHD4U',
    'TRUE\\s*[A-Z]',
    'TamilHQ',
    'HQ',
    'HS\\s*\\d*',
    'TR',
    'HD',
    'WEB\\-DL',
    'WEBRip',
    'BDRip',
    'HDRip',
    'BluRay',
    'x264',
    'x265',
    'HEVC',
    'AAC',
    'DDP',
    'ATMOS',
    'ESub',
    'combined',
    'TRUE\\s*W',
    '\\b[0-9]+(?:\\.[0-9]+)?\\s*(?:gb|mb)\\b',
    'mkv',
    'mp4',
    'avi',
    'webm',
  ];

  for (const pattern of spamKeywords) {
    working = working.replace(new RegExp(`\\b${pattern}\\b`, 'gi'), ' ');
  }

  // Clean trailing single stray characters, brackets, dots, underscores, and extra whitespace
  working = working
    .replace(/[_\.\(\)\[\]\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  // Strip lone trailing/leading 1-2 character artifacts (e.g. stray 'x' or 'H')
  working = working.replace(/^\b[a-z0-9]\b\s*/i, '').replace(/\s*\b[a-z0-9]\b$/i, '').trim();

  return {
    cleanTitle: working || filename.replace(/[_\.]+/g, ' ').trim(),
    year,
    seasonEpisode,
    resolution,
    audio,
    rawName: filename,
  };
}

