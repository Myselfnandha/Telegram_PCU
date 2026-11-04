/**
 * Movie Draft Engine (Deep Module)
 *
 * Automatically detects movie franchises, sequels, sagas, and release year groupings
 * from Telegram video archives behind a clean, focused public interface.
 */

import type { CinemaVideo } from '@/types';
import { cleanFileName, formatBytes, formatDuration } from '@/lib/formatters';
import { formatMediaDate, normalizeDateToSeconds } from '@/lib/seriesDraftEngine';

export interface MovieDraftItem {
  video: CinemaVideo;
  cleanTitle: string;
  franchiseTitle: string;
  year?: number;
  partNumber?: number;
  resolution?: string;
  audio?: string;
  releaseDate: number; // Unix timestamp in seconds
  formattedDate: string;
  durationFormatted: string;
  sizeFormatted: string;
}

export interface MovieCollectionDraft {
  id: string; // Lowercase slug / canonical key
  collectionTitle: string;
  type: 'franchise' | 'year_group';
  totalMovies: number;
  totalBytes: number;
  latestDate: number; // Unix timestamp of newest movie post
  earliestDate: number;
  latestDateFormatted: string;
  latestYear?: number;
  earliestYear?: number;
  leadThumbUrl?: string;
  movies: MovieDraftItem[];
}

export type MovieSortOption = 'date-desc' | 'date-asc' | 'year-desc' | 'year-asc' | 'title-asc' | 'size-desc';

export interface MovieDraftOptions {
  sortBy?: MovieSortOption;
  filterQuery?: string;
}

/**
 * Extracts the canonical franchise root title, release year, and sequel part from a movie title.
 */
export function extractMovieMetadata(filename: string): {
  cleanTitle: string;
  franchiseTitle: string;
  year?: number;
  partNumber?: number;
} {
  const clean = cleanFileName(filename) || filename;

  // 1. Extract 4-digit Year (1900 - 2099)
  let year: number | undefined;
  const yearMatch = clean.match(/(?:[\(\[\{])?\b(19\d{2}|20\d{2})\b(?:[\)\]\}])?/);
  if (yearMatch) {
    year = parseInt(yearMatch[1], 10);
  }

  // 2. Extract Sequel / Part Number: Part 1, Part 2, Chapter 3, Part II, Part III, Part IV, 2, 3
  let partNumber: number | undefined;
  const partMatch = clean.match(/\b(?:part|chapter|vol(?:ume)?|pt)\s*[:.\-_]?\s*(\d{1,2}|[ivx]+)\b/i);
  if (partMatch) {
    const rawVal = partMatch[1].toLowerCase();
    if (/^\d+$/.test(rawVal)) {
      partNumber = parseInt(rawVal, 10);
    } else {
      const romanMap: Record<string, number> = { i: 1, ii: 2, iii: 3, iv: 4, v: 5, vi: 6, vii: 7, viii: 8, ix: 9, x: 10 };
      partNumber = romanMap[rawVal] || 1;
    }
  }

  // 3. Derive Canonical Franchise Root Title
  // Strip year, resolution, tags, and sequel markers
  let working = clean
    .replace(/\b(19\d\d|20\d\d)\b/g, ' ')
    .replace(/\b(2160p|4k|1080p|720p|480p|uhd|fhd|hd|hdr|remux|bluray|web-dl|webrip|x264|x265|hevc|ddp|atmos|esub)\b/gi, ' ')
    .replace(/\b(?:part|chapter|vol(?:ume)?|pt)\s*[:.\-_]?\s*(\d{1,2}|[ivx]+)\b/gi, ' ')
    .replace(/[\(\)\[\]\{\}]/g, ' ')
    .replace(/^(?:org|com|net|tv|hd|link|linkzz|official|tamilmv|tamilblasters)\s+/i, '')
    .trim();

  // Strip subtitle trailing after colon or dash (e.g. "Avatar: The Way of Water" -> "Avatar", "John Wick: Chapter 4" -> "John Wick")
  let franchise = working;
  const colonIdx = working.indexOf(':');
  if (colonIdx > 2) {
    franchise = working.substring(0, colonIdx).trim();
  } else {
    const dashIdx = working.indexOf(' - ');
    if (dashIdx > 2) {
      franchise = working.substring(0, dashIdx).trim();
    }
  }

  // Strip trailing standalone number (e.g. "Deadpool 2" -> "Deadpool", "Iron Man 3" -> "Iron Man")
  const trailingNumMatch = franchise.match(/^(.*?)\s+(\d{1,2})$/);
  if (trailingNumMatch && trailingNumMatch[1].length >= 3) {
    franchise = trailingNumMatch[1].trim();
    if (!partNumber) {
      partNumber = parseInt(trailingNumMatch[2], 10);
    }
  }

  // Fallback: cleanup extra spaces
  franchise = franchise.replace(/\s+/g, ' ').trim() || clean;

  return {
    cleanTitle: clean,
    franchiseTitle: franchise,
    year,
    partNumber,
  };
}

