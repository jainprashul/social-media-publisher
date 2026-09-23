# Durable Media and Publishing Pipeline — Product & Technical Specification

**Status:** Draft for implementation  
**Owner:** Prashul / Hermes Agent  
**Target workspace:** `/home/X/Playground/durable-media-publishing/`  
**Primary use cases:** A2A-generated images, videos, reels, voiceovers, captions, and cross-platform publishing

## 1. Summary

The Durable Media and Publishing Pipeline turns media generation into a reproducible, auditable, and publishable workflow. Every asset receives a stable identity, immutable artifact record, provenance metadata, validation results, and platform-specific derivatives before it can be published.

The system separates four concerns:

1. **Create** — generate or ingest media from Hermes, Antigravity/A2A, local tools, or external sources.
2. **Package** — normalize files, compute hashes, record provenance, and create a project manifest.
3. **Prepare** — render platform-specific derivatives, captions, thumbnails, metadata, and previews.
4. **Publish** — upload through isolated platform adapters with approval gates, retries, idempotency, and verifiable receipts.

A successful render is not considered published until the target platform returns a durable external identifier or URL and the pipeline records it.

## 2. Problem

Current media work is distributed across generated files, FFmpeg commands, A2A output folders, and one-off publishing scripts. This causes:

- Lost or overwritten generated assets.
- No reliable link between source prompt, source media, derivative, and published post.
- Repeated uploads after network failures.
- Platform-specific scripts with inconsistent validation and status handling.
- Credentials and publishing logic mixed with project code.
- No single view of what was created, approved, published, or failed.
- Difficulty reproducing a successful reel or correcting one derivative without rebuilding everything.

## 3. Goals

1. Make every media artifact durable, addressable, and traceable.
2. Preserve source prompts, model/tool versions, input assets, transformations, and approvals.
3. Support image, audio, video, caption, thumbnail, and metadata artifacts.
4. Produce platform-specific derivatives without mutating the master artifact.
5. Provide a common publishing interface for Instagram, LinkedIn, YouTube, Discord, and future platforms.
6. Make retries safe through idempotency keys and persisted job state.
7. Require explicit human approval before public publishing by default.
8. Verify external publishing by reading back the returned post/media identifier where supported.
9. Store credentials outside project directories with restrictive permissions.
10. Support local-first operation when a remote platform or provider is unavailable.

## 4. Non-Goals

- Building a general-purpose DAM for arbitrary teams in the first release.
- Circumventing platform API limits, moderation, or upload requirements.
- Automatically publishing generated media without an approval policy.
- Storing access tokens, API secrets, or private source media in Git.
- Replacing FFmpeg, Kokoro, Whisper, Antigravity, or platform APIs.
- Automatically deleting source assets after publication.

## 5. Design Principles

### 5.1 Masters are immutable

The original generated or ingested file is never edited in place. Every transformation creates a new derivative linked to its parent by hash and operation metadata.

### 5.2 Metadata is part of the artifact

A file without provenance, checksum, MIME type, dimensions/duration, and creation context is incomplete and must not enter the publish queue.

### 5.3 Local state is the source of truth for orchestration

The pipeline persists jobs and receipts locally before making external requests. External platform IDs are secondary references, not replacements for local history.

### 5.4 Publish is a state machine

Publishing is not one command. It may include validation, upload, remote processing, polling, publication, verification, and failure recovery.

### 5.5 Approval is explicit

A user must approve the exact asset/derivative, destination, caption, visibility, and scheduled time. Approval applies to a content fingerprint, not merely to a project name.

## 6. High-Level Architecture

