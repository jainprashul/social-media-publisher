# Durable Media Publishing

A local-first, auditable pipeline for ingesting generated media, creating validated derivatives, approving content, publishing through isolated platform adapters, and recording verifiable receipts.

The project is designed for Hermes/A2A-generated images, video, audio, captions, thumbnails, and cross-platform publishing. Masters are immutable, every artifact is hashed, and public publishing requires explicit approval.

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` for media rendering and metadata extraction
- SQLite (included with Python)
- Optional: `setuptools` and `wheel` for package builds
- Optional: platform credentials for live publishing; offline tests never require them

Check local dependencies:

```bash
python --version
ffmpeg -version
ffprobe -version
```

## Installation

From the repository root:

```bash
python -m pip install -e .
```

This installs the `media-pipeline` command. If you do not install the package, commands can be run with:

```bash
PYTHONPATH=src python -m durable_media.cli --help
```

Initialize a workspace:

```bash
media-pipeline --workspace ./demo init
```

The workspace contains local state and generated artifacts:

```text
demo/
├── data/
│   ├── assets/       immutable masters
│   ├── derivatives/  rendered platform outputs
│   ├── previews/    thumbnails and contact sheets
│   ├── receipts/    optional receipt files
│   ├── logs/        structured JSONL events
│   └── registry.sqlite3
├── manifests/       project and A2A manifests
└── projects/        optional source/project files
```

Do not put credentials in the project directory. Use environment variables or secret-file references.

## Basic media workflow

Ingest a source file. A2A provenance can be attached with `--source`, `--source-task`, and `--prompt`:

```bash
media-pipeline --workspace ./demo ingest ./clip.mp4 \
  --project launch-reel \
  --role master \
  --source a2a \
  --source-task task-123
```

The command returns an `asset_id`. Create a derivative using a versioned profile:

```bash
media-pipeline --workspace ./demo derive ASSET_ID \
  --profile instagram_reel_v1
```

Validate and create previews:

```bash
media-pipeline --workspace ./demo validate DERIVATIVE_ID
media-pipeline --workspace ./demo thumbnail DERIVATIVE_ID
media-pipeline --workspace ./demo contact-sheet DERIVATIVE_ID
```

Available profiles:

- `instagram_reel_v1` — vertical H.264/AAC video
- `youtube_short_v1` — vertical H.264/AAC video
- `linkedin_video_v1` — landscape H.264/AAC video
- `discord_preview_v1` — size-constrained preview video
- `audio_voiceover_v1` — normalized audio artifact

Inspect profile constraints:

```bash
media-pipeline --workspace ./demo profile show
```

Register existing caption or voiceover artifacts:

```bash
media-pipeline --workspace ./demo caption register DERIVATIVE_ID captions.srt
media-pipeline --workspace ./demo voiceover register ASSET_ID voice.wav \
  --model voice-model \
  --tool tts-tool
```

Voice synthesis is intentionally not included; existing WAV/MP3/M4A files can be registered with provenance.

## Approval and publishing workflow

Prepare a publish job:

```bash
media-pipeline --workspace ./demo prepare DERIVATIVE_ID \
  --platform fake \
  --destination test-account \
  --caption "Launch update #ai" \
  --visibility public
```

Preview the job and copy its fingerprint:

```bash
media-pipeline --workspace ./demo preview JOB_ID
```

Approve the exact fingerprint:

```bash
media-pipeline --workspace ./demo approve JOB_ID \
  --actor prashul \
  --fingerprint FINGERPRINT
```

Run a safe dry-run first, then publish:

```bash
media-pipeline --workspace ./demo publish JOB_ID --dry-run
media-pipeline --workspace ./demo publish JOB_ID
```

Inspect status and transitions:

```bash
media-pipeline --workspace ./demo status JOB_ID
media-pipeline --workspace ./demo failures --since 2026-01-01T00:00:00Z
```

Publishing is idempotent. A verified receipt is returned instead of uploading again. Retryable failures are persisted with bounded backoff. Unknown remote outcomes require explicit reconciliation:

```bash
media-pipeline --workspace ./demo reconcile JOB_ID \
  --outcome published \
  --operator prashul \
  --reason "Verified in provider dashboard"
