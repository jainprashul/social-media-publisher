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

## Phase 5 Hermes & A2A Integration

Phase 5 connects A2A (agent-to-agent) generative media tasks with the durable media publishing pipeline, exposes Hermes agent-friendly workflow commands, links project manifests and publish receipts into Obsidian daily notes, and provides an operational nightly report.

### A2A Artifact Manifest Schema

Generative media tasks produce a schema-validated artifact manifest JSON before entering the pipeline.

```json
{
  "task_id": "task-a2a-gen-101",
  "agent": "gemini-video-agent",
  "project": "launch-campaign",
  "prompt_hash": "a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0",
  "context_id": "ctx-session-42",
  "model": "veo-2",
  "tool": "a2a_media_generator",
  "artifacts": [
    {
      "path": "projects/launch-campaign/reel.mp4",
      "kind": "video",
      "role": "master",
      "caption_path": "projects/launch-campaign/reel.vtt",
      "thumbnail_path": "projects/launch-campaign/poster.png",
      "model": "veo-2",
      "tool": "a2a_media_generator"
    }
  ],
  "created_at": "2026-09-23T04:00:00Z"
}
```

- **Validation rules:**
  - `task_id` and `agent` (or `source_agent`) are required and non-empty.
  - `prompt_hash` must be a valid 64-character hex SHA-256 string, or explicitly omitted (`null`).
  - At least one artifact is required.
  - Reject missing files, zero-byte files, duplicate ambiguous paths, workspace escapes (`..`), unsupported file types, and secret-bearing paths (e.g. `.env`, `.pem`, `.key`, `id_rsa`, `token`, `credentials`).
  - Every ingested artifact records `task_id`, `agent`, `prompt_hash`, `original_path`, `asset_id`, checksum, and ingestion timestamp.

### A2A CLI Commands

```bash
# Validate an artifact manifest against schema and security checks
media-pipeline a2a validate manifests/sample.json

# Ingest all artifacts from manifest (idempotent, never publishes automatically)
media-pipeline a2a ingest manifests/sample.json --project launch-campaign
```

### Hermes Workflow Commands

Hermes provides agent-friendly CLI wrappers for `preview`, `approve`, `publish`, `status`, and `ingest`. These return machine-readable JSON suitable for Hermes tool wrappers and strictly enforce Phase 3 approval gates:

```bash
# Ingest generated media manifest
media-pipeline hermes ingest manifests/sample.json

# Inspect job details and obtain fingerprint
media-pipeline hermes preview JOB_ID

# Check status, approval state, and reconciliation state
media-pipeline hermes status JOB_ID

# Explicitly approve with actor and matching fingerprint
media-pipeline hermes approve JOB_ID --actor hermes --fingerprint FINGERPRINT

# Dry-run publish without calling platform adapter
media-pipeline hermes publish JOB_ID --dry-run

# Execute verified publish (idempotent, reuses existing receipts)
media-pipeline hermes publish JOB_ID
```

- **Approval safety:** Public publishing cannot bypass approval. If a job is not approved, publishing fails immediately.
- **Reconciliation state:** If remote provider outcome is ambiguous (`unknown_remote`), Hermes commands report `reconciliation_required: true` and block uncoordinated retries.

### Obsidian Daily-Note Links

Generates Markdown link blocks for manifests and verified publish receipts:

```bash
media-pipeline obsidian daily --date 2026-09-23
```

- **Output boundary:** Daily notes default to an AI-owned directory (`data/obsidian/daily/{date}.md`) within the workspace.
- **Human-authored note protection:** The system never mutates or overwrites human-authored notes. Any pre-existing file without the explicit AI-owned marker `<!-- GENERATED BY DURABLE MEDIA PIPELINE (AI-OWNED) - DO NOT MANUALLY EDIT -->` is protected and will not be overwritten.
- **Relative links & escaping:** Paths with special characters or spaces are escaped safely.

### Operational Nightly Reports

The nightly report aggregates operational metrics across all assets, jobs, and platform adapters:

```bash
# Output JSON report to stdout
media-pipeline report nightly

# Output filtered by timestamp to a designated Markdown file
media-pipeline report nightly --since 2026-09-22T00:00:00Z --output reports/nightly.md --format markdown
```

- **Report sections:**
  1. Unpublished validated derivatives
  2. Failed jobs grouped by platform and error class
  3. Unknown remote states (requiring operator reconciliation)
  4. Published jobs missing verified receipts
  5. Orphan and drift records (missing files or SHA-256 hash changes)
  6. Recent verified publish receipts
  7. High-level summary metrics

### Observability & Security Guarantees

- **Provenance:** Structured logs in `data/logs/events.jsonl` record `task_id`, `agent`, `context_id`, `artifact_count`, and `workflow_command`.
- **Redaction:** Secrets (tokens, authorization headers, passwords, cookies, signed URL parameters like `x-amz-signature` or `token`) are automatically redacted before disk persistence or CLI display.
- **Local confinement:** Ingestion, derivatives, and reports are strictly confined within the configured workspace.

## Phase 6 Production Hardening

Phase 6 hardens the pipeline into an operable local service with reproducible packaging, CI quality gates, consistent database snapshots with checksums, operational diagnostics (`doctor`), opt-in live provider verification, scheduler-safe nightly reports, and bounded operational log rotation.

### 1. Packaging & Build Gates