```text
┌─────────────────────┐
│ Hermes / A2A / CLI  │
│ generation sources  │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Ingest + asset ID   │  copy, validate, hash
└──────────┬──────────┘
           ▼
┌─────────────────────┐      ┌───────────────────┐
│ Asset registry      │─────▶│ Manifest store    │
│ SQLite + JSON       │      │ project manifests │
└──────────┬──────────┘      └───────────────────┘
           ▼
┌─────────────────────┐
│ Derivative workers  │  FFmpeg, captions, TTS,
│                     │  thumbnails, resizing
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Validation gate     │  codec, aspect, size,
│                     │  duration, captions
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Human approval      │  exact fingerprint + target
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Publish orchestrator│  adapter, retry, polling
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Platform adapters   │  Instagram, LinkedIn,
│                     │  YouTube, Discord
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Receipt + verifier  │  external ID, URL, status
└─────────────────────┘
```

## 7. Project Layout

```text
/home/X/Playground/durable-media-publishing/
├── README.md
├── pyproject.toml
├── src/durable_media/
│   ├── cli.py
│   ├── config.py
│   ├── registry.py
│   ├── ingest.py
│   ├── manifest.py
│   ├── provenance.py
│   ├── validation.py
│   ├── derivatives.py
│   ├── approval.py
│   ├── orchestration.py
│   ├── receipts.py
│   └── adapters/
│       ├── base.py
│       ├── instagram.py
│       ├── linkedin.py
│       ├── youtube.py
│       └── discord.py
├── tests/
├── manifests/
├── projects/
└── data/
    ├── registry.sqlite3
    ├── assets/
    ├── derivatives/
    ├── previews/
    ├── receipts/
    └── logs/
```

The project workspace is not the Obsidian vault. Daily notes may link to manifests and receipts, but binary assets should remain under the media workspace or an explicitly configured object store.

## 8. Core Data Model

### 8.1 Asset

```json
{
  "asset_id": "asset_01J...",
  "kind": "video",
  "role": "master",
  "path": "data/assets/asset_01J.../master.mp4",
  "sha256": "...",
  "mime_type": "video/mp4",
  "bytes": 24819302,
  "created_at": "2026-09-23T18:00:00+05:30",
  "source": {
    "type": "a2a",
    "agent": "antigravity",
    "task_id": "task-...",
    "prompt_hash": "..."
  },
  "media": {
    "width": 1080,
    "height": 1920,
    "duration_ms": 28400,
    "fps": 30,
    "audio_channels": 2
  },
  "status": "verified"
}
```

### 8.2 Derivative

```json
{
  "derivative_id": "deriv_01J...",
  "asset_id": "asset_01J...",
  "parent_sha256": "...",
  "path": "data/derivatives/instagram_reel.mp4",
  "sha256": "...",
  "profile": "instagram_reel_v1",
  "operations": [
    {"name": "resize", "width": 1080, "height": 1920},
    {"name": "encode", "video_codec": "h264", "audio_codec": "aac"},
    {"name": "burn_captions", "caption_asset_id": "asset_..."}
  ],
  "validation": {
    "status": "passed",
    "checks": ["codec", "dimensions", "duration", "audio", "file_readability"]
  }
}
```

### 8.3 Publish job

```json
{
  "publish_job_id": "pub_01J...",
  "derivative_id": "deriv_01J...",
  "platform": "instagram",
  "destination": "configured-account-id",
  "caption_sha256": "...",
  "visibility": "public",
  "scheduled_at": null,
  "idempotency_key": "sha256(derivative + platform + destination + caption + visibility)",
  "approval": {
    "status": "approved",
    "approved_by": "prashul",
    "approved_at": "2026-09-23T19:00:00+05:30",
    "fingerprint": "..."
  },
  "state": "published",
  "external": {
    "media_id": "platform-specific-id",
    "url": "https://..."
  }
}
```

## 9. Asset Lifecycle

```text
discovered
   ▼
ingested → hashed → metadata_extracted → verified
   ▼
mastered
   ▼
derivative_requested → rendered → validated
   ▼
awaiting_approval → approved
   ▼
queued → uploading → processing → publishing → verifying → published
                                      └──────────────▶ failed/retryable
```

Terminal states:

- `published`
- `rejected`
- `cancelled`
- `failed_permanent`

