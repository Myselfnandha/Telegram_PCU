# TG Power Suite v2.0-RC — Source Verification Audit

**Reviewed artifact:** `/mnt/data/tg_power_suite_rc_v3.zip`  
**Reported release commit:** `4495be2` on `audit-remediation-v3`  
**Review type:** Static source/archive inspection plus attempted local test execution  
**Scope limitation:** The archive does not include a `.git` directory. No Telegram credentials or external tunnel were available, and the full test suite could not be reproduced in this environment.

## Executive assessment

**Recommendation: do not sign off on production release yet.** The archive contains a remote authentication exposure in the middleware allowlist, release-artifact hygiene/traceability concerns, and clean-install test/runtime dependency problems. Several tests also do not measure the behavior claimed by their names or by the certification summary.

## Findings

### P1 — Sensitive telemetry and network details bypass global authentication

**Evidence**

- `backend/app/main.py`, `GlobalAuthEnforcementMiddleware.PUBLIC_PATHS` (around lines 352–355) exempts `/api/system/stats`, `/api/system/network`, and `/api/system/qrcode` before the authentication decision.
- `backend/app/routes/system.py` returns detailed metrics from `/stats`, local interface/IP data and tunnel status from `/network`, and an externally supplied QR payload from `/qrcode`.
- `get_system_telemetry()` includes `user_name`, `user_id`, active upload/stream counts, system memory/CPU metrics, and server timing fields. `get_network_info()` returns LAN/interface addresses and the active Cloudflare tunnel URL.

**Impact**

These endpoints remain accessible without credentials even when `API_AUTH_TOKEN` is configured, and even when the server has no token configured. This contradicts the stated default-deny policy for remote traffic and exposes operational/network metadata.

**Recommended fix**

Remove these endpoints from `PUBLIC_PATHS` and protect them with `Depends(verify_auth)` or an equivalent middleware policy. If a public health check is needed, add a separate minimal endpoint that reveals no user, network, tunnel, or operational details. Add tests for each endpoint with: no token, invalid token, valid token, token configured, and token unset with a simulated remote peer.

### P1 — Archive source commit does not match the reported RC commit

**Evidence**

The ZIP archive comment is `3cf7b544e1a1b7ab01bd1788c859cec01bb467c3`, while the release report identifies `4495be2` as `HEAD`.

**Impact**

The archive's embedded Git archive identifier does not match the reported commit prefix. The reviewed source therefore cannot be assumed to be the exact reported release candidate without reconciliation.

**Recommended fix**

Recreate the archive explicitly from the intended commit, e.g. `git archive --format=zip --output=tg_power_suite_rc_v3.zip 4495be2`, then inspect the ZIP comment and verify it starts with the expected full commit ID. Record the SHA-256 of the final archive in release notes.

### P1 — The purported clean archive contains runtime artifacts

**Evidence**

The archive includes:

- `backend/data/tg_power_suite.log.1`, `.2`, `.3` (about 20 MB combined)
- `backend/data/tg_power_suite.db`
- `backend/data/subtitles/` with media subtitle files
- `backend/data/bin/cloudflared` (a bundled x86-64 ELF executable)
- `backend/backups/audit_12_files/` containing older source snapshots

The `.gitignore` excludes `backend/data`, but tracked files are still included by `git archive`. The archive's uncompressed contents total about 75.7 MB. One archived log contains a Cloudflare Quick Tunnel URL.

**Impact**

The archive is not clean in the release-hygiene sense. Logs can disclose network/tunnel details, and subtitle/database artifacts are runtime/user data that should not be shipped by default. The bundled executable also needs explicit provenance, version, platform, and license handling.

**Recommended fix**

Untrack runtime logs, database files, generated subtitles, and other local artifacts. Keep only deliberately vendored binaries, with source/version/checksum/license recorded. Build the release from a sanitized staging directory and inspect the final archive contents before publication. Do not rely on `.gitignore` alone to exclude already tracked files.

### P1 — Test configuration fails in the review environment; dependencies are not fully declared

**Evidence**

