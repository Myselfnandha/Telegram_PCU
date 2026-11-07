/**
 * Web Series Draft Engine (Deep Module)
 *
 * Encapsulates the algorithmic detection, title canonicalization, multi-pattern
 * season/episode extraction, fuzzy show clustering, and multi-dimensional date
 * sorting of episodic Telegram video streams behind a clean, focused interface.
 */

import type { CinemaVideo } from '@/types';
import { cleanFileName, formatBytes, formatDuration } from '@/lib/formatters';

/**
 * Format a Unix timestamp (seconds or milliseconds) or ISO string into a human-readable date.
 * Example: 1730764800 -> "Nov 5, 2026"
 */
export function formatMediaDate(dateVal?: number | string): string {
  if (!dateVal) return '';
  const num = typeof dateVal === 'string' ? Number(dateVal) : dateVal;
  let date: Date;

  if (!isNaN(num) && num > 0) {
    // If timestamp is in seconds (< 10^11), convert to milliseconds
    const ms = num < 10000000000 ? num * 1000 : num;
    date = new Date(ms);
  } else if (typeof dateVal === 'string') {
    date = new Date(dateVal);
  } else {
    return '';
  }

  if (isNaN(date.getTime())) return '';

  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date);
}

/**
 * Normalizes date into a numeric Unix timestamp in seconds for consistent sorting.
 */
export function normalizeDateToSeconds(dateVal?: number | string): number {
  if (!dateVal) return 0;
  const num = typeof dateVal === 'string' ? Number(dateVal) : dateVal;
  if (!isNaN(num) && num > 0) {
    return num < 10000000000 ? num : Math.floor(num / 1000);
  }
  if (typeof dateVal === 'string') {
    const parsed = Date.parse(dateVal);
    if (!isNaN(parsed)) {
      return Math.floor(parsed / 1000);
    }
  }
  return 0;
}

export interface WebSeriesEpisode {
  video: CinemaVideo;
  canonicalTitle: string;
  seasonNumber: number;
  seasonLabel: string;
  episodeNumber: number;
  episodeLabel: string;
  displayTitle: string;
  releaseDate: number; // Unix timestamp in seconds
  formattedDate: string;
  durationFormatted: string;
  sizeFormatted: string;
}

export interface WebSeriesSeason {
  seasonNumber: number;
  seasonLabel: string;
  episodes: WebSeriesEpisode[];
  totalEpisodes: number;
  totalBytes: number;
  latestDate: number;
  earliestDate: number;
}

export interface WebSeriesDraft {
  id: string; // Lowercase slug / canonical key
  seriesTitle: string;
  totalSeasons: number;
  totalEpisodes: number;
  totalBytes: number;
  latestDate: number; // Timestamp of newest episode
  earliestDate: number; // Timestamp of oldest episode
  latestDateFormatted: string;
  leadThumbUrl?: string;
  seasons: WebSeriesSeason[]; // Sorted by seasonNumber ascending (Season 1, Season 2...)
  activeSeasonNumber: number; // Defaults to first season
}

export type SeriesSortOption = 'date-desc' | 'date-asc' | 'title-asc' | 'episodes-desc';
export type EpisodeSortOption = 'date-desc' | 'date-asc' | 'episode-asc' | 'episode-desc';

export interface DraftEngineOptions {
  seriesSortBy?: SeriesSortOption;
  episodeSortBy?: EpisodeSortOption;
  filterQuery?: string;
}

/**
 * Extracts season number, episode number, and canonical show title from a raw media filename.
 */