Every transition must record timestamp, actor/process, reason, and relevant external response metadata with secrets removed.

## 10. Ingestion Requirements

The ingest command must accept:

```bash
media-pipeline ingest /path/to/file \
  --project tech-reel-2026-09 \
  --role master \
  --source a2a \
  --source-task task-123
```

Ingestion must:

1. Verify the source path exists and is readable.
2. Detect MIME type using content inspection, not only filename.
3. Compute SHA-256 before copying.
4. Copy into content-addressed or asset-ID storage.
5. Extract media metadata with `ffprobe` or an equivalent safe reader.
6. Generate a thumbnail/contact sheet for visual media.
7. Write a provenance record.
8. Refuse unsupported, corrupt, or zero-byte files.
9. Preserve the original extension as metadata but normalize storage names.

## 11. Derivative Profiles

Initial profiles:

| Profile | Target | Required output |
|---|---|---|
| `instagram_reel_v1` | Instagram Reels | MP4, H.264/AAC, vertical 9:16, caption, cover frame |
| `youtube_short_v1` | YouTube Shorts | MP4, vertical 9:16, title, description, tags, thumbnail |
| `linkedin_video_v1` | LinkedIn | MP4, validated duration/size, post text, thumbnail |
| `discord_preview_v1` | Discord | size-constrained MP4/GIF preview, message text |
| `audio_voiceover_v1` | Local/archive | WAV/MP3, sample rate, voice/model metadata |

Profiles must be versioned. A change to codec flags, caption style, dimensions, or quality settings creates a new profile version instead of silently changing old outputs.

## 12. Validation Gate

Validation is deterministic and must run before approval.

### All media

- File exists and is non-empty.
- SHA-256 is recorded.
- MIME type matches detected content.
- File can be decoded/read.
- No path escapes the configured workspace.
- No embedded credentials or accidental secret files in the package.

### Video

- Width, height, aspect ratio, duration, frame rate, codec, bitrate, and audio stream are present.
- No corrupt frames detected by a bounded decode check.
- Caption track or burned captions match project policy.
- Thumbnail is readable and representative.

### Audio

- Duration is non-zero.
- Sample rate, channels, codec, and loudness metadata are recorded.
- Optional loudness range is within the selected profile.

### Text and captions

- Caption file is valid UTF-8.
- Cue timestamps are monotonic and inside media duration.
- No empty or overlapping cues unless explicitly permitted.
- Platform caption length and character constraints are checked before publishing.

## 13. Approval Workflow

Default policy: **no public publish without explicit approval**.

The approval preview must show:

- Asset/derivative preview.
- Destination platform and account.
- Caption and hashtags.
- Visibility.
- Scheduled time.
- File size, dimensions, duration, and checksum.
- Source/provenance summary.

Example:

```bash
media-pipeline approve pub_01J... \
  --actor prashul \
  --fingerprint <displayed-fingerprint>
```

Approval becomes invalid if any of the following change:

- Media checksum.
- Caption text.
- Target account or platform.
- Visibility.
- Scheduled time.

## 14. Publishing Adapter Contract

Every adapter implements the same interface:

```python
class Publisher(Protocol):
    def validate_target(self, target: Target) -> ValidationResult: ...
    def create_upload(self, job: PublishJob) -> RemoteUpload: ...
    def upload(self, remote: RemoteUpload, file_path: str) -> UploadResult: ...
    def wait_until_ready(self, remote: RemoteUpload) -> ProcessingResult: ...
    def publish(self, remote: RemoteUpload, job: PublishJob) -> PublishResult: ...
    def verify(self, result: PublishResult) -> VerificationResult: ...
    def rollback_or_cleanup(self, remote: RemoteUpload) -> None: ...
```

Adapters must not expose access tokens in logs or error messages. They must map platform-specific states into the common state machine.

### Platform-specific notes

