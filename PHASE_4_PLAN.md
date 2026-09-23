# Durable Media Publishing — Phase 4 Platform Adapters Plan

> **For Antigravity:** Implement this plan in the repository root. Use current provider documentation for API shapes, but keep all credentials external and all tests offline with injected transports.

**Goal:** Add isolated Instagram, LinkedIn, YouTube, and Discord publisher adapters that implement the common contract, preserve provider state/IDs, redact secrets, and are fully testable without network access.

**Architecture:** Each adapter owns only provider-specific request/response mapping and uses an injected HTTP or SDK transport. Shared orchestration remains platform-neutral. Configuration contains secret references, not tokens. Tests use deterministic fake transports to cover request construction, polling, retries, malformed responses, and receipt verification. No live credentials or network calls are permitted in the default test suite.

**Tech Stack:** Python 3.11+, existing `Publisher` protocol, standard library HTTP abstractions or a small injectable transport, pytest. Provider SDKs are optional and must not be required for import or tests.

---

## Acceptance criteria

- Every adapter implements target validation, upload creation, upload, processing polling, publish, verify, and cleanup semantics.
- Provider credentials are loaded only from environment/secret references and never appear in logs, manifests, exceptions, or serialized requests.
- Instagram preserves container ID, processing status, media ID, and permalink.
- LinkedIn separates asset registration/upload from post creation and supports image/video plus text-only capability boundaries.
- YouTube models resumable upload session URLs, processing polling, privacy, title, description, tags, thumbnail, video ID, and watch URL.
- Discord sends a file/message through an injected client and records channel/message IDs; no permanence is assumed.
- All adapters map provider statuses/errors into common retryable, permanent, and unknown categories.
- Tests are offline, deterministic, and do not require credentials.
- Existing 14 Phase 3 tests remain green.

## Task 1: Adapter configuration and transport boundaries

Create `src/durable_media/adapters/config.py` and `transport.py`. Define secret-reference configuration objects, redacting serializers, an injectable HTTP transport protocol, response helpers, timeout/error mapping, and safe request logging. Do not import provider SDKs eagerly.

Add tests proving credentials are never included in repr/log/error output.

## Task 2: Instagram adapter

Create `src/durable_media/adapters/instagram.py`. Implement container creation, media upload/reference handling, bounded processing-status polling, publication, verification, and cleanup. Preserve container ID, media ID, status, permalink, and provider response metadata after redaction.

Use current Meta Graph API documentation for endpoint/payload names, but keep endpoint/version/account IDs configurable. Add fake-transport tests for success, processing failure, timeout, rate limit, auth failure, malformed response, and duplicate/reconcile behavior.

## Task 3: LinkedIn adapter

Create `src/durable_media/adapters/linkedin.py`. Implement separate text-only and media flows: asset registration, upload, readiness, post creation, and verification. Preserve asset URN and post ID. Reject unsupported target/content combinations before upload.

Add fake-transport tests for image/video and text-only success, validation errors, upload failures, and verification.

## Task 4: YouTube adapter

Create `src/durable_media/adapters/youtube.py`. Implement resumable upload session creation, chunk upload abstraction, processing-status polling, privacy/status fields, title/description/tags, thumbnail upload, video ID capture, and verification. Preserve the resumable session URL and video ID without logging authorization headers.

Add fake-transport tests for chunking, resume, processing delay, rejected processing, quota/auth failures, and successful watch URL verification.

## Task 5: Discord adapter

Create `src/durable_media/adapters/discord.py`. Implement injected client-based file/message delivery, channel validation, message ID capture, attachment URL capture, and verification. Make retention/permanence limits explicit in receipt metadata.

Add fake-client tests for success, missing channel, upload failure, message failure, and verification.

## Task 6: Common result/error mapping and registry integration

Update `adapters/base.py`, `orchestration.py`, `receipts.py`, and registry migrations as needed. Normalize adapter results into the Phase 3 state machine and persist provider/account/external IDs, processing metadata, verification timestamp, and redacted response data. Ensure idempotency and unknown-state reconciliation work consistently across adapters.

Add cross-adapter contract tests that run the same lifecycle assertions.

## Task 7: CLI/configuration documentation

Update `cli.py`, `README.md`, and `pyproject.toml` only where needed. Add adapter selection/configuration without exposing credentials, a command to inspect configured targets, and dry-run validation of adapter configuration. Document secret references, sandbox requirements, provider API version configuration, and the fact that tests never make network calls.

## Task 8: Full verification

Add `tests/test_adapters.py` and provider-specific fixtures. Run:

```bash
pytest -q
python -m compileall -q src tests
media-pipeline --help
```

Verify no network calls occur in tests, no secret-shaped values enter persisted metadata, and the working tree contains no credentials or generated media. Do not claim live-account verification without explicit sandbox credentials and a successful read-back receipt.