```

Supported reconciliation outcomes are `published`, `not-published`, and `retry`.

## Platform adapters

Phase 4 provides isolated adapters for:

- Instagram Graph API/Reels
- LinkedIn text, image, and video posts
- YouTube resumable uploads and processing verification
- Discord channel/webhook delivery with message receipts

Inspect supported targets without network access:

```bash
media-pipeline --workspace ./demo targets
media-pipeline --workspace ./demo target validate \
  --platform instagram \
  --destination ACCOUNT_ID
```

Credentials are referenced, never embedded:

- `INSTAGRAM_ACCESS_TOKEN`
- `LINKEDIN_ACCESS_TOKEN`
- `YOUTUBE_ACCESS_TOKEN`
- `DISCORD_BOT_TOKEN`
- `DISCORD_WEBHOOK_URL`

Use `SecretRef` environment or file references. Tokens are redacted from logs, manifests, receipts, exceptions, and CLI output.

Normal target validation is offline. Live provider checks require both `--live` and:

```bash
export DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1
```

Do not enable live checks unless the target account and provider sandbox are intentionally configured.

## A2A and Hermes workflow

A2A media-producing tasks must return a JSON artifact manifest. An example is in `examples/a2a_manifest_valid.json`.

Validate and ingest a manifest:

```bash
media-pipeline --workspace ./demo a2a validate examples/a2a_manifest_valid.json
media-pipeline --workspace ./demo a2a ingest examples/a2a_manifest_valid.json
```

The manifest records task ID, agent, prompt hash, context ID, returned paths, artifact roles, and tool/model provenance. Ingestion rejects path traversal, unsupported files, missing files, zero-byte files, duplicate paths, and secret-bearing paths.

Hermes-friendly workflow commands return stable JSON:

```bash
media-pipeline --workspace ./demo hermes ingest MANIFEST.json
media-pipeline --workspace ./demo hermes preview JOB_ID
media-pipeline --workspace ./demo hermes status JOB_ID
media-pipeline --workspace ./demo hermes approve JOB_ID \
  --actor hermes \
  --fingerprint FINGERPRINT
media-pipeline --workspace ./demo hermes publish JOB_ID --dry-run
media-pipeline --workspace ./demo hermes publish JOB_ID
```

Hermes commands use the same approval, retry, reconciliation, and idempotency rules as the main CLI.

## Obsidian links and nightly reports

The pipeline writes only AI-owned daily notes by default:

```bash
media-pipeline --workspace ./demo obsidian daily --date 2026-09-23
```

It refuses to overwrite a file without the generated-note marker. Human-authored notes are not mutated.

Generate an operational report:

```bash
media-pipeline --workspace ./demo report nightly
media-pipeline --workspace ./demo report nightly \
  --format markdown \
  --output reports/nightly.md \
  --since 2026-09-23T00:00:00Z
```

Reports include unpublished validated derivatives, failed jobs, unknown remote states, unverified publications, checksum drift, orphan records, and recent receipts. Repeated runs are protected by a lock and atomic output replacement.

## Backup, recovery, and diagnostics

Run diagnostics without network access:

```bash
media-pipeline --workspace ./demo doctor
```

Create and verify a registry backup:

```bash
media-pipeline --workspace ./demo backup create
media-pipeline --workspace ./demo backup verify PATH_TO_BACKUP
```

Restore to a new database:

```bash
media-pipeline --workspace ./demo backup restore PATH_TO_BACKUP \
  --target-db ./demo/data/registry-restored.sqlite3
```

Restoring over a non-empty database requires `--force`. Backups include SHA-256 sidecars, SQLite integrity checks, and schema validation.

## Testing and CI

Run the complete offline suite:

```bash
pytest -q
python -m compileall -q src tests
python -m durable_media.cli --help
```

Build checks require packaging tools:

```bash
python -m pip install setuptools wheel build
python -m build
```

The CI workflow runs tests, compilation, diff hygiene, secret/generated-file scanning, and opt-in provider checks. No default test makes a network request.

## Security boundaries

- Masters are immutable; transformations create derivatives.
- Workspace paths are validated before reads and writes.
- Credentials never belong in Git, manifests, logs, captions, or chat.
- `.env`, private keys, SQLite runtime state, caches, and generated media are ignored by Git.
- Public publishing requires explicit approval of the exact content fingerprint.
- Unknown provider state pauses instead of blindly retrying.
- Discord receipts document that channel retention is not permanent hosting.

See `DURABLE_MEDIA_PUBLISHING_SPEC.md` for the complete product and technical specification and `RELEASE_CHECKLIST.md` for production verification requirements.