- **Instagram:** support container creation, processing-status polling, publication, and receipt verification. The adapter must preserve the container ID and published media ID.
- **LinkedIn:** support asset registration/upload, post creation, and post ID capture. Image/video upload and text-only post flows should remain separate capabilities.
- **YouTube:** support resumable upload, processing polling, privacy selection, title/description/tags, and video ID capture.
- **Discord:** treat delivery as a message/file send with a recorded channel/message ID; avoid assuming public permanence beyond the channel's retention policy.

Platform API behavior and requirements must be read from current provider documentation during implementation; this spec defines the internal contract, not frozen external API details.

## 15. Retry and Idempotency

Each publish job has a deterministic idempotency key:

```text
sha256(derivative_sha256 + platform + destination + caption_sha256 + visibility + scheduled_at)
```

Before starting an upload:

1. Search local receipts by idempotency key.
2. If a verified published receipt exists, return it without uploading again.
3. If an in-progress remote container exists, resume polling rather than creating another.
4. If the remote state is unknown, require an explicit `--reconcile` or human decision before retrying.

Retryable failures include network timeout, rate limit, temporary 5xx, and provider processing delay. Authentication errors, validation errors, policy rejection, and malformed media are non-retryable until corrected.

Use exponential backoff with a bounded retry count and persisted next-attempt time.

## 16. Credentials and Security

- Store credentials only under `/root/.hermes/secrets/` or the configured secret manager.
- Set credential files to mode `0600`.
- Never place tokens in manifests, Git, logs, Discord messages, or generated captions.
- Redact `Authorization`, access tokens, client secrets, cookies, OAuth codes, and signed upload URLs from persisted responses.
- Use environment variables or secret-file references in configuration; never hardcode secrets in source.
- Validate that the output package excludes `/root/.hermes/secrets/`, `.env`, private keys, and token-like files.
- Restrict publishing commands to explicitly configured accounts and destinations.
- Provide a `--dry-run` mode that performs validation and previews but makes no external write.

## 17. CLI Surface

```bash
# Initialize registry and workspace
media-pipeline init

# Ingest and register an asset
media-pipeline ingest FILE --project PROJECT --role master

# Inspect assets and provenance
media-pipeline asset show ASSET_ID
media-pipeline asset lineage ASSET_ID

# Render a platform derivative
media-pipeline derive ASSET_ID --profile instagram_reel_v1

# Validate a derivative
media-pipeline validate DERIVATIVE_ID

# Create a publish job
media-pipeline prepare DERIVATIVE_ID --platform instagram --destination ACCOUNT

# Preview and approve
media-pipeline preview PUBLISH_JOB_ID
media-pipeline approve PUBLISH_JOB_ID --actor prashul --fingerprint FINGERPRINT

# Publish and verify
media-pipeline publish PUBLISH_JOB_ID
media-pipeline status PUBLISH_JOB_ID

# Reconcile unknown remote state
media-pipeline reconcile PUBLISH_JOB_ID

# Inspect failures
media-pipeline failures --since 7d
```

## 18. Manifest Example

```yaml
schema_version: 1
project_id: hermes-a2a-reel-2026-09
created_at: 2026-09-23T18:00:00+05:30
source:
  kind: a2a
  agent: antigravity
  task_id: task-849da070f73244e0
  prompt_hash: sha256:...
assets:
  - id: asset_01J...
    role: master
    kind: video
    sha256: sha256:...
    path: data/assets/asset_01J.../master.mp4
    mime: video/mp4
    metadata:
      width: 1080
      height: 1920
      duration_ms: 28400
derivatives:
  - id: deriv_01J...
    profile: instagram_reel_v1
    parent_asset: asset_01J...
    sha256: sha256:...
    validation_status: passed
publishing:
  - job_id: pub_01J...
    platform: instagram
    approval_status: pending
    state: awaiting_approval
```

## 19. Observability

Every job produces structured JSON logs with:

