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

The `FakePublisher` is deterministic and offline: `FakePublisher(mode="success")` supports `timeout`, `rate_limit`, `5xx`, `auth`, `validation`, `malformed`, and `unknown` outcomes, and `outcomes=[...]` sequences them while exposing `uploads` and `calls` for integration tests. Real platform adapters and credentials are intentionally out of scope.
