from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    AdapterResult,
    BasePublisher,
    MalformedResponseError,
    TimeoutError,
    ValidationError,
)
from .config import InstagramConfig, SecretRef
from .transport import FakeHttpTransport, HttpTransport, raise_for_status
from ..security import redact


class InstagramPublisher(BasePublisher):
    """Instagram Graph API publisher for Reels and Media."""

    def __init__(
        self,
        config: InstagramConfig | None = None,
        transport: HttpTransport | None = None,
    ):
        self.config = config or InstagramConfig()
        self.transport = transport or FakeHttpTransport()

    def validate_target(self, target: Any) -> dict[str, Any]:
        account_id = target or self.config.account_id
        if not account_id:
            raise ValidationError("Instagram account ID (destination) is required")
        # Ensure credentials resolve
        token = self.config.access_token.resolve()
        if not token:
            raise ValidationError("Instagram access token is required")
        return {"valid": True, "account_id": str(account_id)}

    def _url(self, path: str) -> str:
        clean_base = self.config.base_url.rstrip("/")
        version = self.config.api_version.strip("/")
        clean_path = path.lstrip("/")
        return f"{clean_base}/{version}/{clean_path}"

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.access_token.resolve()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def create_upload(self, job: dict[str, Any]) -> dict[str, Any]:
        account_id = job.get("destination") or self.config.account_id
        if not account_id:
            raise ValidationError("Missing Instagram account destination in job")

        # Determine media type (REELS by default for video, or IMAGE)
        media_type = "REELS"
        caption = job.get("caption", "")

        url = self._url(f"{account_id}/media")
        body = {
            "media_type": media_type,
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": True,
        }

        resp = self.transport.request(
            "POST",
            url,
            headers=self._auth_headers(),
            json_body=body,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "Instagram container creation")

        data = resp.json()
        if not isinstance(data, dict) or "id" not in data:
            raise MalformedResponseError("Instagram container response missing 'id'")

        container_id = str(data["id"])
        upload_uri = data.get("uri") or self._url(f"{container_id}")

        return {
            "platform": "instagram",
            "container_id": container_id,
            "upload_uri": upload_uri,
            "account_id": account_id,
            "status": "IN_PROGRESS",
            "caption": caption,
            "container_response": redact(data),
        }

    def upload(self, remote: dict[str, Any], file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.is_file():
            raise ValidationError(f"File to upload does not exist: {file_path}")

        file_bytes = path.read_bytes()
        upload_uri = remote.get("upload_uri") or self._url(f"{remote['container_id']}")

        headers = self._auth_headers()
        headers.update({
            "offset": "0",
            "Content-Type": "application/octet-stream",
            "X-Entity-Length": str(len(file_bytes)),
        })

        resp = self.transport.request(
            "POST",
            upload_uri,
            headers=headers,
            data=file_bytes,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "Instagram media upload")

        return {
            **remote,
            "uploaded": True,
            "upload_status": "UPLOADED",
            "bytes_uploaded": len(file_bytes),
        }

    def wait_until_ready(self, remote: dict[str, Any]) -> dict[str, Any]:
        container_id = remote["container_id"]
        url = self._url(f"{container_id}")
        params = {"fields": "status_code,status"}

        attempts = 0
        max_attempts = max(1, self.config.max_poll_attempts)

        while attempts < max_attempts:
            attempts += 1
            resp = self.transport.request(
                "GET",
                url,
                headers=self._auth_headers(),
                params=params,
                timeout=self.config.timeout,
            )
            raise_for_status(resp, "Instagram status poll")

            data = resp.json()
            if not isinstance(data, dict):
                raise MalformedResponseError("Instagram status poll returned invalid JSON")

            status_code = str(data.get("status_code", "")).upper()
            if status_code == "FINISHED":
                return {
                    **remote,
                    "status": "FINISHED",
                    "poll_attempts": attempts,
                    "processing_metadata": redact(data),
                }
            if status_code == "ERROR":
                error_detail = data.get("status") or "Media processing failed"
                raise ValidationError(f"Instagram media processing failed: {error_detail}")
            if status_code == "EXPIRED":
                raise ValidationError("Instagram media container expired before publishing")

            # Still IN_PROGRESS
            continue

        raise TimeoutError(
            f"Instagram container {container_id} processing timed out after {attempts} attempts"
        )

    def publish(self, remote: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        account_id = remote["account_id"]
        container_id = remote["container_id"]

        url = self._url(f"{account_id}/media_publish")
        body = {"creation_id": container_id}

        resp = self.transport.request(
            "POST",
            url,
            headers=self._auth_headers(),
            json_body=body,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "Instagram media publish")

        data = resp.json()
        if not isinstance(data, dict) or "id" not in data:
            raise MalformedResponseError("Instagram publish response missing media 'id'")

        media_id = str(data["id"])
        return {
            **remote,
            "media_id": media_id,
            "publish_metadata": redact(data),
        }

    def verify(self, result: dict[str, Any]) -> AdapterResult:
        media_id = result.get("media_id")
        if not media_id:
            raise MalformedResponseError("Instagram publish result missing media_id for verification")

        url = self._url(f"{media_id}")
        params = {"fields": "id,permalink,status,media_type"}

        resp = self.transport.request(
            "GET",
            url,
            headers=self._auth_headers(),
            params=params,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "Instagram media verification")

        data = resp.json()
        permalink = data.get("permalink") or f"https://www.instagram.com/reel/{media_id}/"

        payload = {
            "platform": "instagram",
            "account_id": result["account_id"],
            "container_id": result["container_id"],
            "media_id": media_id,
            "status": "PUBLISHED",
            "permalink": permalink,
            "response_metadata": redact(data),
        }

        return AdapterResult(
            external_id=media_id,
            url=permalink,
            verified=True,
            payload=payload,
        )

    def rollback_or_cleanup(self, remote: dict[str, Any]) -> None:
        container_id = remote.get("container_id")
        if not container_id:
            return
        try:
            url = self._url(f"{container_id}")
            self.transport.request(
                "DELETE",
                url,
                headers=self._auth_headers(),
                timeout=self.config.timeout,
            )
        except Exception:
            # Cleanup is best-effort and must not mask the primary error
            pass

    def _check_readiness_live(self, timeout: float = 5.0) -> dict[str, Any]:
        token = self.config.access_token.resolve()
        if not token:
            return {"status": "unconfigured", "live": True, "error_class": "ValidationError"}
        try:
            url = self._url("me?fields=id,name")
            resp = self.transport.request("GET", url, headers=self._auth_headers(), timeout=timeout)
            raise_for_status(resp, context="Instagram live readiness check")
            return {"status": "ok", "live": True, "error_class": None}
        except Exception as err:
            return {"status": "error", "live": True, "error_class": err.__class__.__name__}
