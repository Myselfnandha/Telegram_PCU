# PRODUCT_USER_WORKFLOWS.md

## 1. Core User Journeys

### A. Telegram Account Setup
1. **User Action**: Provides session string/credentials.
2. **System Action**: Authenticates via Rust MTProto core.
3. **Success State**: Session established; accounts count updated.

### B. Cinema Discovery
1. **User Action**: Navigates to Library.
2. **System Action**: Fetches media index; displays continuous grid.
3. **Behavior**: Scrolling loads additional content incrementally.

### C. Media Playback
1. **User Action**: Selects media.
2. **System Action**: Initiates streaming; pre-warms buffer.
3. **Behavior**: VLC/Browser playback starts; buffer runway maintained at 20-24MB.

## 2. Failure & Recovery Paths
- **Connection Interruption**: Rust pacer handles flood cooldown; client should automatically retry once available.
- **Session Expiry**: User prompted to re-authorize through settings.
