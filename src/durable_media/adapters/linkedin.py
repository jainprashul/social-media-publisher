from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import (
    AdapterResult,
    BasePublisher,
    MalformedResponseError,
    TimeoutError,
    ValidationError,
)
from .config import LinkedInConfig
from .transport import FakeHttpTransport, HttpTransport, raise_for_status
from ..security import redact

URN_PATTERN = re.compile(r"^urn:li:(person|organization):[A-Za-z0-9_\-]+$")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


class LinkedInPublisher(BasePublisher):
    """LinkedIn Community Management / Posts API publisher with separate media & text-only flows."""

    def __init__(
        self,
        config: LinkedInConfig | None = None,
        transport: HttpTransport | None = None,
    ):
        self.config = config or LinkedInConfig()
        self.transport = transport or FakeHttpTransport()

    def validate_target(self, target: Any) -> dict[str, Any]:
        author_urn = target or self.config.author_urn
        if not author_urn:
            raise ValidationError("LinkedIn author URN (destination) is required")
        if not URN_PATTERN.match(str(author_urn)):
            raise ValidationError(
                f"Invalid LinkedIn author URN '{author_urn}'. Expected 'urn:li:person:<id>' or 'urn:li:organization:<id>'"
            )
        token = self.config.access_token.resolve()
        if not token:
            raise ValidationError("LinkedIn access token is required")
        return {"valid": True, "author_urn": str(author_urn)}

    def _url(self, path: str) -> str:
        clean_base = self.config.base_url.rstrip("/")
        clean_path = path.lstrip("/")
        return f"{clean_base}/{clean_path}"

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.access_token.resolve()
        return {
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": self.config.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Accept": "application/json",
        }

    def _detect_flow(self, job: dict[str, Any], file_path: str | None = None) -> str:
        # Explicit flow override from job if present
        flow_hint = job.get("flow")
        if flow_hint in ("text_only", "image", "video"):
            return flow_hint

        if not file_path:
            return "text_only"

        path = Path(file_path)
        if not path.exists() or path.stat().st_size == 0:
            return "text_only"

        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        if suffix in AUDIO_EXTENSIONS:
            raise ValidationError(
                f"LinkedIn does not support standalone audio publishing ({suffix})"
            )
        raise ValidationError(f"Unsupported file format '{suffix}' for LinkedIn publishing")

    def create_upload(self, job: dict[str, Any]) -> dict[str, Any]:
        author_urn = job.get("destination") or self.config.author_urn
        file_path = job.get("file_path")
        flow = self._detect_flow(job, file_path)

        if flow == "text_only":
            return {
                "platform": "linkedin",
                "flow": "text_only",
                "author": author_urn,
                "status": "READY",
                "caption": job.get("caption", ""),
            }

        headers = self._auth_headers()

        if flow == "image":
            url = self._url("rest/images?action=initializeUpload")
            body = {"initializeUploadRequest": {"owner": author_urn}}
            resp = self.transport.request(
                "POST",
                url,
                headers=headers,
                json_body=body,
                timeout=self.config.timeout,
            )
            raise_for_status(resp, "LinkedIn image initializeUpload")
            data = resp.json()
            val = data.get("value", {})
            upload_url = val.get("uploadUrl")
            image_urn = val.get("image")
            if not upload_url or not image_urn:
                raise MalformedResponseError("LinkedIn image initializeUpload missing uploadUrl or image URN")

            return {
                "platform": "linkedin",
                "flow": "image",
                "author": author_urn,
                "asset_urn": image_urn,
                "upload_url": upload_url,
                "status": "WAITING_UPLOAD",
                "caption": job.get("caption", ""),
                "init_response": redact(data),
            }

        if flow == "video":
            file_size = Path(file_path).stat().st_size if file_path and Path(file_path).is_file() else 1024
            url = self._url("rest/videos?action=initializeUpload")
            body = {
                "initializeUploadRequest": {
                    "owner": author_urn,
                    "fileSizeBytes": file_size,
                    "uploadCaptions": False,
                }
            }
            resp = self.transport.request(
                "POST",
                url,
                headers=headers,
                json_body=body,
                timeout=self.config.timeout,
            )
            raise_for_status(resp, "LinkedIn video initializeUpload")
            data = resp.json()
            val = data.get("value", {})
            instructions = val.get("uploadInstructions", [])
            upload_url = instructions[0].get("uploadUrl") if instructions else None
            video_urn = val.get("video")
            if not upload_url or not video_urn:
                raise MalformedResponseError("LinkedIn video initializeUpload missing uploadUrl or video URN")

            return {
                "platform": "linkedin",
                "flow": "video",
                "author": author_urn,
                "asset_urn": video_urn,
                "upload_url": upload_url,
                "status": "WAITING_UPLOAD",
                "caption": job.get("caption", ""),
                "init_response": redact(data),
            }

        raise ValidationError(f"Unknown LinkedIn flow: {flow}")

    def upload(self, remote: dict[str, Any], file_path: str) -> dict[str, Any]:
        if remote.get("flow") == "text_only":
            return {**remote, "uploaded": True, "status": "READY"}

        path = Path(file_path)
        if not path.is_file():
            raise ValidationError(f"File to upload does not exist: {file_path}")

        file_bytes = path.read_bytes()
        upload_url = remote.get("upload_url")
        if not upload_url:
            raise ValidationError("Missing upload_url for LinkedIn media upload")

        headers = {
            "Content-Type": "application/octet-stream",
        }

        resp = self.transport.request(
            "PUT",
            upload_url,
            headers=headers,
            data=file_bytes,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "LinkedIn media upload")

        return {
            **remote,
            "uploaded": True,
            "status": "UPLOADED",
            "bytes_uploaded": len(file_bytes),
        }

    def wait_until_ready(self, remote: dict[str, Any]) -> dict[str, Any]:
        flow = remote.get("flow")
        if flow in ("text_only", "image"):
            return {**remote, "status": "AVAILABLE"}

        # For video: poll video readiness
        video_urn = remote.get("asset_urn")
        if not video_urn:
            raise ValidationError("Missing video URN for LinkedIn readiness check")

        # Encode video URN for URL path
        encoded_urn = video_urn.replace(":", "%3A")
        url = self._url(f"rest/videos/{encoded_urn}")

        attempts = 0
        max_attempts = max(1, self.config.max_poll_attempts)

        while attempts < max_attempts:
            attempts += 1
            resp = self.transport.request(
                "GET",
                url,
                headers=self._auth_headers(),
                timeout=self.config.timeout,
            )
            raise_for_status(resp, "LinkedIn video status poll")

            data = resp.json()
            status = str(data.get("status", "")).upper()

            if status == "AVAILABLE":
                return {
                    **remote,
                    "status": "AVAILABLE",
                    "poll_attempts": attempts,
                    "video_metadata": redact(data),
                }
            if status == "PROCESSING_FAILED":
                raise ValidationError(f"LinkedIn video processing failed: {data.get('status')}")

            # WAITING_UPLOAD or PROCESSING
            continue

        raise TimeoutError(f"LinkedIn video {video_urn} processing timed out after {attempts} attempts")

    def publish(self, remote: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        author_urn = remote["author"]
        flow = remote.get("flow", "text_only")
        commentary = job.get("caption", remote.get("caption", ""))
        visibility = str(job.get("visibility", "PUBLIC")).upper()

        body: dict[str, Any] = {
            "author": author_urn,
            "commentary": commentary,
            "visibility": visibility,
            "distribution": {"feedDistribution": "MAIN_FEED"},
        }

        if flow in ("image", "video") and remote.get("asset_urn"):
            body["content"] = {
                "media": {
                    "id": remote["asset_urn"],
                    "title": job.get("title", ""),
                }
            }

        url = self._url("rest/posts")
        resp = self.transport.request(
            "POST",
            url,
            headers=self._auth_headers(),
            json_body=body,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "LinkedIn post creation")

        # Post ID is returned in x-restli-id header or body
        post_urn = resp.get_header("x-restli-id")
        data = {}
        try:
            data = resp.json()
            if not post_urn and isinstance(data, dict):
                post_urn = data.get("id")
        except Exception:
            pass

        if not post_urn:
            raise MalformedResponseError("LinkedIn post creation response missing post URN")

        return {
            **remote,
            "post_urn": str(post_urn),
            "post_response": redact(data),
        }

    def verify(self, result: dict[str, Any]) -> AdapterResult:
        post_urn = result.get("post_urn")
        if not post_urn:
            raise MalformedResponseError("LinkedIn publish result missing post_urn for verification")

        encoded_post_urn = post_urn.replace(":", "%3A")
        url = self._url(f"rest/posts/{encoded_post_urn}")

        resp = self.transport.request(
            "GET",
            url,
            headers=self._auth_headers(),
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "LinkedIn post verification")

        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        permalink = f"https://www.linkedin.com/feed/update/{post_urn}"

        payload = {
            "platform": "linkedin",
            "author": result["author"],
            "post_urn": post_urn,
            "asset_urn": result.get("asset_urn"),
            "flow": result.get("flow"),
            "url": permalink,
            "response_metadata": redact(data),
        }

        return AdapterResult(
            external_id=post_urn,
            url=permalink,
            verified=True,
            payload=payload,
        )

    def rollback_or_cleanup(self, remote: dict[str, Any]) -> None:
        post_urn = remote.get("post_urn")
        if not post_urn:
            return
        try:
            encoded = post_urn.replace(":", "%3A")
            self.transport.request(
                "DELETE",
                self._url(f"rest/posts/{encoded}"),
                headers=self._auth_headers(),
                timeout=self.config.timeout,
            )
        except Exception:
            pass
