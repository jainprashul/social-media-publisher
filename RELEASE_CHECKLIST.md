# Durable Media Publishing — Release & Provider Verification Checklist

This checklist documents requirements for promoting releases and verifying external platform adapters against provider sandboxes, test accounts, and production gates.

---

## 1. Automated Quality Gates

Before any release or deployment, all local gates must pass without network access:

- [ ] **Offline Test Suite:** `pytest -q` passes 100% (unit, integration, state machine, and error handling).
- [ ] **Bytecode Compilation:** `python -m compileall -q src tests` completes with zero syntax or compile errors.
- [ ] **Diff & Whitespace Hygiene:** `git diff --check` passes cleanly with no trailing whitespace or extra newlines at EOF.
- [ ] **Packaging Verification:** `pyproject.toml` defines correct metadata, scripts, and Python versions (`>=3.11`). Package builds cleanly via `python -m build` or `pip install -e .`.
- [ ] **Secret & File Scan:** `python -c "from durable_media.observability import scan_package; from pathlib import Path; assert not scan_package(Path('.'))"` confirms no `.env`, private keys (`.pem`, `.key`, `id_rsa`), or temporary databases are committed.
- [ ] **Diagnostics Health:** `media-pipeline doctor` returns `overall_status: healthy` (or `warning` only for optional local tools).

---

## 2. Platform Sandbox & Test Account Verification

Live platform verification is strictly opt-in and must only be performed against test/sandbox accounts using explicit credentials stored outside the repository (e.g. in `/root/.hermes/secrets/` with `0600` permissions).

Set `export DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1` prior to executing live readiness commands.

### 2.1 Instagram (Meta Graph API)

- **Target API:** Meta Graph API (`v21.0`)
- **Required Permissions:** `instagram_basic`, `instagram_content_publish`
- **Sandbox Setup:**
  - Create a Meta Developer App in "Business" mode.
  - Create a Test User and link a Test Instagram Business/Creator Account.
  - Generate a User Access Token with `instagram_content_publish`.
  - Store token: `export INSTAGRAM_ACCESS_TOKEN="<token>"` or save under `/root/.hermes/secrets/instagram_access_token`.
- **Pre-flight Check:**
  ```bash
  media-pipeline target validate --platform instagram --destination "<ig_user_id>" --live
  ```
- **Lifecycle Verification:**
  1. Initialize video container: `POST /{ig_user_id}/media` (`media_type=REELS`).
  2. Bounded polling: Poll `GET /{container_id}?fields=status_code` until `FINISHED` (timeout: 120s).
  3. Publish container: `POST /{ig_user_id}/media_publish`.
  4. Verify permalink: Confirm receipt contains valid post ID and `https://www.instagram.com/reel/{media_id}/`.

### 2.2 LinkedIn (Community Management / Posts API)

- **Target API:** LinkedIn Restli API / Posts API (`202401`)
- **Required Permissions:** `w_member_social`, `r_liteprofile` (or `w_organization_social`)
- **Sandbox Setup:**
  - Create a LinkedIn Developer Application in the Developer Portal.
  - Configure OAuth 2.0 3-legged authorization redirect for test account.
  - Store token: `export LINKEDIN_ACCESS_TOKEN="<token>"` or save under `/root/.hermes/secrets/linkedin_access_token`.
- **Pre-flight Check:**
  ```bash
  media-pipeline target validate --platform linkedin --destination "urn:li:person:<id>" --live
  ```
- **Lifecycle Verification:**
  1. Asset registration: Initialize upload (`initializeUpload` or `action=registerUpload`).
  2. Binary upload: PUT media bytes to signed upload URL with sanitized headers.
  3. Video readiness polling: Poll asset state until `AVAILABLE`.
  4. Create post: `POST /rest/posts` with valid author URN and distribution.
  5. Verify receipt: Confirm external ID (`urn:li:share:...` or `urn:li:post:...`) and post URL.

### 2.3 YouTube (YouTube Data API v3)

- **Target API:** YouTube Data API (`v3`)
- **Required Scopes:** `https://www.googleapis.com/auth/youtube.upload`
- **Sandbox Setup:**
  - Configure Google Cloud Project with YouTube Data API v3 enabled.
  - Create OAuth 2.0 Client credentials with Test Users configured.
  - Acquire refreshable OAuth access token.
  - Store token: `export YOUTUBE_ACCESS_TOKEN="<token>"` or save under `/root/.hermes/secrets/youtube_access_token`.
- **Pre-flight Check:**
  ```bash
  media-pipeline target validate --platform youtube --live
  ```
- **Lifecycle Verification:**
  1. Resumable session: `POST /upload/youtube/v3/videos?uploadType=resumable` (`privacyStatus=private` or `unlisted`).
  2. Chunked transfer: Send chunks via PUT; verify `308 Resume Incomplete` handling.
  3. Processing polling: Poll `GET /youtube/v3/videos?part=status&id={video_id}` until `processingStatus == succeeded`.
  4. Verify receipt: Confirm watch URL (`https://www.youtube.com/watch?v={video_id}`).

### 2.4 Discord (Bot & Webhook APIs)

- **Target API:** Discord API (`v10`)
- **Required Scopes / Permissions:** `Send Messages`, `Attach Files`, `Read Message History`
- **Sandbox Setup:**
  - Create a dedicated private Discord guild for staging and integration testing.
  - Create a test bot application or channel webhook.
  - Store token/webhook: `export DISCORD_BOT_TOKEN="<bot_token>"` and/or `export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."`.
- **Pre-flight Check:**
  ```bash
  media-pipeline target validate --platform discord --destination "<channel_id>" --live
  ```
- **Lifecycle Verification:**
  1. Multipart delivery: Send multipart request containing file attachment and JSON payload.
  2. Channel verification: Confirm message ID is returned.
  3. Receipt permanence: Confirm receipt includes `permanence: ephemeral_channel_retention` metadata.

---

## 3. Disaster Recovery & Rollback Verification

- [ ] **Snapshot Backup:** `media-pipeline backup create` generates verified SQLite snapshot and `.sha256` sidecar.
- [ ] **Checksum Verification:** `media-pipeline backup verify <path>` confirms integrity and schema consistency.
- [ ] **Safe Overwrite:** Attempting `media-pipeline backup restore <path>` against non-empty DB without `--force` is rejected.
- [ ] **Forced Restore:** Restoring with `--force` updates database atomically without partial writes.
- [ ] **Lock Concurrency:** Overlapping scheduled runs (`report nightly`) block with `SchedulerLockError` without corrupting state.
- [ ] **Atomic Replacement:** Interrupted or failing reports clean up temporary files without altering active files.
