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
