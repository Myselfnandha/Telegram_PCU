# KNOWN LIMITATIONS & OPERATIONAL CONSTRAINTS

**Project**: TG Power Suite  
**Date**: November 15, 2026  

---

## 1. Telegram Platform & Protocol Constraints

1. **Telegram File Size Limits**:
   - Standard Telegram accounts are limited to 2.0 GB per uploaded file.
   - Files larger than 2.0 GB are automatically sliced into `.part001`, `.part002`, etc., using zero-copy range splitting.
   - Telegram Premium accounts support up to 4.0 GB per file via MTProto.
2. **MTProto FloodWait Rate Limits**:
   - Excessive concurrent chunk downloads across multiple large videos can trigger Telegram `FloodWaitError` restrictions.
   - The integrated `AIMD` rate governor and Rust token bucket pacer throttle requests dynamically, but intensive bulk scraping should be paced.

---

## 2. Media Streaming & Player Specifics

1. **Multipart Byte Ranges (RFC 9110)**:
   - Media streaming endpoints (`/dl/` and `/stream/`) reject multi-range HTTP requests (`bytes=0-100, 200-300`) with `HTTP 416 Range Not Satisfiable`. Clients must request single continuous byte ranges.
2. **HEVC / H.265 in Web Browsers**:
   - Browsers lacking hardware HEVC/H.265 decoding support (e.g. certain Chromium builds on Linux) may fail to play HEVC MKV files directly in the HTML5 video element.
   - **Recommended Solution**: Use the 1-click **Play in VLC** or **Play in MPV** button in the Cinema View, which invokes native desktop players supporting all video/audio codecs.
3. **Embedded Subtitle Extraction**:
   - Extraction of subtitles from dense multi-gigabyte MKV files requires prefetching the MKV track header and cluster indexes. If the file has non-interleaved subtitle tracks, extracting WebVTT subtitles may require streaming up to 2-5% of the file before playback starts.

---

## 3. Deployment & Environment Bounds

1. **Memory Budget**:
   - The in-memory sliding buffer (`StreamSlidingBuffer`) enforces a strict 256 MB RAM ceiling. On low-memory environments (e.g. 512MB VPS), operating multiple concurrent 4K streams simultaneously may lead to rapid LRU slice turnover.
2. **Disk Cache Storage**:
   - The default block cache ceiling is set to 10 GB. The emergency disk guard triggers when free filesystem space drops below 2.0 GB or the cache exceeds 10 GB, evicting least-recently-used 512KB slices.
