"""Discord publisher adapter supporting bot tokens and incoming webhooks.

Implements message and file attachment delivery with explicit ephemeral retention
semantics (Discord messages and attachments are subject to server retention limits
and do not constitute permanent asset storage). Supports message deletion rollback.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    AdapterResult,
    BasePublisher,
    MalformedResponseError,
    ValidationError,
)
from .config import DiscordConfig
from .transport import FakeHttpTransport, HttpTransport, raise_for_status
from ..security import redact


class DiscordPublisher(BasePublisher):
    """Discord API adapter for file and message publishing with explicit ephemeral retention semantics."""


    def __init__(
        self,
        config: DiscordConfig | None = None,
        transport: HttpTransport | None = None,
    ):
        self.config = config or DiscordConfig()
        self.transport = transport or FakeHttpTransport()

    def _url(self, path: str) -> str:
        clean_base = self.config.base_url.rstrip("/")
        clean_path = path.lstrip("/")
        return f"{clean_base}/{clean_path}"

    def _auth_headers(self) -> dict[str, str]:
        if self.config.bot_token.is_configured():
            token = self.config.bot_token.resolve()
            return {
                "Authorization": f"Bot {token}",
                "Accept": "application/json",
            }
        return {"Accept": "application/json"}

    def validate_target(self, target: Any) -> dict[str, Any]:
        channel_id = target or self.config.channel_id
        if self.config.webhook_url.is_configured():
            return {"valid": True, "type": "webhook"}

        if not channel_id:
            raise ValidationError("Discord channel ID (destination) is required")

        if self.config.bot_token.is_configured():
            # Validate channel exists
            url = self._url(f"channels/{channel_id}")
            resp = self.transport.request(
                "GET",
                url,
                headers=self._auth_headers(),
                timeout=self.config.timeout,
            )
            raise_for_status(resp, f"Discord channel validation for '{channel_id}'")
            data = resp.json()
            return {"valid": True, "channel_id": str(channel_id), "guild_id": data.get("guild_id")}

        raise ValidationError("Discord requires either bot_token or webhook_url")

    def create_upload(self, job: dict[str, Any]) -> dict[str, Any]:
        channel_id = job.get("destination") or self.config.channel_id
        caption = job.get("caption", "")
        return {
            "platform": "discord",
            "channel_id": str(channel_id) if channel_id else "",
            "content": caption,
            "status": "PREPARED",
        }

    def upload(self, remote: dict[str, Any], file_path: str) -> dict[str, Any]:
        channel_id = remote.get("channel_id") or self.config.channel_id
        content = remote.get("content", "")

        files: dict[str, tuple[str, bytes, str]] | None = None
        if file_path:
            path = Path(file_path)
            if not path.is_file():
                raise ValidationError(f"File to upload does not exist: {file_path}")
            file_bytes = path.read_bytes()
            mime_type = "application/octet-stream"
            if path.suffix.lower() == ".mp4":
                mime_type = "video/mp4"
            elif path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                mime_type = "image/png"
            files = {"files[0]": (path.name, file_bytes, mime_type)}

        # Determine target URL
        if self.config.webhook_url.is_configured():
            url = self.config.webhook_url.resolve()
            headers = {"Accept": "application/json"}
        else:
            if not channel_id:
                raise ValidationError("Missing channel ID for Discord message send")
            url = self._url(f"channels/{channel_id}/messages")
            headers = self._auth_headers()

        json_payload = {"content": content}

        resp = self.transport.request(
            "POST",
            url,
            headers=headers,
            json_body=json_payload,
            files=files,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "Discord message delivery")

        data = resp.json()
        if not isinstance(data, dict) or "id" not in data:
            raise MalformedResponseError("Discord response missing message 'id'")

        message_id = str(data["id"])
        guild_id = data.get("guild_id")
        actual_channel = str(data.get("channel_id") or channel_id)

        attachments = data.get("attachments", [])
        attachment_url = attachments[0].get("url") if attachments else None

        return {
            **remote,
            "message_id": message_id,
            "channel_id": actual_channel,
            "guild_id": guild_id,
            "attachment_url": attachment_url,
            "message_response": redact(data),
            "status": "DELIVERED",
        }

    def wait_until_ready(self, remote: dict[str, Any]) -> dict[str, Any]:
        # Discord delivery is synchronous
        return {**remote, "status": "READY"}

    def publish(self, remote: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return {**remote, "status": "PUBLISHED"}

    def verify(self, result: dict[str, Any]) -> AdapterResult:
        message_id = result.get("message_id")
        channel_id = result.get("channel_id")
        guild_id = result.get("guild_id") or "@me"

        if not message_id or not channel_id:
            raise MalformedResponseError("Discord result missing message_id or channel_id")

        # Verify message exists if bot token is configured
        data = {}
        if self.config.bot_token.is_configured():
            url = self._url(f"channels/{channel_id}/messages/{message_id}")
            resp = self.transport.request(
                "GET",
                url,
                headers=self._auth_headers(),
                timeout=self.config.timeout,
            )
            raise_for_status(resp, "Discord message verification")
            data = resp.json()

        message_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

        payload = {
            "platform": "discord",
            "channel_id": channel_id,
            "message_id": message_id,
            "guild_id": guild_id,
            "attachment_url": result.get("attachment_url"),
            "url": message_url,
            "permanence": "ephemeral_channel_retention",
            "retention_note": "subject to discord channel retention policy; no long-term hosting guarantee",
            "response_metadata": redact(data or result.get("message_response", {})),
        }

        return AdapterResult(
            external_id=message_id,
            url=message_url,
            verified=True,
            payload=payload,
        )

    def rollback_or_cleanup(self, remote: dict[str, Any]) -> None:
        message_id = remote.get("message_id")
        channel_id = remote.get("channel_id")
        if not message_id or not channel_id:
            return
        if not self.config.bot_token.is_configured():
            return
        try:
            url = self._url(f"channels/{channel_id}/messages/{message_id}")
            self.transport.request(
                "DELETE",
                url,
                headers=self._auth_headers(),
                timeout=self.config.timeout,
            )
        except Exception:
            pass

    def _check_readiness_live(self, timeout: float = 5.0) -> dict[str, Any]:
        has_bot = self.config.bot_token.is_configured()
        has_webhook = self.config.webhook_url.is_configured()
        if not has_bot and not has_webhook:
            return {"status": "unconfigured", "live": True, "error_class": "ValidationError"}
        try:
            if has_bot:
                url = self._url("users/@me")
                resp = self.transport.request("GET", url, headers=self._auth_headers(), timeout=timeout)
            else:
                url = self.config.webhook_url.resolve()
                resp = self.transport.request("GET", url, timeout=timeout)
            raise_for_status(resp, context="Discord live readiness check")
            return {"status": "ok", "live": True, "error_class": None}
        except Exception as err:
            return {"status": "error", "live": True, "error_class": err.__class__.__name__}