- `pytest.ini` filters `starlette.exceptions.StarletteDeprecationWarning`. In this environment, pytest aborts while parsing the configuration because that attribute is absent from the installed Starlette version.
- `backend/tests/test_production_verification.py` imports `numpy`, but `numpy` is absent from `backend/requirements.txt`.
- `backend/app/services/network_service.py` imports `qrcode`, but `qrcode` is absent from `backend/requirements.txt`.
- After bypassing `pytest.ini`, collection still cannot complete here because runtime dependencies including `telethon` and `python-socketio` are not installed in the review environment. Therefore the claimed 128-pass run was not independently reproduced.

**Impact**

The zero-warning result is environment-dependent, and a clean installation based only on the declared requirements may fail to run the test suite or QR/network endpoints.

**Recommended fix**

Declare all direct runtime and test dependencies (`qrcode`, `numpy`, and any other direct imports missing from requirements). Pin or constrain compatible FastAPI/Starlette versions, or use a warning filter that does not fail when a warning class is unavailable. Run CI in a fresh environment using only the declared dependency files.

### P2 — Performance certification does not measure media TTFB or seek latency

**Evidence**

`backend/tests/test_production_verification.py` lines around 281–299 measures 25 calls to `/api/system/stats`; the test comment itself describes it as a representative fast internal endpoint. It does not request a media stream or issue HTTP range seeks. The rate-governor test only makes 20 local `acquire_chunk_slot()` calls and checks state; it does not transfer media or inject FloodWait responses.

**Impact**

The test supports a claim about a lightweight API endpoint, not the reported media TTFB/seek latency or sustained throughput. The performance certification language is stronger than the measured evidence.

**Recommended fix**

Create a repeatable media benchmark that measures first media byte, range-seek latency, throughput, underruns, errors, and memory under stated cache states and concurrency. Separate unit tests of the governor from end-to-end network/Telegram performance tests.

### P2 — Empty-file Range handling does not match the stated edge-case coverage

**Evidence**

In `backend/app/routes/proxy.py`, `parse_http_range()` returns status `200` immediately when `file_size <= 0`, even if a Range header is supplied. The certification test shown in `test_production_verification.py` tests a missing Range header on a 5,000-byte file; it does not test an empty file.

**Impact**

The reported “empty media”/range edge-case coverage is not present, and a range request against a zero-byte resource is treated as a full `200` response rather than being evaluated as unsatisfiable.

**Recommended fix**

Handle the no-Range and zero-size cases separately. Add tests for a zero-byte file with no Range header, `bytes=0-`, suffix ranges, malformed ranges, `HEAD`, and exact `Content-Range`/`Content-Length` behavior.

### P2 — Sensitive-data redaction is not proven by tests and has narrow token matching

**Evidence**

`SensitiveDataFilter` in `backend/app/main.py` matches query keys `token`, `apiKey`, and `api_key`, and Bearer values matching a limited character class with a minimum length of six. No test in the archive exercises the filter across file, console, and Uvicorn access logging.

**Impact**

The claim that tokens are never logged is not established by the current test suite. Short or unusually formatted Bearer tokens may not be fully redacted, and future credential parameter names could bypass the current expression.

**Recommended fix**

Prefer structured logging that omits credential-bearing fields at source. Add tests using short tokens, padded/base64-like tokens, URL-encoded query strings, formatted logging arguments, exceptions, and the actual Uvicorn access-log format. Verify that no original secret substring appears in any handler output.

## Verification performed

- Archive opened and inspected; 407 entries found.
- Python syntax compilation: 70 Python files, 0 syntax errors.
- Default `pytest` invocation: failed during configuration parsing because `starlette.exceptions.StarletteDeprecationWarning` is unavailable in the review environment.
- Test invocation bypassing `pytest.ini`: collection failed because required dependencies (including `telethon` and `socketio`) are not installed in the review environment.
- No live Telegram, LAN, or Cloudflare Tunnel behavior was independently exercised.

## Release gates

1. Close the public telemetry/network authentication gap.
2. Rebuild and verify the archive from the exact intended commit.
3. Remove runtime logs/database/subtitle artifacts from the release archive.
4. Declare and test all dependencies from a clean install.
5. Replace the current API-latency proxy benchmark with actual media TTFB/seek/throughput tests.
6. Add direct tests for log redaction and empty-file Range semantics.

**Final disposition: RC needs remediation and a fresh verification run before production sign-off.**
