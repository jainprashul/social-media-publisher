# Durable Media Publishing — Phase 5 Hermes and A2A Integration Plan

> **For Antigravity:** Implement this plan in the repository root. Preserve the existing Phase 1–4 contracts, keep secrets external, and make all tests offline and deterministic.

**Goal:** Connect A2A-generated media to the durable pipeline through a validated artifact manifest, expose Hermes-friendly preview/approval/publish/status commands, link receipts into the Obsidian daily workflow, and provide a nightly operational report.

**Architecture:** The integration layer accepts explicit A2A task metadata and returned file paths, validates a schema-first artifact manifest, then delegates to existing ingestion/derivative/orchestration services. A2A helpers must not trust arbitrary paths or provider responses; all paths are workspace-confined and all persisted metadata is redacted. Daily-note and report writers create AI-owned notes only and never mutate human-authored notes.

**Tech Stack:** Python 3.11+, existing SQLite registry/CLI, JSON and YAML-compatible manifests, standard library filesystem/time utilities, pytest. No live A2A peer or platform credentials are required for tests.

---

## Acceptance criteria

- A2A task results require a valid artifact manifest containing task ID, source agent, prompt hash or explicit omission, and one or more returned file paths.
- Artifact ingestion rejects missing files, zero-byte files, workspace escapes, unsupported files, duplicate ambiguous paths, and secret-bearing paths.
- Every A2A artifact links task ID, agent, prompt hash, original path, asset ID, checksum, and ingestion timestamp.
- Hermes CLI commands expose preview, approval, publish, status, and A2A ingest without bypassing Phase 3 approval gates.
- Daily-note links and nightly reports are written only to configured AI-owned output paths, with no mutation of human-authored notes.
- Nightly reports identify unpublished validated derivatives, failed/retryable/unknown jobs, missing receipts, and checksum drift.
- All output is deterministic JSON/Markdown and contains no credentials, tokens, cookies, or signed URLs.
- Existing Phase 1–4 tests remain green.

## Task 1: Define A2A artifact manifest schema

Create `src/durable_media/a2a_manifest.py`. Define strict dataclasses or validation functions for task ID, agent, prompt hash, project, returned artifacts, artifact kind/role/path, optional caption/thumbnail paths, and tool/model provenance. Implement deterministic JSON serialization and redaction.

Add tests for valid manifests, missing required fields, malformed hashes, duplicate artifacts, and secret-path rejection.

## Task 2: Implement A2A artifact ingestion service

Create `src/durable_media/a2a_ingest.py`. Accept a manifest path or in-memory manifest plus workspace root, validate every returned file, and call the existing ingest service with source type `a2a`. Return an artifact-to-asset mapping and persisted manifest reference. Support multiple artifacts while preserving parent/role relationships.

Add tests for task/agent/prompt provenance, checksum persistence, path traversal, missing files, zero-byte files, and duplicate idempotent ingestion.

## Task 3: Add A2A artifact-manifest CLI command

Update `src/durable_media/cli.py` with `a2a ingest MANIFEST --project PROJECT` and `a2a validate MANIFEST`. Emit machine-readable mappings and concise errors. Ensure the command never writes outside the configured workspace and never publishes automatically.

Add CLI tests covering valid ingestion, invalid manifests, and repeat ingestion.

## Task 4: Add Hermes-friendly workflow commands

Add command aliases or a dedicated `hermes` CLI group for `preview`, `approve`, `publish`, and `status`. These must call the existing Phase 3 services, preserve fingerprint approval, support `--dry-run`, and return stable JSON suitable for Hermes tool wrappers.

Add tests proving the Hermes path cannot bypass approval, can reuse receipts idempotently, and exposes reconciliation-required state.

## Task 5: Add manifest and receipt links for Obsidian daily notes

Create `src/durable_media/obsidian_links.py`. Generate Markdown link blocks for project manifests and publish receipts. Default output must be an AI-owned directory under the configured workspace; an explicit external output path must be required for vault integration. Never read or overwrite human-authored notes.

Add tests for safe relative links, path escaping, deterministic output, and no secret leakage.

## Task 6: Implement operational nightly report

Create `src/durable_media/reports.py`. Generate deterministic JSON and Markdown reports for unpublished validated derivatives, failed jobs grouped by platform/error class, unknown remote states, published jobs missing verification, orphan/drift records, and recent receipts. Support `--since` and output path options.

Add `report nightly` CLI support and tests against a temporary registry with representative records.

## Task 7: Add A2A/Hermes observability metadata

Update structured events and manifests to include A2A task ID, agent, context ID when provided, artifact count, and workflow command. Redact secrets before persistence. Add regression tests ensuring task metadata survives while tokens and signed URLs do not.

## Task 8: Documentation and full verification

Update README with the A2A manifest format, CLI examples, Hermes workflow, Obsidian output boundary, nightly report usage, and limitations. Add example files under `examples/` without real media or credentials.

Run:

```bash
pytest -q
python -m compileall -q src tests
media-pipeline --help
media-pipeline a2a --help
media-pipeline report --help
```

Verify `git diff --check`, no generated binaries/secrets, and no changes to the original specification.