export function extractSeriesMetadata(filename: string, channelName?: string): {
  isSeries: boolean;
  canonicalTitle: string;
  seasonNumber: number;
  seasonLabel: string;
  episodeNumber: number;
  episodeLabel: string;
  cleanTitle: string;
} {
  const clean = cleanFileName(filename) || filename;

  // Regex pattern matching season and episode conventions:
  // 1) S01E02, S1E1, S01-E02, S01.E02, S01 EP02
  // 2) 1x02, 01x02
  // 3) Season 1 Episode 2, Season 01 Ep 02
  // 4) EP01, Episode 01, Part 1, Pt 1, Part 02 (defaults to Season 1)
  // 5) Episode ranges: EP 01 - 04, S01E01-E04, EP 25 28
  const seriesRegex = /^(.*?)(?:[\s._\-\(\[]+)(?:(s\d{1,2}|season\s*\d{1,2})[\s._\-\]\)]*)?(?:(e\d{1,3}(?:[\s\-]+\d{1,3})?|ep\s*\d{1,3}(?:[\s\-]+\d{1,3})?|episode\s*\d{1,3}(?:[\s\-]+\d{1,3})?|part\s*\d{1,2}|pt\s*\d{1,2}))(.*)$/i;
  const match = clean.match(seriesRegex);

  if (match) {
    let rawTitle = (match[1] || '').replace(/\.\w+$/, '').replace(/[._\-\(\)]+$/, '').trim();
    if (!rawTitle) rawTitle = clean.replace(/\.\w+$/, '').trim();

    // Canonical title: strip channel noise, domain signatures, watermarks, year, and empty brackets
    let canonical = cleanFileName(rawTitle)
      .replace(/\b(19\d\d|20\d\d)\b/g, ' ')
      .replace(/[\(\)\[\]\{\}]/g, ' ')
      .replace(/^(?:org|com|net|tv|hd|link|linkzz|official|tamilmv|tamilblasters)\s+/i, '')
      .replace(/\s+/g, ' ')
      .trim();

    if (!canonical) {
      canonical = channelName ? channelName.replace(/\b(channel|group|backup|official)\b/gi, '').trim() : 'Web Series';
    }

    const sSeason = (match[2] || 'S01').toUpperCase().replace(/\s+/g, ' ');
    const sEp = (match[3] || 'EP01').toUpperCase().replace(/\s+/g, ' ');
    const seasonNumber = parseInt(sSeason.replace(/\D/g, ''), 10) || 1;
    const seasonLabel = `Season ${seasonNumber}`;

    // Extract first numeric episode value
    const episodeDigits = sEp.match(/\d{1,3}/);
    const episodeNumber = episodeDigits ? parseInt(episodeDigits[0], 10) : 1;
    const episodeLabel = `S${String(seasonNumber).padStart(2, '0')}E${String(episodeNumber).padStart(2, '0')}`;

    const cleanedTitle = clean.replace(/\(\s*\)/g, '').replace(/\[\s*\]/g, '').replace(/\s+/g, ' ').trim();

    return {
      isSeries: true,
      canonicalTitle: canonical,
      seasonNumber,
      seasonLabel,
      episodeNumber,
      episodeLabel,
      cleanTitle: cleanedTitle,
    };
  }

  // Fallback: Check for SxxExx or 1x02 without strict leading delimiter
  const secondaryRegex = /\b[sS](\d{1,2})[eE](\d{1,3})\b/;
  const secMatch = clean.match(secondaryRegex);
  if (secMatch) {
    const sNum = parseInt(secMatch[1], 10) || 1;
    const eNum = parseInt(secMatch[2], 10) || 1;
    const beforePart = clean.substring(0, secMatch.index).trim();
    const canonical = cleanFileName(beforePart)
      .replace(/\b(19\d\d|20\d\d)\b/g, ' ')
      .replace(/[\(\)\[\]\{\}]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim() || 'Web Series';

    return {
      isSeries: true,
      canonicalTitle: canonical,
      seasonNumber: sNum,
      seasonLabel: `Season ${sNum}`,
      episodeNumber: eNum,
      episodeLabel: `S${String(sNum).padStart(2, '0')}E${String(eNum).padStart(2, '0')}`,
      cleanTitle: clean,
    };
  }

  return {
    isSeries: false,
    canonicalTitle: clean,
    seasonNumber: 1,
    seasonLabel: 'Season 1',
    episodeNumber: 1,
    episodeLabel: 'EP01',
    cleanTitle: clean,
  };
}

/**
 * Deep Module Main Entry:
 * Automatically drafts, organizes, and classifies web series by Season from an array of channel videos.
 */
export function draftWebSeriesBySeason(
  videos: CinemaVideo[],
  options?: DraftEngineOptions
): {
  drafts: WebSeriesDraft[];
  standaloneVideos: CinemaVideo[];
} {
  const seriesSortBy = options?.seriesSortBy || 'date-desc';
  const episodeSortBy = options?.episodeSortBy || 'episode-asc';
  const filterQuery = (options?.filterQuery || '').trim().toLowerCase();

  const rawShowsMap = new Map<string, { canonicalTitle: string; episodes: WebSeriesEpisode[] }>();
  const standaloneVideos: CinemaVideo[] = [];

  videos.forEach((video) => {
    const meta = extractSeriesMetadata(video.filename);
    const dateSec = normalizeDateToSeconds(video.date);

    if (meta.isSeries) {
      const key = meta.canonicalTitle.toLowerCase();
      if (!rawShowsMap.has(key)) {
        rawShowsMap.set(key, {
          canonicalTitle: meta.canonicalTitle,
          episodes: [],
        });
      }

      const ep: WebSeriesEpisode = {
        video: {
          ...video,
          series_name: meta.canonicalTitle,
          season: meta.seasonNumber,
          episode: meta.episodeNumber,
        },
        canonicalTitle: meta.canonicalTitle,
        seasonNumber: meta.seasonNumber,
        seasonLabel: meta.seasonLabel,
        episodeNumber: meta.episodeNumber,
        episodeLabel: meta.episodeLabel,
        displayTitle: meta.cleanTitle,
        releaseDate: dateSec,
        formattedDate: formatMediaDate(dateSec),
        durationFormatted: formatDuration(video.duration),
        sizeFormatted: formatBytes(video.size || video.file_size || 0),
      };

      rawShowsMap.get(key)!.episodes.push(ep);
    } else {
      standaloneVideos.push(video);
    }
  });

  // Fuzzy Merge: Combine shows sharing substring titles (e.g. "Game of Thrones" and "Game of Thrones 4k")
  const mergedShowsMap = new Map<string, { canonicalTitle: string; episodes: WebSeriesEpisode[] }>();
  const rawKeys = Array.from(rawShowsMap.keys()).sort((a, b) => b.length - a.length);

  rawKeys.forEach((key) => {
    const showData = rawShowsMap.get(key);
    if (!showData) return;

    let mergedIntoExisting = false;
    for (const [existingKey, existingData] of mergedShowsMap.entries()) {
      // If keys are very close or one is a clear substring of another
      if (existingKey === key || (existingKey.length > 3 && key.length > 3 && (existingKey.includes(key) || key.includes(existingKey)))) {
        existingData.episodes.push(...showData.episodes);
        if (showData.canonicalTitle.length > existingData.canonicalTitle.length) {
          existingData.canonicalTitle = showData.canonicalTitle;
        }
        mergedIntoExisting = true;
        break;
      }
    }

    if (!mergedIntoExisting) {
      mergedShowsMap.set(key, {
        canonicalTitle: showData.canonicalTitle,
        episodes: [...showData.episodes],
      });
    }
  });

  // Construct WebSeriesDraft objects
  const drafts: WebSeriesDraft[] = [];

  mergedShowsMap.forEach((show, showKey) => {
    if (show.episodes.length > 0) {
      const seasonMap = new Map<number, WebSeriesEpisode[]>();

      show.episodes.forEach((ep) => {
        if (!seasonMap.has(ep.seasonNumber)) {
          seasonMap.set(ep.seasonNumber, []);
        }
        seasonMap.get(ep.seasonNumber)!.push(ep);
      });

      // Sort episodes inside each season based on episodeSortBy
      const seasons: WebSeriesSeason[] = [];
      let seriesLatestDate = 0;
      let seriesEarliestDate = Number.MAX_SAFE_INTEGER;
      let seriesTotalBytes = 0;

      const sortedSeasonNumbers = Array.from(seasonMap.keys()).sort((a, b) => a - b);

      sortedSeasonNumbers.forEach((sNum) => {
        const eps = seasonMap.get(sNum) || [];

        eps.sort((a, b) => {
          if (episodeSortBy === 'date-desc') return b.releaseDate - a.releaseDate;
          if (episodeSortBy === 'date-asc') return a.releaseDate - b.releaseDate;
          if (episodeSortBy === 'episode-desc') return b.episodeNumber - a.episodeNumber;
          return a.episodeNumber - b.episodeNumber; // Default: episode-asc (1 -> N)
        });

        const seasonBytes = eps.reduce((sum, e) => sum + (e.video.size || e.video.file_size || 0), 0);
        const seasonDates = eps.map((e) => e.releaseDate).filter((d) => d > 0);
        const sLatest = seasonDates.length > 0 ? Math.max(...seasonDates) : 0;
        const sEarliest = seasonDates.length > 0 ? Math.min(...seasonDates) : 0;

        if (sLatest > seriesLatestDate) seriesLatestDate = sLatest;
        if (sEarliest > 0 && sEarliest < seriesEarliestDate) seriesEarliestDate = sEarliest;
        seriesTotalBytes += seasonBytes;

        seasons.push({
          seasonNumber: sNum,
          seasonLabel: `Season ${sNum}`,
          episodes: eps,
          totalEpisodes: eps.length,
          totalBytes: seasonBytes,
          latestDate: sLatest,
          earliestDate: sEarliest,
        });
      });

      if (seriesEarliestDate === Number.MAX_SAFE_INTEGER) seriesEarliestDate = 0;

      // Find first episode with a thumbnail to serve as series poster
      const leadThumb = show.episodes.find((e) => e.video.has_thumb || e.video.thumb_url)?.video.thumb_url;

      drafts.push({
        id: showKey,
        seriesTitle: show.canonicalTitle,
        totalSeasons: seasons.length,
        totalEpisodes: show.episodes.length,
        totalBytes: seriesTotalBytes,
        latestDate: seriesLatestDate,
        earliestDate: seriesEarliestDate,
        latestDateFormatted: formatMediaDate(seriesLatestDate),
        leadThumbUrl: leadThumb,
        seasons,
        activeSeasonNumber: seasons[0]?.seasonNumber || 1,
      });
    } else {
      // Single solitary episode without siblings treats as standalone
      standaloneVideos.push(...show.episodes.map((e) => e.video));
    }
  });

  // Apply search filtering on drafts if query is provided
  let filteredDrafts = drafts;
  if (filterQuery) {
    filteredDrafts = drafts.filter((d) =>
      d.seriesTitle.toLowerCase().includes(filterQuery) ||
      d.seasons.some((s) => s.episodes.some((e) => e.displayTitle.toLowerCase().includes(filterQuery)))
    );
  }

  // Sort Drafts based on seriesSortBy
  filteredDrafts.sort((a, b) => {
    if (seriesSortBy === 'date-desc') return b.latestDate - a.latestDate;
    if (seriesSortBy === 'date-asc') return a.latestDate - b.latestDate;
    if (seriesSortBy === 'title-asc') return a.seriesTitle.localeCompare(b.seriesTitle);
    if (seriesSortBy === 'episodes-desc') return b.totalEpisodes - a.totalEpisodes;
    return 0;
  });

  return {
    drafts: filteredDrafts,
    standaloneVideos,
  };
}
