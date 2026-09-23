# Durable Media Publishing

Local-first, auditable media pipeline. Phase 2 adds safe ffmpeg/ffprobe-backed derivatives while preserving immutable masters and the Phase 1 approval/publishing flow. Every derivative and preview is hash-addressed in SQLite with profile, command, tool-version, metadata, and provenance.

## Setup

```bash
python -m pip install -e .
media-pipeline --workspace ./demo init
media-pipeline --workspace ./demo ingest ./clip.mp4 --project demo --source a2a --source-task task-1
media-pipeline --workspace ./demo derive ASSET_ID --profile instagram_reel_v1
media-pipeline --workspace ./demo thumbnail DERIVATIVE_ID
media-pipeline --workspace ./demo contact-sheet DERIVATIVE_ID
media-pipeline --workspace ./demo validate DERIVATIVE_ID
```

Profiles are versioned: `instagram_reel_v1`, `youtube_short_v1`, `linkedin_video_v1`, `discord_preview_v1`, and `audio_voiceover_v1`. Use `profile show` to inspect constraints and fingerprints. Caption files can be registered with `caption register PARENT_ID captions.srt`; existing WAV/MP3 files can be registered with `voiceover register PARENT_ID voice.wav --model MODEL --tool TOOL` (synthesis is intentionally not included).

FFmpeg invocations use argument arrays, bounded execution, workspace checks, atomic temporary outputs, and redacted captured provenance. Undecodable generic files retain Phase 1 copy behavior; valid media is rendered and validated against its profile. No credentials or network publishing are used; the bundled publisher remains fake and real platform adapters remain future work.

## Phase 3 publishing state machine

Publishing is persisted in SQLite. Jobs move through `awaiting_approval -> approved -> queued -> uploading -> processing -> publishing -> verifying -> published`; retryable failures enter `failed_retryable` with a persisted, bounded exponential backoff, permanent failures enter `failed_permanent`, and ambiguous provider responses enter `unknown_remote` until an operator reconciles them. Every transition records an actor, reason, attempt, retryability, timestamp, and redacted metadata. Approval is a fingerprint of the exact derivative checksum, platform, destination, caption, visibility, and schedule; changing any of these invalidates it.

Use `preview JOB` or `status JOB` to inspect the complete payload, approval, transitions, retry schedule, and verified receipt. `publish JOB --dry-run` validates and previews without invoking an adapter or creating a receipt. Reconcile an unknown result explicitly: `reconcile JOB --outcome published --operator NAME --reason "verified in provider UI"` (the outcomes are `published`, `not-published`, and `retry`). `failures --since ISO_TIMESTAMP` lists persisted failures and unknown remote jobs.

The `FakePublisher` is deterministic and offline: `FakePublisher(mode="success")` supports `timeout`, `rate_limit`, `5xx`, `auth`, `validation`, `malformed`, and `unknown` outcomes, and `outcomes=[...]` sequences them while exposing `uploads` and `calls` for integration tests.

## Phase 4 Platform Adapters

Phase 4 introduces isolated platform adapters implementing the common `Publisher` protocol:

- **Instagram (`InstagramPublisher`)**: Implements Meta Graph API Reels and video container creation, media upload, bounded processing-status polling (`FINISHED`, `IN_PROGRESS`, `ERROR`, `EXPIRED`), publishing, and permalink verification (`https://www.instagram.com/reel/{media_id}/`). Preserves container ID, media ID, status, and permalink.
- **LinkedIn (`LinkedInPublisher`)**: Implements LinkedIn Community Management / Posts API with separated text-only and media flows (images and videos). Handles asset registration (`initializeUpload`), binary upload, video readiness polling (`AVAILABLE`), post creation, and verification (`https://www.linkedin.com/feed/update/{post_urn}`). Rejects unsupported content combinations (such as audio-only uploads) before upload.
- **YouTube (`YouTubePublisher`)**: Implements YouTube Data API v3 resumable video uploads with multi-chunk transfer, `308 Resume Incomplete` handling, resume inquiry, video processing polling, thumbnail attachment, and watch URL verification (`https://www.youtube.com/watch?v={video_id}`). Preserves session URLs and video IDs without logging credentials.
- **Discord (`DiscordPublisher`)**: Implements webhook or bot-based channel message and attachment delivery with channel validation and message verification (`https://discord.com/channels/{guild_id}/{channel_id}/{message_id}`). Delivery receipts explicitly document ephemeral channel retention limits (`permanence: ephemeral_channel_retention`).

### Security & Secret References

All adapter credentials use `SecretRef` objects rather than raw tokens:
- Environment variable references: `INSTAGRAM_ACCESS_TOKEN`, `LINKEDIN_ACCESS_TOKEN`, `YOUTUBE_ACCESS_TOKEN`, `DISCORD_BOT_TOKEN`, `DISCORD_WEBHOOK_URL`.
- File path references: credential files (e.g. `/root/.hermes/secrets/`) with restrictive permissions.
- Redaction guarantee: tokens are never printed in `repr()`, string representations, logs, manifests, SQLite transitions, or exception messages.

### CLI Target Inspection & Validation

```bash
# List supported platforms and their configuration readiness
media-pipeline targets

# Validate a target adapter configuration without network calls
media-pipeline target validate --platform instagram --destination 17841400000000000
media-pipeline target validate --platform linkedin --destination urn:li:person:12345
media-pipeline target validate --platform youtube
media-pipeline target validate --platform discord --destination 123456789012345678
```

### Offline Testing & Transports

All adapters accept an injectable `HttpTransport`. The test suite uses `FakeHttpTransport` exclusively:
- Zero external network calls are made during test execution.
- Tests deterministically simulate upload chunking, 308 resumes, polling delays, rate limits (HTTP 429), timeouts (HTTP 408/504), server errors (5xx), authentication failures (401/403), validation errors (400/422), malformed responses, and receipt verifications.