- **Editable and Wheel Installations:** Package is configured via `pyproject.toml` using `setuptools.build_meta` with `durable_media` package discovery and CLI entry point `media-pipeline`.
- **Supported Python Versions:** Declared `>=3.11` (tested against Python 3.11 and 3.12).
- **Distribution Hygiene:** `MANIFEST.in` explicitly excludes runtime state (`data/`, `manifests/`, `projects/`, `*.sqlite3*`) and sensitive files (`.env*`, `*.key`, `*.pem`, `id_rsa*`).
- **Build Verification:**
  ```bash
  python -m pip install -e .
  python -m build
  ```

### 2. CI Quality Gates (`.github/workflows/ci.yml`)

The CI workflow automates multi-stage verification on every push and pull request:
- Multi-version matrix: Python 3.11 & 3.12 on Ubuntu Linux.
- Bytecode compile validation: `python -m compileall -q src tests`.
- Whitespace and diff hygiene: `git diff --check`.
- Secret and artifact hygiene scan: `scan_package` rejects committed `.env`, private keys, or SQLite databases.
- Offline pytest suite execution: 100% deterministic with zero external network access.
- Opt-in live provider checks: Run only when `DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1` is explicitly set in the workflow environment.

### 3. SQLite Backup and Safe Recovery

Database snapshots use SQLite's online backup API (`sqlite3.Connection.backup`) to guarantee consistent point-in-time state without locking out readers:
- **Sidecar SHA-256 generation:** Every backup produces a matching `<backup>.sha256` checksum sidecar.
- **Verification gate:** Checks file existence, checksum match, SQLite `PRAGMA integrity_check` / `quick_check`, and schema completeness.
- **Safe overwrite protection:** Restoring to an existing non-empty target database is refused unless `--force` is provided. Restores are performed atomically via a temporary file.

```bash
# Create an online SQLite backup snapshot with SHA-256 sidecar
media-pipeline backup create

# Create backup to a designated destination
media-pipeline backup create --output backups/registry_snapshot.sqlite3

# Verify backup integrity, checksum, and schema compatibility
media-pipeline backup verify backups/registry_snapshot.sqlite3

# Safely restore backup (requires --force if target already exists and is non-empty)
media-pipeline backup restore backups/registry_snapshot.sqlite3 --target-db data/registry.sqlite3 --force
```

### 4. Operational Diagnostics (`doctor`)

The `doctor` command inspects the host environment, workspace permissions, database integrity, schema migrations, profiles, adapter configuration readiness, and secret references without exposing sensitive values:

```bash
# Run local diagnostics (offline, safe for scripts and dashboards)
media-pipeline doctor
```

- **Output Structure:** Machine-readable JSON reporting `overall_status` (`healthy`, `warning`, `error`), Python environment, tool paths/versions (`ffmpeg`, `ffprobe`), workspace directory permissions, database counts and migration status, and adapter credential presence.
- **Secret Redaction:** Variable names and presence are reported (`source: env` or `source: missing`), but actual tokens or passwords are never displayed.

### 5. Provider Readiness & Opt-In Live Checks

- **Dry-run validation by default:** `media-pipeline target validate` operates purely offline, validating credential presence and target formatting with zero network calls.
- **Live check gate:** Live probes against external APIs require both the `--live` flag and the environment variable `DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1`. If the environment flag is missing, checks return `status: gated` and do not attempt network access.
- **Bounded timeouts & safe errors:** Live probes use a default 5-second timeout and report only `status` and `error_class` (e.g. `AuthenticationError`, `RateLimitError`), never sensitive payloads.

```bash
# Dry-run validation (default, offline)
media-pipeline target validate --platform instagram --destination 17841400000000000

# Opt-in live connectivity verification
export DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1
media-pipeline target validate --platform instagram --destination 17841400000000000 --live
media-pipeline doctor --live
```

### 6. Scheduler-Safe Nightly Reporting

Nightly reporting is engineered for unattended execution via external cron or systemd timers:
- **Advisory file locking:** Uses `FileLock` (`fcntl.flock`) on `data/locks/nightly_report.lock` to prevent overlapping runs.
- **Atomic replacement:** Writes reports to `.tmp.<pid>` before renaming into place (`os.replace`).
- **Human note protection:** Refuses to overwrite non-AI-owned Markdown notes.
- **Retention pruning:** Safely cleans older AI-owned reports according to `--retention-days`.

```bash
# Execute scheduled report with locking and atomic write
media-pipeline report nightly --output data/reports/nightly.md --format markdown --lock-timeout 10

# Crontab entry example (runs daily at 01:00 UTC)
# 0 1 * * * /usr/local/bin/media-pipeline --workspace /home/X/Playground/durable-media-publishing report nightly --output /home/X/Playground/durable-media-publishing/data/reports/nightly.md --format markdown >> /home/X/Playground/durable-media-publishing/data/logs/cron.log 2>&1
```

### 7. Bounded Operational Log Rotation

Structured operational events in `data/logs/events.jsonl` are size-bounded and strictly sanitized:
- **`JsonLogger` rotation:** Automatically rotates logs when reaching `max_bytes` (default 1 MB) keeping up to `backup_count` (default 5) archives (`.1`, `.2`, etc.).
- **Proactive redaction:** Bearer tokens, query parameters (`signature`, `token`), and credential headers are redacted before calculating size or persisting bytes.
- **Log inspection CLI:**
  ```bash
  media-pipeline logs inspect --limit 20
  ```

### Limitations

- Peer discovery and live A2A network protocols are deliberately not invoked in local execution; manifests are consumed locally.
- Approval fingerprints require exact matching; modifying destination, caption, schedule, or media file invalidates existing approval.
- Human-authored Obsidian notes cannot be appended to or merged into without an explicit AI-owned header.
- External provider sandbox/live checks require `DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1` and live credentials configured outside source code.
