"""Platform adapter configuration and secret reference management.

Manages credentials through SecretRef objects that isolate secret resolution
from serialization, preventing tokens from leaking into logs, terminal output,
or audit manifests. Defines platform-specific endpoint and timeout configurations.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import AuthenticationError


class SecretRef:
    """Secure reference to a secret stored in an environment variable or file.

    Guarantees that raw secret values are never exposed through repr(), str(),
    custom serialization, or dictionary export. Secrets are only resolved into
    memory at the moment of HTTP request construction.
    """

    def __init__(
        self,
        env: str | None = None,
        file_path: Path | str | None = None,
        value: str | None = None,
    ):
        self.env = env
        self.file_path = Path(file_path).expanduser() if file_path is not None else None
        self._value = value

    @classmethod
    def from_env(cls, env_var: str) -> SecretRef:
        """Create reference to an environment variable name."""
        return cls(env=env_var)

    @classmethod
    def from_file(cls, path: Path | str) -> SecretRef:
        """Create reference to a secret file on disk."""
        return cls(file_path=path)

    @classmethod
    def from_value(cls, val: str) -> SecretRef:
        """Create reference to an in-memory secret string (redacted upon repr/str)."""
        return cls(value=val)

    def resolve(self) -> str:
        """Resolve and return the plaintext secret value for HTTP transmission.

        Raises AuthenticationError if the referenced environment variable or file is missing.
        """

        if self._value is not None:
            return self._value
        if self.env:
            val = os.environ.get(self.env)
            if val is not None and val != "":
                return val
            raise AuthenticationError(f"Required secret environment variable '{self.env}' is not set")
        if self.file_path:
            if not self.file_path.is_file():
                raise AuthenticationError(f"Required secret file '{self.file_path}' does not exist")
            return self.file_path.read_text(encoding="utf-8").strip()
        raise AuthenticationError("No secret reference configured")

    def is_configured(self) -> bool:
        if self._value is not None and self._value != "":
            return True
        if self.env and bool(os.environ.get(self.env)):
            return True
        if self.file_path and self.file_path.is_file():
            return True
        return False

    def __repr__(self) -> str:
        if self.env:
            return f"SecretRef(env={self.env!r})"
        if self.file_path:
            return f"SecretRef(file_path={str(self.file_path)!r})"
        if self._value:
            return "SecretRef(value='[REDACTED]')"
        return "SecretRef(empty)"

    def __str__(self) -> str:
        return "[REDACTED]"

    def __redact__(self) -> str:
        return "[REDACTED]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "env" if self.env else ("file" if self.file_path else ("value" if self._value else "none")),
            "reference": self.env or (str(self.file_path) if self.file_path else None),
            "configured": self.is_configured(),
        }


@dataclass
class InstagramConfig:
    account_id: str = ""
    access_token: SecretRef = field(default_factory=lambda: SecretRef(env="INSTAGRAM_ACCESS_TOKEN"))
    api_version: str = "v21.0"
    base_url: str = "https://graph.facebook.com"
    timeout: float = 30.0
    max_poll_attempts: int = 10
    poll_interval: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.account_id) and self.access_token.is_configured()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": "instagram",
            "account_id": self.account_id,
            "api_version": self.api_version,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "token_configured": self.access_token.is_configured(),
            "secret_ref": self.access_token.to_dict(),
        }

    def __repr__(self) -> str:
        return (
            f"InstagramConfig(account_id={self.account_id!r}, "
            f"access_token={self.access_token!r}, "
            f"api_version={self.api_version!r}, base_url={self.base_url!r})"
        )


@dataclass
class LinkedInConfig:
    author_urn: str = ""
    access_token: SecretRef = field(default_factory=lambda: SecretRef(env="LINKEDIN_ACCESS_TOKEN"))
    api_version: str = "202401"
    base_url: str = "https://api.linkedin.com"
    timeout: float = 30.0
    max_poll_attempts: int = 10
    poll_interval: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.author_urn) and self.access_token.is_configured()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": "linkedin",
            "author_urn": self.author_urn,
            "api_version": self.api_version,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "token_configured": self.access_token.is_configured(),
            "secret_ref": self.access_token.to_dict(),
        }

    def __repr__(self) -> str:
        return (
            f"LinkedInConfig(author_urn={self.author_urn!r}, "
            f"access_token={self.access_token!r}, "
            f"api_version={self.api_version!r}, base_url={self.base_url!r})"
        )


@dataclass
class YouTubeConfig:
    channel_id: str = ""
    access_token: SecretRef = field(default_factory=lambda: SecretRef(env="YOUTUBE_ACCESS_TOKEN"))
    base_url: str = "https://www.googleapis.com"
    upload_url: str = "https://www.googleapis.com/upload/youtube/v3/videos"
    chunk_size_bytes: int = 1024 * 1024
    timeout: float = 30.0
    max_poll_attempts: int = 10
    poll_interval: float = 0.0

    def is_configured(self) -> bool:
        return self.access_token.is_configured()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": "youtube",
            "channel_id": self.channel_id,
            "base_url": self.base_url,
            "upload_url": self.upload_url,
            "chunk_size_bytes": self.chunk_size_bytes,
            "timeout": self.timeout,
            "token_configured": self.access_token.is_configured(),
            "secret_ref": self.access_token.to_dict(),
        }

    def __repr__(self) -> str:
        return (
            f"YouTubeConfig(channel_id={self.channel_id!r}, "
            f"access_token={self.access_token!r}, "
            f"base_url={self.base_url!r})"
        )


@dataclass
class DiscordConfig:
    channel_id: str = ""
    bot_token: SecretRef = field(default_factory=lambda: SecretRef(env="DISCORD_BOT_TOKEN"))
    webhook_url: SecretRef = field(default_factory=lambda: SecretRef(env="DISCORD_WEBHOOK_URL"))
    base_url: str = "https://discord.com/api/v10"
    timeout: float = 30.0

    def is_configured(self) -> bool:
        has_auth = self.bot_token.is_configured() or self.webhook_url.is_configured()
        if self.webhook_url.is_configured():
            return True
        return bool(self.channel_id) and has_auth

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": "discord",
            "channel_id": self.channel_id,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "bot_token_configured": self.bot_token.is_configured(),
            "webhook_configured": self.webhook_url.is_configured(),
            "bot_token_ref": self.bot_token.to_dict(),
            "webhook_ref": self.webhook_url.to_dict(),
        }

    def __repr__(self) -> str:
        return (
            f"DiscordConfig(channel_id={self.channel_id!r}, "
            f"bot_token={self.bot_token!r}, webhook_url={self.webhook_url!r}, "
            f"base_url={self.base_url!r})"
        )


PLATFORM_CAPABILITIES: dict[str, list[str]] = {
    "instagram": ["reels", "images", "resumable_upload", "polling"],
    "linkedin": ["text", "images", "videos", "polling"],
    "youtube": ["videos", "shorts", "resumable_chunks", "polling"],
    "discord": ["messages", "file_attachments"],
    "fake": ["mock_publishing", "error_simulation"],
}
