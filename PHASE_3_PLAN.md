# Durable Media Publishing — Phase 3 Approval and Orchestration Plan

> **For Hermes:** Implement this plan task-by-task with an isolated coding agent. Preserve Phase 1/2 behavior and commit only after independent verification.

**Goal:** Harden approval and publishing into a durable, resumable state machine with persisted retries, explicit dry-run behavior, reconciliation for unknown remote state, and deterministic fake-adapter integration tests.

**Architecture:** Local SQLite remains the orchestration source of truth. Every job transition is validated against an explicit transition graph and persisted with actor, reason, attempt, retryability, next-attempt time, and redacted provider metadata. Approval fingerprints are calculated from one canonical payload and are invalidated by any publish-relevant change. Adapters are invoked behind a protocol; fake adapters simulate success and failure classes without network access.

**Tech Stack:** Python 3.11+, SQLite, standard library, pytest, existing adapter protocol.

---

## Acceptance criteria

- Invalid state transitions are rejected and never silently mutate jobs.
- Every transition records timestamp, actor/process, reason, attempt, retryability, and redacted metadata.
- Approval is bound to the exact derivative checksum, caption, platform, destination, visibility, and schedule.
- Existing verified receipts are returned without another upload.
- Retryable failures persist attempt count and next-attempt time with bounded exponential backoff.
- Permanent failures do not retry automatically.
- Unknown remote state pauses the job and requires explicit reconciliation.
- Dry-run validates and previews but performs no adapter write and no receipt creation.
- Fake adapter tests cover success, timeout, rate limit, 5xx, auth failure, validation failure, malformed response, and unknown remote state.
- `status`, `failures`, `reconcile`, `preview`, and `publish --dry-run` expose the persisted state clearly.
- Full Phase 1 and Phase 2 suite remains green.

## Task 1: Define state machine and durable job fields

Modify `src/durable_media/orchestration.py` and `src/durable_media/registry.py`. Add the canonical transition graph, strict transition validation, attempt count, next-attempt time, retry limit, last error class, and redacted remote metadata. Migrate existing SQLite databases without destroying data.

Add tests for all valid transitions, invalid transitions, terminal states, and migration.

## Task 2: Centralize approval payloads and invalidation

Update `src/durable_media/approval.py` and registry accessors. Store the canonical approval payload/fingerprint with the job. Make preview show the complete approval object and ensure changed media, caption, platform, destination, visibility, or schedule invalidates approval before publish.

Add tests for stale approvals and exact fingerprint changes.

## Task 3: Implement retry policy and persisted scheduling

Create `src/durable_media/retry.py`. Classify errors as retryable/permanent/unknown, calculate bounded exponential backoff with deterministic jitter-free delays, and persist attempts/next-attempt timestamps. Ensure retries never bypass approval or create duplicate jobs.

Add tests for timeout, rate limit, 5xx, authentication, validation, malformed response, retry exhaustion, and backoff limits.

## Task 4: Harden publisher adapter contract

Update `src/durable_media/adapters/base.py` and `fake.py`. Define structured upload, processing, publish, verification, and unknown-state results/errors. Add configurable fake outcomes for every failure class and record call counts so duplicate prevention can be asserted.

Add tests that adapter errors are redacted before persistence.

## Task 5: Implement resumable publish orchestration

Rewrite `publish` in `orchestration.py` as explicit phases: validate target, queue, upload, processing, publishing, verifying, receipt persistence. Resume an existing in-progress remote/container ID when available. On retryable errors, persist a retryable state and schedule; on unknown state, pause for reconcile; on permanent errors, terminate permanently.

Add integration tests using the fake adapter for success, retry then success, permanent failure, and unknown state.

## Task 6: Implement idempotency and receipt verification

Update `receipts.py` and registry queries. Use the deterministic idempotency key before every upload, return verified receipts immediately, detect conflicting/in-progress jobs, and persist platform/account/external ID/URL/verification timestamp with redaction. Make receipt reads prove the external response was recorded.

Add repeat-run and concurrent-like duplicate prevention tests.

## Task 7: Implement dry-run and reconciliation

Add `reconcile_job` and dry-run validation/preview services. `publish --dry-run` must not transition into upload/publish and must not create receipts. `reconcile` must accept explicit outcomes such as published, not-published, or retry, and require an operator plus reason for state-changing reconciliation.

Add CLI and service tests for unknown remote state and each reconciliation outcome.

## Task 8: Complete CLI status and failure inspection

Update `src/durable_media/cli.py` with structured outputs for `preview`, `status`, `publish --dry-run`, `reconcile`, and `failures --since`. Include transitions, retry information, approval, receipt, and redacted last error. Add exit codes/messages for invalid approval, retry-not-due, reconcile-required, and permanent failure.

Add end-to-end CLI tests covering approval, dry-run, retry, reconcile, publish, and idempotent replay.

## Task 9: Observability and security regression

Ensure all transition and adapter events use the existing structured observability path. Add redaction fixtures for Authorization headers, tokens, cookies, OAuth codes, and signed URLs. Verify manifests, logs, SQLite metadata, and CLI output contain no secrets.

## Task 10: Full verification and documentation

Update README with the Phase 3 state machine, retry/reconcile behavior, dry-run examples, and fake adapter test usage. Run:

```bash
pytest -q
python -m compileall -q src tests
media-pipeline --help
```

Run an end-to-end temporary-workspace flow and verify the working tree is clean except for intentional source changes before committing.
