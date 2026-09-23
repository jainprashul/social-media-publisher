from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Publisher, ValidationError
from .config import (
    DiscordConfig,
    InstagramConfig,
    LinkedInConfig,
    PLATFORM_CAPABILITIES,
    SecretRef,
    YouTubeConfig,
)
from .discord import DiscordPublisher
from .fake import FakePublisher
from .instagram import InstagramPublisher
from .linkedin import LinkedInPublisher
from .transport import FakeHttpTransport, HttpTransport
from .youtube import YouTubePublisher


def get_publisher(
    platform: str,
    destination: str | None = None,
    transport: HttpTransport | None = None,
    **kwargs: Any,
) -> Publisher:
    """Factory to instantiate the appropriate platform publisher."""
    plat = platform.lower()

    if plat == "fake":
        return FakePublisher(**kwargs)

    if plat == "instagram":
        cfg = InstagramConfig(account_id=destination or "")
        return InstagramPublisher(config=cfg, transport=transport)

    if plat == "linkedin":
        cfg = LinkedInConfig(author_urn=destination or "")
        return LinkedInPublisher(config=cfg, transport=transport)

    if plat == "youtube":
        cfg = YouTubeConfig(channel_id=destination or "")
        return YouTubePublisher(config=cfg, transport=transport)

    if plat == "discord":
        cfg = DiscordConfig(channel_id=destination or "")
        return DiscordPublisher(config=cfg, transport=transport)

    raise ValueError(f"Unsupported publisher platform: '{platform}'")


def list_targets() -> list[dict[str, Any]]:
    """List all supported platform targets and their credential readiness without revealing secrets."""
    ig_cfg = InstagramConfig()
    li_cfg = LinkedInConfig()
    yt_cfg = YouTubeConfig()
    dc_cfg = DiscordConfig()

    return [
        {
            "platform": "instagram",
            "capabilities": PLATFORM_CAPABILITIES.get("instagram", []),
            "configured": ig_cfg.is_configured(),
            "api_version": ig_cfg.api_version,
            "secret_ref": ig_cfg.access_token.to_dict(),
        },
        {
            "platform": "linkedin",
            "capabilities": PLATFORM_CAPABILITIES.get("linkedin", []),
            "configured": li_cfg.is_configured(),
            "api_version": li_cfg.api_version,
            "secret_ref": li_cfg.access_token.to_dict(),
        },
        {
            "platform": "youtube",
            "capabilities": PLATFORM_CAPABILITIES.get("youtube", []),
            "configured": yt_cfg.is_configured(),
            "api_version": "v3",
            "secret_ref": yt_cfg.access_token.to_dict(),
        },
        {
            "platform": "discord",
            "capabilities": PLATFORM_CAPABILITIES.get("discord", []),
            "configured": dc_cfg.is_configured(),
            "api_version": "v10",
            "bot_token_ref": dc_cfg.bot_token.to_dict(),
            "webhook_ref": dc_cfg.webhook_url.to_dict(),
        },
        {
            "platform": "fake",
            "capabilities": PLATFORM_CAPABILITIES.get("fake", []),
            "configured": True,
            "api_version": "offline-v1",
            "secret_ref": {"configured": True, "source": "none"},
        },
    ]


def validate_target_config(platform: str, destination: str | None = None) -> dict[str, Any]:
    """Dry-run validate a platform target configuration without network access."""
    plat = platform.lower()
    if plat == "fake":
        return {"platform": "fake", "status": "valid", "destination": destination}

    if plat == "instagram":
        cfg = InstagramConfig(account_id=destination or "")
        issues = []
        if not destination and not cfg.account_id:
            issues.append("Missing account_id/destination")
        if not cfg.access_token.is_configured():
            issues.append(f"Secret '{cfg.access_token.env}' is not configured")
        return {
            "platform": "instagram",
            "status": "valid" if not issues else "invalid",
            "issues": issues,
            "config": cfg.to_dict(),
        }

    if plat == "linkedin":
        cfg = LinkedInConfig(author_urn=destination or "")
        issues = []
        if not destination and not cfg.author_urn:
            issues.append("Missing author_urn/destination")
        elif destination and not destination.startswith("urn:li:"):
            issues.append("Destination must start with 'urn:li:'")
        if not cfg.access_token.is_configured():
            issues.append(f"Secret '{cfg.access_token.env}' is not configured")
        return {
            "platform": "linkedin",
            "status": "valid" if not issues else "invalid",
            "issues": issues,
            "config": cfg.to_dict(),
        }

    if plat == "youtube":
        cfg = YouTubeConfig(channel_id=destination or "")
        issues = []
        if not cfg.access_token.is_configured():
            issues.append(f"Secret '{cfg.access_token.env}' is not configured")
        return {
            "platform": "youtube",
            "status": "valid" if not issues else "invalid",
            "issues": issues,
            "config": cfg.to_dict(),
        }

    if plat == "discord":
        cfg = DiscordConfig(channel_id=destination or "")
        issues = []
        if not cfg.webhook_url.is_configured() and not (cfg.bot_token.is_configured() and destination):
            issues.append("Requires either DISCORD_WEBHOOK_URL or (DISCORD_BOT_TOKEN and channel_id)")
        return {
            "platform": "discord",
            "status": "valid" if not issues else "invalid",
            "issues": issues,
            "config": cfg.to_dict(),
        }

    raise ValueError(f"Unknown platform '{platform}'")
