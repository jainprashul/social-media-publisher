# Durable Media Publishing — Phase 2 Derivative Engine Plan

> **For Hermes:** Implement this plan task-by-task with an isolated coding agent. Preserve Phase 1 behavior and run the complete suite before handoff.

**Goal:** Replace the Phase 1 copy-only derivative path with a reproducible, media-aware derivative engine that records FFmpeg provenance, generates thumbnails/contact sheets, registers caption and voiceover artifacts, and enforces deterministic profile validation.

**Architecture:** Keep masters immutable and make every output an addressable registry artifact. A safe FFmpeg runner owns command construction, version capture, bounded execution, and redacted provenance. Profile definitions declare constraints and operations; the derivative service materializes outputs and records the exact profile/version/command. Validation consumes recorded metadata and never mutates files.

**Tech Stack:** Python 3.11+, SQLite, standard library, system `ffmpeg`/`ffprobe` when available, pytest. No real platform credentials or network writes.

---

## Acceptance criteria

- FFmpeg and ffprobe commands are constructed as argument arrays, never shell strings.
- Tool versions, commands, return codes, and bounded stderr are recorded without secrets.
- Missing tools and malformed media produce actionable deterministic failures.
- Video profiles enforce dimensions/aspect ratio, codecs, duration, frame rate, audio presence, and size constraints where configured.
- Audio profiles enforce duration, sample rate, channels, codec, and loudness metadata where available.
- Thumbnail generation produces a readable image artifact; contact sheets are deterministic for multi-frame inputs.
- Caption artifacts use UTF-8 validation, monotonic in-range cues, and reject empty/overlapping cues.
- Voiceover artifacts record sample rate/channels/model/tool provenance and remain separate from masters.
- All generated artifacts are hash-addressed and registered; masters are never modified.
- Rebuilding a derivative with the same input/profile/operations is deterministic or returns the existing matching derivative.
- Existing Phase 1 tests continue to pass.

## Task 1: Extend registry for artifact metadata

Modify `src/durable_media/registry.py` and add migration tests. Add an `artifacts` table or equivalent records for thumbnails, captions, contact sheets, and voiceovers with kind, parent ID, path, hash, MIME, bytes, metadata, and provenance. Preserve compatibility with existing databases.

Verify migration on a fresh and already-initialized registry.

## Task 2: Add media tool runner

Create `src/durable_media/media_tools.py` and tests. Implement `run_ffprobe`, `run_ffmpeg`, tool-version discovery, timeout handling, bounded output capture, and secret redaction. Use `subprocess.run([...], check=False, timeout=...)`; reject workspace escapes and shell metacharacter injection by construction.

Verify with fake executable scripts and a real `ffprobe` fixture when installed.

## Task 3: Add structured media metadata extraction

Extend ingestion or create `src/durable_media/media_metadata.py`. Parse ffprobe JSON for streams, format, dimensions, duration, fps, codecs, bitrate, audio channels, sample rate, and loudness tags. Store metadata on assets and derivatives without failing generic non-media ingestion when ffprobe cannot decode the file.

Verify video, audio, malformed, missing-tool, and generic-file cases.

## Task 4: Implement versioned profile definitions

Replace the copy-only profile map with typed profile definitions in `src/durable_media/profiles.py`. Define exact constraints and operations for `instagram_reel_v1`, `youtube_short_v1`, `linkedin_video_v1`, `discord_preview_v1`, and `audio_voiceover_v1`. Include profile serialization and a profile fingerprint so changes cannot silently reuse old outputs.

Verify profile IDs, versions, constraints, and deterministic fingerprints.

## Task 5: Implement FFmpeg derivative rendering

Update `src/durable_media/derivatives.py`. Build profile-specific FFmpeg commands for resize/crop, H.264/AAC encoding, audio extraction/encoding, and quality settings. Record command, tool version, profile fingerprint, parent hash, and operation metadata. Use temporary output files followed by atomic rename; never overwrite an existing derivative.

Verify output hash, master immutability, command provenance, duplicate rebuild behavior, and failure cleanup.

## Task 6: Add thumbnails and contact sheets

Create `src/durable_media/previews.py`. Generate a representative thumbnail and deterministic contact sheet via FFmpeg, register each output as an artifact, and expose paths through CLI/manifest data. Keep preview generation bounded and safe for long videos.

Verify readable output, stable naming, registration, and cleanup on failure.

## Task 7: Add captions and voiceover artifact registration

Create `src/durable_media/captions.py` and `src/durable_media/voiceover.py`. Support UTF-8 SRT/VTT parsing and validation; register caption assets separately. Add a voiceover registration path for existing WAV/MP3 files with metadata and model/tool provenance; do not synthesize audio in this phase.

Verify timestamp ordering, overlap/range checks, encoding errors, audio metadata, and provenance preservation.

## Task 8: Implement deterministic validation gates

Expand `src/durable_media/validation.py` into structured checks for generic files, video, audio, captions, thumbnails, dimensions/aspect ratio, codec, duration, FPS, file size, and readability. Persist validation status and checks on the derivative/artifact records. Return all failures in stable order.

Verify valid/invalid fixtures, drift detection, missing metadata, and deterministic JSON output.

## Task 9: Expand CLI and manifest integration

Update `src/durable_media/cli.py`, `manifest.py`, and README. Add `thumbnail`, `contact-sheet`, `caption register`, `voiceover register`, `profile show`, and richer `validate` output. Ensure `derive` accepts profile options and outputs artifact IDs/paths. Keep all commands JSON-capable and workspace constrained.

Verify the documented end-to-end flow with a generated short video fixture:

```bash
media-pipeline init
media-pipeline ingest input.mp4 --project demo --source a2a --source-task task-1
media-pipeline derive ASSET_ID --profile instagram_reel_v1
media-pipeline thumbnail DERIVATIVE_ID
media-pipeline validate DERIVATIVE_ID
```

## Task 10: Full regression and packaging verification

Add integration fixtures/tests under `tests/fixtures/` and `tests/test_phase2.py`. Run:

```bash
pytest -q
python -m compileall -q src tests
media-pipeline --help
```

Confirm no credentials, `.env`, private keys, or generated binaries enter source control or manifests. Document that real platform adapters remain Phase 4.
