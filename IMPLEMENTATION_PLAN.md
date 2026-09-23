# Durable Media Publishing Pipeline — Implementation Plan

> **For Hermes:** Implement this plan task-by-task with an A2A coding agent. Keep the first release local-first and provider-safe; do not add real platform credentials or external writes.

**Goal:** Build a tested Phase 1 vertical slice that ingests immutable media, records provenance and metadata in SQLite, serializes manifests, creates deterministic derivatives/jobs, enforces approval fingerprints, and supports idempotent fake publishing.

**Architecture:** A Python package under `src/durable_media` owns a workspace-relative SQLite registry and content-addressed artifact directories. CLI commands call small domain services; platform adapters implement a common protocol and the initial fake adapter is used for integration tests. External publishing adapters remain isolated and dry-run by default.

**Tech Stack:** Python 3.11+, standard library (`sqlite3`, `hashlib`, `json`, `pathlib`, `mimetypes`, `subprocess`), Typer or argparse CLI, pytest, optional `ffprobe` with a safe fallback for generic files.

---

## Scope and acceptance gates

The implementation must satisfy these gates before calling the MVP complete:

- `media-pipeline init` creates `data/{assets,derivatives,previews,receipts,logs}` and SQLite schema.
- Ingest refuses missing, unreadable, zero-byte, unsupported, or workspace-escaping inputs; records SHA-256, MIME, bytes, source/task, and immutable storage path.
- Registry detects missing files and checksum drift.
- Manifest JSON/YAML-compatible serialization preserves provenance and lineage without secrets.
- Derivative records never mutate masters; profile IDs are versioned.
- Validation is deterministic and reports all failed checks.
- Approval fingerprint changes when media, caption, destination, visibility, or schedule changes; publish is blocked without exact approval.
- Idempotency lookup returns an existing verified receipt instead of creating a duplicate.
- Fake publisher integration tests cover success, timeout/rate-limit retryable errors, auth/validation permanent errors, malformed responses, unknown remote state, and dry-run.
- No secrets are stored in project files, manifests, logs, or test output.

## Task 1: Bootstrap package and workspace config

**Files:** Create `pyproject.toml`, `README.md`, `src/durable_media/__init__.py`, `src/durable_media/config.py`, `tests/test_config.py`.

Implement a `WorkspaceConfig` that resolves the configured workspace, creates only the documented data directories, rejects paths outside the workspace, and exposes the SQLite path. Add a console script named `media-pipeline`.

Verify with: `pytest -q tests/test_config.py` and `media-pipeline --help`.

## Task 2: Create SQLite registry schema

**Files:** Create `src/durable_media/registry.py`, `tests/test_registry.py`.

Add tables for assets, derivatives, publish jobs, approvals, receipts, and state transitions. Use parameterized SQL, UTC timestamps, unique IDs, and indexes for SHA-256/idempotency. Implement CRUD needed by later tasks plus a consistency query for missing files and hash drift.

Verify with temporary workspaces and `pytest -q tests/test_registry.py`.

## Task 3: Implement secure hashing and ingestion

**Files:** Create `src/durable_media/ingest.py`, `src/durable_media/provenance.py`, `tests/test_ingest.py`.

Implement streaming SHA-256, content-based MIME detection, safe copy into `data/assets/<asset_id>/master.<ext>`, source metadata, prompt hash when supplied, and immutable file permissions where supported. Generic files must work without ffprobe; media metadata extraction should use bounded `ffprobe` when available and record the tool/version/command without exposing environment secrets.

Verify stable hashes, distinct asset IDs, rejection cases, source-task persistence, and no mutation of the input with `pytest -q tests/test_ingest.py`.

## Task 4: Add manifest and lineage serialization

**Files:** Create `src/durable_media/manifest.py`, `tests/test_manifest.py`.

Serialize project manifests as deterministic JSON (with a YAML-compatible structure), including schema version, project/source, assets, derivatives, publishing records, and operations. Implement lineage traversal from derivative/job to master. Redact secret-looking keys and values before persistence.

Verify round-trip equality, deterministic output, lineage ordering, and redaction with `pytest -q tests/test_manifest.py`.

## Task 5: Implement versioned derivative profiles and validation

**Files:** Create `src/durable_media/derivatives.py`, `src/durable_media/validation.py`, `tests/test_derivatives.py`, `tests/test_validation.py`.

Define initial profile metadata for `instagram_reel_v1`, `youtube_short_v1`, `linkedin_video_v1`, `discord_preview_v1`, and `audio_voiceover_v1`. Implement a safe derivative copy/render boundary that records parent hash, profile, operations, and output hash; do not silently overwrite outputs. Add deterministic generic/media checks and a structured validation report.

Verify profile version identity, master immutability, failed-check reporting, and derivative registration with `pytest -q tests/test_derivatives.py tests/test_validation.py`.

## Task 6: Add approval fingerprints and publish state machine

**Files:** Create `src/durable_media/approval.py`, `src/durable_media/orchestration.py`, `tests/test_approval.py`, `tests/test_orchestration.py`.

Implement canonical approval payloads and SHA-256 fingerprints. Persist every transition with actor/process, timestamp, reason, and redacted metadata. Enforce `awaiting_approval -> approved -> queued -> uploading -> processing -> publishing -> verifying -> published`; reject stale approvals and invalid transitions.

Verify every fingerprint input changes the fingerprint and unapproved/stale jobs cannot publish.

## Task 7: Implement adapter contract and fake publisher

**Files:** Create `src/durable_media/adapters/base.py`, `src/durable_media/adapters/fake.py`, `src/durable_media/receipts.py`, `tests/test_publishing.py`.

Define the publisher protocol and redacted result/error types. Implement a deterministic fake adapter with configurable success, retryable failure, permanent failure, malformed response, and unknown-state behavior. Persist receipts before/after external-like operations as appropriate, and make verified idempotency hits return without another upload.

Verify duplicate prevention, bounded exponential backoff metadata, error classification, receipt verification, reconcile-required unknown state, and dry-run no-write behavior.

## Task 8: Implement the CLI vertical slice

**Files:** Create `src/durable_media/cli.py`, `tests/test_cli.py`; modify `README.md`.

Implement `init`, `ingest`, `asset show`, `asset lineage`, `derive`, `validate`, `prepare`, `preview`, `approve`, `publish`, `status`, `reconcile`, and `failures`. Commands must emit machine-readable JSON when `--json` is supplied and concise human-readable output otherwise. Default publishing mode is dry-run unless explicitly configured.

Verify an end-to-end temporary-workspace flow from init through fake publish with `pytest -q tests/test_cli.py`.

## Task 9: Add package security and observability checks

**Files:** Create `src/durable_media/security.py`, `src/durable_media/observability.py`, `tests/test_security.py`.

Add secret-path/package exclusion checks for `.env`, private keys, `/root/.hermes/secrets`, token-like files, and signed URLs. Emit structured JSON events containing job/asset IDs, transitions, attempts, durations, retryability, external IDs, and redacted errors.

Verify fixture scans and log redaction with `pytest -q tests/test_security.py`.

## Task 10: Full verification and documentation

**Files:** Modify `README.md`; add `tests/test_end_to_end.py`.

Document local setup, CLI examples, workspace layout, dry-run behavior, fake adapter usage, and the path for adding real platform adapters. Run the complete test suite, compile the package, and exercise the documented end-to-end command sequence in a temporary directory.

Verify with:

```bash
pytest -q
python -m compileall -q src
media-pipeline --help
```

Do not claim platform publication support until each real adapter is implemented against current provider documentation and verified with a sandbox/test account.
