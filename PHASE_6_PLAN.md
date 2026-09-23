# Durable Media Publishing — Phase 6 Production Hardening Plan

> **For Antigravity:** Implement this plan in the repository root. Keep live provider verification opt-in; default tests and commands must not require credentials or network access.

**Goal:** Make the pipeline operable as a production-quality local service by adding packaging/CI quality gates, database backup and recovery, configuration diagnostics, provider readiness checks, safe scheduling hooks, and end-to-end release verification.

**Architecture:** Add a small operations layer around the existing registry and CLI. Backups are consistent SQLite snapshots plus checksums, restored only into explicit destinations. Readiness checks report configuration/tool/provider capability without exposing secrets. CI runs deterministic offline tests and optional integration tests only when explicitly enabled. Scheduled reporting is exposed as a safe command and documented external scheduler entry point rather than silently installing a daemon.

**Tech Stack:** Python 3.11+, SQLite backup API, existing stdlib transport/adapters, pytest, optional FFmpeg/ffprobe. No new mandatory network dependencies.

---

## Acceptance criteria

- Package builds cleanly from a fresh checkout and the console entrypoint works after installation.
- CI runs tests, compile checks, diff hygiene, and secret-path checks without credentials.
- Registry backup creates a consistent SQLite snapshot and SHA-256 checksum; restore verifies checksum and refuses unsafe overwrite by default.
- `doctor` reports Python, FFmpeg/ffprobe, workspace, registry migration, adapter configuration, and secret readiness without printing secret values.
- Provider readiness checks are dry-run by default and live checks require explicit opt-in.
- Scheduled nightly reports are safe to invoke repeatedly and write only to configured AI-owned paths.
- Operational logs remain redacted and bounded.
- Existing Phase 1–5 tests remain green.

## Task 1: Packaging and reproducible build checks

Update `pyproject.toml`, README, and add build metadata/tests. Ensure editable and wheel installs expose `media-pipeline`, package all source modules, exclude generated state, and declare supported Python versions. Add a smoke test using a temporary virtual environment or build artifact.

## Task 2: CI quality gates

Create `.github/workflows/ci.yml` or an equivalent documented CI workflow. Run the offline test matrix, compileall, `git diff --check`, package build, and secret/generated-file scan. Keep provider integration tests behind an explicit opt-in environment flag.

## Task 3: SQLite backup and restore

Create `src/durable_media/backup.py`. Implement consistent backup using SQLite backup/online snapshot semantics, checksum sidecar generation, verified restore, schema/version validation, and safe refusal to overwrite an existing non-empty destination without `--force`. Add `backup create`, `backup verify`, and `backup restore` CLI commands.

Add tests for round-trip data preservation, checksum drift, corrupt backups, incompatible databases, and force protection.

## Task 4: Operational diagnostics

Create `src/durable_media/doctor.py`. Report workspace safety, directory permissions, registry readability/migrations, Python version, FFmpeg/ffprobe availability/version, profile availability, adapter target readiness, and secret references. Use stable JSON output and redact all values.

Add `doctor` CLI tests with missing tools, missing secrets, invalid workspace, and healthy local configurations.

## Task 5: Provider readiness and opt-in live checks

Extend adapter registry/configuration with dry-run readiness validation and an explicit `--live` check path. Live checks must require an environment gate, never run during normal tests, use bounded timeouts, and report only status/error class. Add offline tests proving no transport calls occur without `--live`.

## Task 6: Scheduler-safe nightly reporting

Create `src/durable_media/scheduler.py` or equivalent command helpers. Add idempotent `report nightly --output PATH --format json|markdown`, lock protection against overlapping runs, atomic output replacement, and retention-safe output handling. Document a cron/systemd example without installing services automatically.

Add tests for repeated runs, concurrent lock behavior, atomic failure cleanup, and human-note overwrite protection.

## Task 7: Operational log rotation and bounded storage

Add configurable JSONL log rotation/size limits and a `logs inspect` or diagnostics command. Ensure redaction happens before rotation and that signed URLs, authorization headers, cookies, and tokens never persist.

Add regression tests for redaction and rotation boundaries.

## Task 8: Release verification and documentation

Add `tests/test_phase6.py`, update README with installation, CI, backup/restore, doctor, scheduler, live-check gating, and recovery procedures. Add a `RELEASE_CHECKLIST.md` covering sandbox verification requirements for Instagram, LinkedIn, YouTube, and Discord.

Run:

```bash
pytest -q
python -m compileall -q src tests
python -m build
media-pipeline --help
media-pipeline doctor --help
media-pipeline backup --help
media-pipeline report nightly --help
```

Verify `git diff --check`, no credentials/generated binaries, and the original specification remains unchanged.
