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


def list_targets(
    live: bool = False,
    transport: HttpTransport | None = None,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """List all supported platform targets and their credential readiness without revealing secrets."""
    from .base import is_live_allowed

    ig_cfg = InstagramConfig()
    li_cfg = LinkedInConfig()
    yt_cfg = YouTubeConfig()
    dc_cfg = DiscordConfig()

    targets = [
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

    if live:
        for t in targets:
            plat = t["platform"]
            if not is_live_allowed():
                t["live_readiness"] = {
                    "status": "gated",
                    "live": True,
                    "error_class": "LiveChecksDisabledError",
                    "message": "Live checks require DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1 environment variable",
                }
            elif not t["configured"]:
                t["live_readiness"] = {
                    "status": "unconfigured",
                    "live": True,
                    "error_class": "MissingConfigurationError",
                }
            else:
                try:
                    pub = get_publisher(plat, transport=transport)
                    t["live_readiness"] = pub.check_readiness(live=True, timeout=timeout)
                except Exception as err:
                    t["live_readiness"] = {
                        "status": "error",
                        "live": True,
                        "error_class": err.__class__.__name__,
                    }

    return targets


def validate_target_config(
    platform: str,
    destination: str | None = None,
    live: bool = False,
    transport: HttpTransport | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Validate a platform target configuration; offline dry-run by default, opt-in live check."""
    from .base import is_live_allowed

    plat = platform.lower()
    if plat not in ("fake", "instagram", "linkedin", "youtube", "discord"):
        raise ValueError(f"Unknown platform '{platform}'")

    if not live:
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

    # Live check path
    if not is_live_allowed():
        return {
            "platform": plat,
            "destination": destination,
            "status": "gated",
            "live": True,
            "error_class": "LiveChecksDisabledError",
            "issues": ["Live checks require DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1 environment variable"],
        }

    # Dry-run check first
    dry_run = validate_target_config(plat, destination=destination, live=False)
    if dry_run.get("status") != "valid":
        return {
            "platform": plat,
            "destination": destination,
            "status": "unconfigured",
            "live": True,
            "issues": dry_run.get("issues", []),
            "error_class": "ValidationError",
        }

    try:
        pub = get_publisher(plat, destination=destination, transport=transport)
        res = pub.check_readiness(live=True, timeout=timeout)
        return {
            "platform": plat,
            "destination": destination,
            "status": res["status"],
            "live": True,
            "error_class": res.get("error_class"),
            "issues": [] if res["status"] == "ok" else [f"Live check returned {res.get('error_class')}"],
        }
    except Exception as err:
        return {
            "platform": plat,
            "destination": destination,
            "status": "error",
            "live": True,
            "error_class": err.__class__.__name__,
            "issues": [f"Live check failed: {err.__class__.__name__}"],
        }