- `job_id`
- `asset_id` or `derivative_id`
- `platform`
- `state_transition`
- `attempt`
- `duration_ms`
- `retryable`
- `external_id` when available
- `error_class` and redacted error message

Provide local queries for:

- Assets created in a date range.
- Unpublished validated derivatives.
- Failed jobs by platform.
- Published posts with missing verification.
- Orphaned files without registry records.
- Registry records whose files are missing or whose hashes changed.

## 20. Acceptance Criteria

### Durability

- [ ] Every ingested/generated asset has a stable ID and SHA-256 checksum.
- [ ] Master files are immutable and transformations create new derivatives.
- [ ] Provenance links source prompt/task, tool/model, parent assets, and operations.
- [ ] Registry detects missing files and checksum drift.

### Reproducibility

- [ ] A derivative can be rebuilt from its manifest and profile version.
- [ ] FFmpeg/tool versions and relevant command parameters are recorded.
- [ ] Captions, audio, thumbnails, and metadata are separate addressable artifacts.

### Publishing

- [ ] No public publish occurs without approval by default.
- [ ] Approval is bound to the exact media/caption/destination fingerprint.
- [ ] Retries do not create duplicate posts when a prior receipt exists.
- [ ] Provider processing states are polled with bounded timeouts.
- [ ] Published output records platform, account, external ID, URL, and verification timestamp.
- [ ] Unknown remote state pauses for reconciliation instead of blindly retrying.

### Security

- [ ] No credentials appear in logs, manifests, Git, or chat delivery.
- [ ] Secret files have restrictive permissions.
- [ ] Dry-run mode performs no external writes.
- [ ] Workspace packaging excludes secret-bearing paths.

### Testing

- [ ] Unit tests cover hashing, metadata extraction, profile validation, manifest serialization, and idempotency keys.
- [ ] Integration tests use a temporary registry and fake publisher adapters.
- [ ] Failure tests cover timeout, rate limit, auth failure, malformed response, and unknown remote state.
- [ ] Re-running the same job is verified to be idempotent.
- [ ] A fixture verifies that human-readable provenance is preserved without leaking secrets.

## 21. Implementation Phases

### Phase 1 — Registry and durable artifacts

1. Create the project workspace and Python package.
2. Add SQLite schema for assets, derivatives, jobs, transitions, approvals, and receipts.
3. Implement ingest, hashing, metadata extraction, and immutable storage.
4. Add manifest serialization and lineage queries.
5. Add orphan/drift verification.

### Phase 2 — Derivative engine

1. Implement versioned profile definitions.
2. Add FFmpeg wrapper with captured command and tool version.
3. Add thumbnail/contact-sheet generation.
4. Add caption and voiceover artifact registration.
5. Implement deterministic validation gates.

### Phase 3 — Approval and orchestration

1. Implement approval fingerprints and preview output.
2. Add publish-job state machine.
3. Add idempotency and retry persistence.
4. Add fake publisher adapter tests.
5. Add dry-run and reconcile commands.

### Phase 4 — Platform adapters

1. Adapt the existing Instagram Reels flow into the common interface.
2. Adapt LinkedIn image and text flows without copying credentials into project code.
3. Add YouTube resumable upload and processing verification.
4. Add Discord delivery receipts.
5. Verify each adapter against current provider documentation and sandbox/test accounts where available.

### Phase 5 — Hermes and A2A integration

1. Add an A2A artifact-ingest helper that accepts task IDs and returned file paths.
2. Require A2A tasks producing media to return an artifact manifest.
3. Add Hermes commands for preview, approval, publish, and status.
4. Add daily-note links to project manifests and publish receipts.
5. Add a nightly report for unpublished validated assets and failed jobs.

## 22. Definition of Done

The pipeline is complete when an A2A-generated image or video can be ingested, hashed, traced to its prompt/task, rendered into a validated platform derivative, previewed, explicitly approved, uploaded through a provider adapter, safely retried, and verified by an external receipt—without losing the master artifact or exposing credentials.
