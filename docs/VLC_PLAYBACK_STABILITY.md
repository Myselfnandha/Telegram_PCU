# Stable VLC Playback (Arch Linux Desktop + LAN / QR)

This implementation restores VLC playback without restoring the removed remote-control stack.
The existing Telegram MTProto stream engine remains responsible for media retrieval and HTTP
Range/seek handling. VLC consumes the authenticated `/dl/...` endpoint.

## Desktop setup (Arch Linux)

Install VLC on the same Arch Linux desktop that runs TG Power Suite:

```bash
sudo pacman -S vlc
```

Restart TG Power Suite after updating the source. In Cinema, select **VLC** on a media card.
The backend starts VLC with network caching enabled and a local `127.0.0.1` media URL. If VLC
is unavailable, the UI displays the installation hint instead of silently failing.

## Phone / tablet / smart TV

1. Keep the device and TG Power Suite desktop on the same trusted Wi-Fi/LAN.
2. In Cinema, select **QR** for the video.
3. Scan the QR code. On Android, use **Open VLC**; on TVs or other devices, copy the URL into
   VLC's *Open Network Stream* dialog. **Open Stream** remains available for browser playback.
4. The generated playback URL is a media-scoped capability that expires after four hours.
   Generate a fresh QR/link after expiry.

The app uses the detected LAN IP and `PROXY_PORT`. The backend must listen on a LAN-reachable
interface for phone/TV playback; a loopback-only bind cannot be reached by other devices. Permit
the app port in the host firewall only on the trusted LAN profile. Do not expose the port directly
to the public internet.

## Security design

- VLC/mobile clients do not receive the permanent `API_AUTH_TOKEN`.
- A random, in-memory playback capability is scoped to one chat ID and message ID, and only
  authorizes GET/HEAD media requests plus the matching external-subtitle download.
- The capability does not authorize telemetry, system actions, other media, or API routes.
- Playback URLs expire after four hours and are redacted from application/Uvicorn logs.
- Desktop VLC launch is restricted to direct loopback requests and rejects forwarded/proxied calls.
- LAN playback is plain HTTP. Use it only on a trusted private network; do not reuse a playback
  URL as a long-lived share link.

## Stability defaults

VLC starts with `--network-caching=1500` and `--file-caching=1000` (milliseconds), HTTP reconnect
enabled, and the existing stream engine's byte-range support. These defaults favor steady startup
without making seeking excessively sluggish. High-latency Wi-Fi can benefit from increasing VLC's
network caching in VLC's preferences; very low values can cause stalls on congested links.

## Verification performed for this change

- Python syntax compilation: all backend application and test Python files compile.
- Focused capability tests: media binding, expiration, remote playback authorization, route scope,
  subtitle scope, and GET/HEAD-only policy pass.
- Full application tests and Vite build could not be reproduced in this isolated environment because
  runtime Python dependencies (`socketio`, etc.) are absent and `npm ci` did not finish. Run the full
  backend test suite and `npm run build` in the project's normal virtualenv/install environment before
  deploying.
