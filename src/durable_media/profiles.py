"""Platform derivative encoding profiles and constraint specifications.

Each profile defines target container, resolution, codecs, framerate, duration,
and byte limits for destination platforms. Profiles are immutable; their SHA-256
fingerprint contributes directly to derived asset IDs to guarantee reproducibility.
"""
import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Profile:
    """Platform derivative encoding specification and validation constraints."""

    id: str
    version: int
    kind: str
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    max_duration: float | None = None
    max_bytes: int | None = None
    fps: float | None = None
    sample_rate: int | None = None
    channels: int | None = None

    def fingerprint(self) -> str:
        """Compute stable SHA-256 fingerprint from canonical JSON representation."""
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_dict(self) -> dict:
        """Convert profile to dictionary including its computed fingerprint."""
        d = asdict(self)
        d["fingerprint"] = self.fingerprint()
        return d


# Pre-defined profiles tuned for target platforms and social destinations
PROFILES = {
    "instagram_reel_v1": Profile("instagram_reel_v1", 1, "video", 1080, 1920, "h264", "aac", 90, 100_000_000, 30),
    "youtube_short_v1": Profile("youtube_short_v1", 1, "video", 1080, 1920, "h264", "aac", 60, 100_000_000, 30),
    "linkedin_video_v1": Profile("linkedin_video_v1", 1, "video", 1920, 1080, "h264", "aac", 600, 200_000_000, 30),
    "discord_preview_v1": Profile("discord_preview_v1", 1, "video", 1280, 720, "h264", "aac", 120, 25_000_000, 30),
    "audio_voiceover_v1": Profile("audio_voiceover_v1", 1, "audio", audio_codec="aac", sample_rate=48000, channels=2, max_duration=3600, max_bytes=100_000_000),
}


def get_profile(name: str) -> Profile:
    """Retrieve profile by ID; raises ValueError if name is unrecognized."""
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError("unknown profile")
