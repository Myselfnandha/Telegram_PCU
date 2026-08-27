/**
 * Safe Preview Generator for Upload Queue Cards.
 * Manages object URLs with explicit garbage collection to prevent browser memory leaks on multi-GB files.
 */

import { getFileCategory } from './utils.js';

const activePreviews = new Map();

export function createSafePreview(fileId, file) {
  // If preview already exists for this file ID, return it
  if (activePreviews.has(fileId)) {
    return activePreviews.get(fileId);
  }

  const category = getFileCategory(file);
  let previewUrl = null;
  let hasObjectUrl = false;

  // Only create Object URLs for photos/small video thumbs to preserve RAM
  // For files > 100MB, avoid full video blob URLs in preview to prevent browser memory pressure
  if (category === 'photo' || (category === 'video' && file.size < 100 * 1024 * 1024)) {
    try {
      previewUrl = URL.createObjectURL(file);
      hasObjectUrl = true;
    } catch (e) {
      console.warn('Could not create Object URL preview:', e);
    }
  }

  const previewObj = {
    fileId,
    category,
    url: previewUrl,
    hasObjectUrl,
    revoke: () => {
      if (hasObjectUrl && previewUrl) {
        try {
          URL.revokeObjectURL(previewUrl);
        } catch (e) {
          console.warn('Error revoking Object URL:', e);
        }
        previewUrl = null;
        hasObjectUrl = false;
      }
      activePreviews.delete(fileId);
    }
  };

  activePreviews.set(fileId, previewObj);
  return previewObj;
}

export function revokePreview(fileId) {
  const p = activePreviews.get(fileId);
  if (p) {
    p.revoke();
  }
}

export function revokeAllPreviews() {
  activePreviews.forEach((p) => p.revoke());
  activePreviews.clear();
}
