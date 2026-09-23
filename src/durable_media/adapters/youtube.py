from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import (
    AdapterResult,
    BasePublisher,
    MalformedResponseError,
    RateLimitError,
    TimeoutError,
    ValidationError,
)
from .config import SecretRef, YouTubeConfig
from .transport import FakeHttpTransport, HttpTransport, raise_for_status
from ..security import redact

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class YouTubePublisher(BasePublisher):
    """YouTube Data API v3 publisher with resumable uploads, chunking, and processing polling."""

    def __init__(
        self,
        config: YouTubeConfig | None = None,
        transport: HttpTransport | None = None,
    ):
        self.config = config or YouTubeConfig()
        self.transport = transport or FakeHttpTransport()

    def validate_target(self, target: Any) -> dict[str, Any]:
        channel_id = target or self.config.channel_id or "default"
        token = self.config.access_token.resolve()
        if not token:
            raise ValidationError("YouTube access token is required")
        return {"valid": True, "channel_id": str(channel_id)}

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.access_token.resolve()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _extract_tags(self, text: str) -> list[str]:
        # Extract #hashtags from text
        tags = [m.group(1) for m in re.finditer(r"#(\w+)", text)]
        return tags[:15]

    def create_upload(self, job: dict[str, Any]) -> dict[str, Any]:
        channel_id = job.get("destination") or self.config.channel_id or "default"
        file_path = job.get("file_path")

        # Validate video format if file_path is provided
        if file_path:
            path = Path(file_path)
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValidationError(
                    f"YouTube only supports video uploads, got '{path.suffix}'"
                )

        caption = job.get("caption", "")
        title = job.get("title") or (caption.split("\n")[0][:95] if caption else f"Video {job.get('job_id', 'upload')}")
        description = caption
        tags = job.get("tags") or self._extract_tags(caption)
        visibility = str(job.get("visibility", "public")).lower()
        if visibility not in ("public", "private", "unlisted"):
            visibility = "private"

        file_size = Path(file_path).stat().st_size if file_path and Path(file_path).is_file() else 0

        url = f"{self.config.upload_url}?uploadType=resumable&part=snippet,status"
        headers = self._auth_headers()
        headers.update({
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
        })
        if file_size > 0:
            headers["X-Upload-Content-Length"] = str(file_size)

        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "28",  # Science & Technology
            },
            "status": {
                "privacyStatus": visibility,
            },
        }

        resp = self.transport.request(
            "POST",
            url,
            headers=headers,
            json_body=metadata,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "YouTube resumable session creation")

        session_url = resp.get_header("Location")
        if not session_url:
            raise MalformedResponseError("YouTube resumable upload response missing Location header")

        return {
            "platform": "youtube",
            "channel_id": channel_id,
            "session_url": session_url,
            "file_size": file_size,
            "title": title,
            "status": "SESSION_CREATED",
            "caption": caption,
            "privacy_status": visibility,
        }

    def resume_inquiry(self, session_url: str, total_size: int) -> int:
        """Query YouTube for the number of bytes received so far."""
        headers = self._auth_headers()
        headers["Content-Range"] = f"bytes */{total_size}"

        resp = self.transport.request(
            "PUT",
            session_url,
            headers=headers,
            timeout=self.config.timeout,
        )
        if resp.status_code == 308:
            range_header = resp.get_header("Range")
            if range_header and range_header.startswith("bytes=0-"):
                try:
                    return int(range_header.split("-")[1]) + 1
                except Exception:
                    return 0
        return 0

    def upload(self, remote: dict[str, Any], file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.is_file():
            raise ValidationError(f"File to upload does not exist: {file_path}")

        session_url = remote.get("session_url")
        if not session_url:
            raise ValidationError("Missing YouTube resumable session URL")

        file_bytes = path.read_bytes()
        total_size = len(file_bytes)
        chunk_size = self.config.chunk_size_bytes or (1024 * 1024)

        offset = 0
        last_resp = None

        while offset < total_size:
            chunk_end = min(offset + chunk_size, total_size)
            chunk_data = file_bytes[offset:chunk_end]
            content_range = f"bytes {offset}-{chunk_end - 1}/{total_size}"

            headers = {
                "Content-Type": "video/mp4",
                "Content-Range": content_range,
            }

            resp = self.transport.request(
                "PUT",
                session_url,
                headers=headers,
                data=chunk_data,
                timeout=self.config.timeout,
            )

            # Check status code
            if resp.status_code == 308:
                # 308 Resume Incomplete - update offset from Range header or chunk_end
                range_header = resp.get_header("Range")
                if range_header and range_header.startswith("bytes=0-"):
                    try:
                        offset = int(range_header.split("-")[1]) + 1
                    except Exception:
                        offset = chunk_end
                else:
                    offset = chunk_end
                continue

            raise_for_status(resp, "YouTube chunk upload")
            last_resp = resp
            break

        if not last_resp:
            raise MalformedResponseError("YouTube upload loop finished without completion response")

        data = last_resp.json()
        if not isinstance(data, dict) or "id" not in data:
            raise MalformedResponseError("YouTube video upload response missing video 'id'")

        video_id = str(data["id"])
        return {
            **remote,
            "video_id": video_id,
            "bytes_uploaded": total_size,
            "upload_response": redact(data),
            "status": "UPLOADED",
        }

    def wait_until_ready(self, remote: dict[str, Any]) -> dict[str, Any]:
        video_id = remote.get("video_id")
        if not video_id:
            raise ValidationError("Missing video ID for YouTube processing poll")

        clean_base = self.config.base_url.rstrip("/")
        url = f"{clean_base}/youtube/v3/videos?id={video_id}&part=status,processingDetails,snippet"

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
            raise_for_status(resp, "YouTube video processing poll")

            data = resp.json()
            items = data.get("items", [])
            if not items:
                raise ValidationError(f"YouTube video {video_id} not found during processing check")

            item = items[0]
            status = item.get("status", {})
            upload_status = str(status.get("uploadStatus", "")).lower()

            if upload_status == "processed":
                return {
                    **remote,
                    "status": "PROCESSED",
                    "poll_attempts": attempts,
                    "video_metadata": redact(item),
                }

            if upload_status in ("failed", "rejected"):
                reason = status.get("rejectionReason") or status.get("failureReason") or upload_status
                raise ValidationError(f"YouTube video processing rejected: {reason}")

            proc = item.get("processingDetails", {})
            proc_status = str(proc.get("processingStatus", "")).lower()
            if proc_status == "succeeded":
                return {
                    **remote,
                    "status": "PROCESSED",
                    "poll_attempts": attempts,
                    "video_metadata": redact(item),
                }
            if proc_status == "failed":
                raise ValidationError("YouTube video processing failed according to processingDetails")

            # Still processing / uploaded
            continue

        raise TimeoutError(f"YouTube video {video_id} processing timed out after {attempts} attempts")

    def upload_thumbnail(self, video_id: str, thumbnail_path: str) -> dict[str, Any]:
        path = Path(thumbnail_path)
        if not path.is_file():
            raise ValidationError(f"Thumbnail file does not exist: {thumbnail_path}")

        file_bytes = path.read_bytes()
        clean_base = self.config.base_url.rstrip("/")
        url = f"{clean_base}/upload/youtube/v3/thumbnails/set?videoId={video_id}"

        headers = self._auth_headers()
        headers["Content-Type"] = "image/jpeg"

        resp = self.transport.request(
            "POST",
            url,
            headers=headers,
            data=file_bytes,
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "YouTube thumbnail upload")
        return redact(resp.json())

    def publish(self, remote: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        video_id = remote["video_id"]
        # If job provides a thumbnail artifact, upload it
        thumbnail_path = job.get("thumbnail_path")
        thumb_res = None
        if thumbnail_path:
            try:
                thumb_res = self.upload_thumbnail(video_id, thumbnail_path)
            except Exception:
                pass

        return {
            **remote,
            "status": "PUBLISHED",
            "thumbnail_result": thumb_res,
        }

    def verify(self, result: dict[str, Any]) -> AdapterResult:
        video_id = result.get("video_id")
        if not video_id:
            raise MalformedResponseError("YouTube publish result missing video_id for verification")

        clean_base = self.config.base_url.rstrip("/")
        url = f"{clean_base}/youtube/v3/videos?id={video_id}&part=status,snippet"

        resp = self.transport.request(
            "GET",
            url,
            headers=self._auth_headers(),
            timeout=self.config.timeout,
        )
        raise_for_status(resp, "YouTube video verification")

        data = resp.json()
        items = data.get("items", [])
        if not items:
            raise ValidationError(f"YouTube video {video_id} could not be verified; not found")

        item = items[0]
        watch_url = f"https://www.youtube.com/watch?v={video_id}"

        payload = {
            "platform": "youtube",
            "video_id": video_id,
            "channel_id": result.get("channel_id"),
            "session_url": redact(result.get("session_url")),
            "title": result.get("title"),
            "watch_url": watch_url,
            "url": watch_url,
            "response_metadata": redact(item),
        }

        return AdapterResult(
            external_id=video_id,
            url=watch_url,
            verified=True,
            payload=payload,
        )

    def rollback_or_cleanup(self, remote: dict[str, Any]) -> None:
        video_id = remote.get("video_id")
        if not video_id:
            return
        try:
            clean_base = self.config.base_url.rstrip("/")
            self.transport.request(
                "DELETE",
                f"{clean_base}/youtube/v3/videos?id={video_id}",
                headers=self._auth_headers(),
                timeout=self.config.timeout,
            )
        except Exception:
            pass

    def _check_readiness_live(self, timeout: float = 5.0) -> dict[str, Any]:
        token = self.config.access_token.resolve()
        if not token:
            return {"status": "unconfigured", "live": True, "error_class": "ValidationError"}
        try:
            clean_base = self.config.base_url.rstrip("/")
            url = f"{clean_base}/youtube/v3/channels?part=id&mine=true"
            resp = self.transport.request("GET", url, headers=self._auth_headers(), timeout=timeout)
            raise_for_status(resp, context="YouTube live readiness check")
            return {"status": "ok", "live": True, "error_class": None}
        except Exception as err:
            return {"status": "error", "live": True, "error_class": err.__class__.__name__}