/**
 * Deep Module Main Entry:
 * Automatically drafts movie franchises, sagas, and year collections from standalone videos.
 */
export function draftMoviesByFranchiseAndYear(
  videos: CinemaVideo[],
  options?: MovieDraftOptions
): {
  franchises: MovieCollectionDraft[];
  yearCollections: MovieCollectionDraft[];
  allMovieDrafts: MovieDraftItem[];
} {
  const sortBy = options?.sortBy || 'date-desc';
  const filterQuery = (options?.filterQuery || '').trim().toLowerCase();

  // 1. Filter out series and map to MovieDraftItems
  const movieItems: MovieDraftItem[] = [];

  videos.forEach((video) => {
    // If it's a TV series episode, ignore for movie drafting
    if (video.is_series || video.season || video.series_name) return;

    const meta = extractMovieMetadata(video.filename);
    const dateSec = normalizeDateToSeconds(video.date);

    movieItems.push({
      video,
      cleanTitle: meta.cleanTitle,
      franchiseTitle: meta.franchiseTitle,
      year: meta.year,
      partNumber: meta.partNumber,
      resolution: video.resolution,
      audio: undefined,
      releaseDate: dateSec,
      formattedDate: formatMediaDate(dateSec),
      durationFormatted: formatDuration(video.duration),
      sizeFormatted: formatBytes(video.size || video.file_size || 0),
    });
  });

  // 2. Cluster into Franchises (where 2 or more movies share the same franchise root title)
  const rawFranchiseMap = new Map<string, MovieDraftItem[]>();

  movieItems.forEach((item) => {
    const key = item.franchiseTitle.toLowerCase();
    if (!rawFranchiseMap.has(key)) {
      rawFranchiseMap.set(key, []);
    }
    rawFranchiseMap.get(key)!.push(item);
  });

  // Fuzzy Merge Franchises sharing substring keys (e.g. "Avatar" & "Avatar The Way of Water")
  const mergedFranchiseMap = new Map<string, { title: string; items: MovieDraftItem[] }>();
  const franchiseKeys = Array.from(rawFranchiseMap.keys()).sort((a, b) => b.length - a.length);

  franchiseKeys.forEach((key) => {
    const items = rawFranchiseMap.get(key) || [];
    let merged = false;

    for (const [existingKey, existingData] of mergedFranchiseMap.entries()) {
      if (existingKey === key || (existingKey.length > 3 && key.length > 3 && (existingKey.includes(key) || key.includes(existingKey)))) {
        existingData.items.push(...items);
        merged = true;
        break;
      }
    }

    if (!merged) {
      mergedFranchiseMap.set(key, {
        title: items[0]?.franchiseTitle || key,
        items: [...items],
      });
    }
  });

  const franchises: MovieCollectionDraft[] = [];

  mergedFranchiseMap.forEach((fData, fKey) => {
    // A franchise requires 2 or more movies
    if (fData.items.length >= 2) {
      // Sort movies within franchise: by part number or release year or date
      fData.items.sort((a, b) => {
        if (a.partNumber && b.partNumber) return a.partNumber - b.partNumber;
        if (a.year && b.year) return a.year - b.year;
        return a.releaseDate - b.releaseDate;
      });

      const totalBytes = fData.items.reduce((acc, m) => acc + (m.video.size || m.video.file_size || 0), 0);
      const dates = fData.items.map((m) => m.releaseDate).filter((d) => d > 0);
      const years = fData.items.map((m) => m.year).filter((y): y is number => Boolean(y));
      const latestDate = dates.length > 0 ? Math.max(...dates) : 0;
      const earliestDate = dates.length > 0 ? Math.min(...dates) : 0;
      const leadThumb = fData.items.find((m) => m.video.thumb_url)?.video.thumb_url;

      franchises.push({
        id: `franchise_${fKey}`,
        collectionTitle: `${fData.title} Franchise`,
        type: 'franchise',
        totalMovies: fData.items.length,
        totalBytes,
        latestDate,
        earliestDate,
        latestDateFormatted: formatMediaDate(latestDate),
        latestYear: years.length > 0 ? Math.max(...years) : undefined,
        earliestYear: years.length > 0 ? Math.min(...years) : undefined,
        leadThumbUrl: leadThumb,
        movies: fData.items,
      });
    }
  });

  // 3. Cluster Standalone Movies into Release Year Groups (e.g. 2026, 2025, 2024, 2023...)
  const yearMap = new Map<string, MovieDraftItem[]>();

  movieItems.forEach((m) => {
    const yrLabel = m.year ? `${m.year}` : 'Archive Collection';
    if (!yearMap.has(yrLabel)) {
      yearMap.set(yrLabel, []);
    }
    yearMap.get(yrLabel)!.push(m);
  });

  const yearCollections: MovieCollectionDraft[] = [];

  // Sort year keys descending (2026, 2025, 2024...)
  const sortedYearKeys = Array.from(yearMap.keys()).sort((a, b) => {
    const numA = parseInt(a, 10) || 0;
    const numB = parseInt(b, 10) || 0;
    return numB - numA;
  });

  sortedYearKeys.forEach((yKey) => {
    const items = yearMap.get(yKey) || [];
    items.sort((a, b) => b.releaseDate - a.releaseDate);

    const totalBytes = items.reduce((acc, m) => acc + (m.video.size || m.video.file_size || 0), 0);
    const dates = items.map((m) => m.releaseDate).filter((d) => d > 0);
    const latestDate = dates.length > 0 ? Math.max(...dates) : 0;
    const earliestDate = dates.length > 0 ? Math.min(...dates) : 0;
    const leadThumb = items.find((m) => m.video.thumb_url)?.video.thumb_url;

    yearCollections.push({
      id: `year_${yKey.toLowerCase().replace(/\s+/g, '_')}`,
      collectionTitle: yKey === 'Archive Collection' ? yKey : `${yKey} Movies`,
      type: 'year_group',
      totalMovies: items.length,
      totalBytes,
      latestDate,
      earliestDate,
      latestDateFormatted: formatMediaDate(latestDate),
      latestYear: parseInt(yKey, 10) || undefined,
      earliestYear: parseInt(yKey, 10) || undefined,
      leadThumbUrl: leadThumb,
      movies: items,
    });
  });

  // Apply sorting
  const sortCollectionFn = (a: MovieCollectionDraft, b: MovieCollectionDraft) => {
    if (sortBy === 'date-desc') return b.latestDate - a.latestDate;
    if (sortBy === 'date-asc') return a.latestDate - b.latestDate;
    if (sortBy === 'year-desc') return (b.latestYear || 0) - (a.latestYear || 0);
    if (sortBy === 'year-asc') return (a.latestYear || 0) - (b.latestYear || 0);
    if (sortBy === 'title-asc') return a.collectionTitle.localeCompare(b.collectionTitle);
    if (sortBy === 'size-desc') return b.totalBytes - a.totalBytes;
    return 0;
  };

  franchises.sort(sortCollectionFn);
  yearCollections.sort(sortCollectionFn);

  // Filter if query is provided
  let filteredFranchises = franchises;
  let filteredYearCollections = yearCollections;

  if (filterQuery) {
    filteredFranchises = franchises.filter((f) =>
      f.collectionTitle.toLowerCase().includes(filterQuery) ||
      f.movies.some((m) => m.cleanTitle.toLowerCase().includes(filterQuery))
    );
    filteredYearCollections = yearCollections.filter((y) =>
      y.collectionTitle.toLowerCase().includes(filterQuery) ||
      y.movies.some((m) => m.cleanTitle.toLowerCase().includes(filterQuery))
    );
  }

  return {
    franchises: filteredFranchises,
    yearCollections: filteredYearCollections,
    allMovieDrafts: movieItems,
  };
}
