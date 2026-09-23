from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from durable_media.adapters.base import (
    AdapterError,
    AdapterResult,
    AuthenticationError,
    MalformedResponseError,
    Publisher,
    RateLimitError,
    ServerError,
    TimeoutError,
    UnknownRemoteStateError,
    ValidationError,
)
from durable_media.adapters.config import (
    DiscordConfig,
    InstagramConfig,
    LinkedInConfig,
    SecretRef,
    YouTubeConfig,
)
from durable_media.adapters.discord import DiscordPublisher
from durable_media.adapters.fake import FakePublisher
from durable_media.adapters.instagram import InstagramPublisher
from durable_media.adapters.linkedin import LinkedInPublisher
from durable_media.adapters.registry import (
    get_publisher,
    list_targets,
    validate_target_config,
)
from durable_media.adapters.transport import (
    FakeHttpTransport,
    TransportResponse,
    raise_for_status,
    sanitize_headers,
    sanitize_url,
)
from durable_media.adapters.youtube import YouTubePublisher
from durable_media.approval import approve, job_fingerprint
from durable_media.config import WorkspaceConfig
from durable_media.derivatives import derive
from durable_media.ingest import ingest
from durable_media.orchestration import prepare, publish, reconcile_job
from durable_media.registry import Registry


# =====================================================================
# Task 1 Tests: Secret References, Transport Boundaries, Redaction
# =====================================================================

def test_secret_ref_never_leaks_in_repr_or_str():
    secret_value = "super-secret-token-12345"
    ref = SecretRef.from_value(secret_value)
    assert secret_value not in repr(ref)
    assert secret_value not in str(ref)
    assert repr(ref) == "SecretRef(value='[REDACTED]')"
    assert str(ref) == "[REDACTED]"
    assert ref.resolve() == secret_value


def test_secret_ref_from_env_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "env-secret-999")
    env_ref = SecretRef.from_env("TEST_API_TOKEN")
    assert env_ref.is_configured()
    assert env_ref.resolve() == "env-secret-999"
    assert "env-secret-999" not in repr(env_ref)
    assert repr(env_ref) == "SecretRef(env='TEST_API_TOKEN')"

    # Missing env
    missing_env = SecretRef.from_env("NON_EXISTENT_VAR_XYZ")
    assert not missing_env.is_configured()
    with pytest.raises(AuthenticationError):
        missing_env.resolve()

    # From file
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("file-secret-888\n", encoding="utf-8")
    file_ref = SecretRef.from_file(secret_file)
    assert file_ref.is_configured()
    assert file_ref.resolve() == "file-secret-888"
    assert "file-secret-888" not in repr(file_ref)

    # Missing file
    missing_file = SecretRef.from_file(tmp_path / "missing.txt")
    assert not missing_file.is_configured()
    with pytest.raises(AuthenticationError):
        missing_file.resolve()


def test_config_repr_and_dict_redact_secrets():
    token = "raw-ig-token-value"
    cfg = InstagramConfig(
        account_id="178414000",
        access_token=SecretRef.from_value(token),
    )
    assert token not in repr(cfg)
    assert token not in json.dumps(cfg.to_dict())
    assert cfg.to_dict()["token_configured"] is True


def test_transport_raise_for_status_mappings():
    # Success & 308 (YouTube chunk status)
    raise_for_status(TransportResponse(status_code=200, text="OK"))
    raise_for_status(TransportResponse(status_code=308, text="Resume Incomplete"))

    # Timeout
    with pytest.raises(TimeoutError):
        raise_for_status(TransportResponse(status_code=408, text="Request Timeout"))
    with pytest.raises(TimeoutError):
        raise_for_status(TransportResponse(status_code=504, text="Gateway Timeout"))

    # Rate Limit
    with pytest.raises(RateLimitError):
        raise_for_status(TransportResponse(status_code=429, text="Too Many Requests"))

    # Server Error
    with pytest.raises(ServerError):
        raise_for_status(TransportResponse(status_code=500, text="Internal Server Error"))
    with pytest.raises(ServerError):
        raise_for_status(TransportResponse(status_code=503, text="Service Unavailable"))

    # Authentication
    with pytest.raises(AuthenticationError):
        raise_for_status(TransportResponse(status_code=401, text="Unauthorized"))
    with pytest.raises(AuthenticationError):
        raise_for_status(TransportResponse(status_code=403, text="Forbidden"))

    # Validation
    with pytest.raises(ValidationError):
        raise_for_status(TransportResponse(status_code=400, text="Bad Request"))
    with pytest.raises(ValidationError):
        raise_for_status(TransportResponse(status_code=422, text="Unprocessable Entity"))


def test_transport_response_json_parsing():
    valid = TransportResponse(status_code=200, text='{"key": "value"}')
    assert valid.json() == {"key": "value"}

    malformed = TransportResponse(status_code=200, text='{not valid json')
    with pytest.raises(MalformedResponseError):
        malformed.json()


def test_safe_transport_logging():
    headers = {
        "Authorization": "Bearer confidential-token",
        "X-Api-Key": "secret-key",
        "Content-Type": "application/json",
    }
    sanitized = sanitize_headers(headers)
    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["X-Api-Key"] == "[REDACTED]"
    assert sanitized["Content-Type"] == "application/json"

    url = "https://example.com/api?token=sensitive123&user=john"
    assert "sensitive123" not in sanitize_url(url)
    assert sanitize_url(url) == "https://example.com/api?token=[REDACTED]&user=john"


# =====================================================================
# Task 2 Tests: Instagram Adapter
# =====================================================================

def test_instagram_success_lifecycle(tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-video-bytes")

    transport = FakeHttpTransport()
    # 1. Container creation
    transport.register_response(
        "POST", r"/media$",
        status_code=200,
        json_body={"id": "container_ig_999", "uri": "https://upload.ig.example/container_ig_999"},
    )
    # 2. Upload
    transport.register_response(
        "POST", r"/container_ig_999",
        status_code=200,
        json_body={"success": True},
    )
    # 3. Status poll (FINISHED)
    transport.register_response(
        "GET", r"/container_ig_999\?fields=status_code,status",
        status_code=200,
        json_body={"status_code": "FINISHED", "id": "container_ig_999"},
    )
    # 4. Publish
    transport.register_response(
        "POST", r"/media_publish$",
        status_code=200,
        json_body={"id": "media_ig_777"},
    )
    # 5. Verify
    transport.register_response(
        "GET", r"/media_ig_777\?fields=id,permalink,status,media_type",
        status_code=200,
        json_body={
            "id": "media_ig_777",
            "permalink": "https://www.instagram.com/reel/media_ig_777/",
            "status": "PUBLISHED",
        },
    )

    config = InstagramConfig(
        account_id="17841400000000000",
        access_token=SecretRef.from_value("test-ig-token"),
    )
    adapter = InstagramPublisher(config=config, transport=transport)

    job = {
        "job_id": "job-ig-1",
        "destination": "17841400000000000",
        "caption": "Check out this reel #ai",
    }
    result = adapter.publish_job(job, str(media))

    assert result["external_id"] == "media_ig_777"
    assert result["url"] == "https://www.instagram.com/reel/media_ig_777/"
    assert result["verified"] is True
    assert result["container_id"] == "container_ig_999"
    assert result["platform"] == "instagram"


def test_instagram_processing_failure_raises_validation_error(tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-video-bytes")

    transport = FakeHttpTransport()
    transport.register_response("POST", r"/media$", json_body={"id": "container_fail"})
    transport.register_response("POST", r"/container_fail", json_body={"success": True})
    transport.register_response(
        "GET", r"/container_fail",
        json_body={"status_code": "ERROR", "status": "Audio codec unsupported"},
    )
    transport.register_response("DELETE", r"/container_fail", json_body={"success": True})

    adapter = InstagramPublisher(
        config=InstagramConfig(
            account_id="178414",
            access_token=SecretRef.from_value("tok"),
        ),
        transport=transport,
    )

    with pytest.raises(ValidationError) as exc:
        adapter.publish_job({"destination": "178414"}, str(media))
    assert "Audio codec unsupported" in str(exc.value)


def test_instagram_polling_timeout_raises_timeout_error(tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-video-bytes")

    transport = FakeHttpTransport()
    transport.register_response("POST", r"/media$", json_body={"id": "container_timeout"})
    transport.register_response("POST", r"/container_timeout", json_body={"success": True})
    # Status stays IN_PROGRESS
    transport.register_response(
        "GET", r"/container_timeout",
        json_body={"status_code": "IN_PROGRESS"},
    )
    transport.register_response("DELETE", r"/container_timeout", json_body={"success": True})

    adapter = InstagramPublisher(
        config=InstagramConfig(
            account_id="178414",
            access_token=SecretRef.from_value("tok"),
            max_poll_attempts=3,
        ),
        transport=transport,
    )

    with pytest.raises(TimeoutError) as exc:
        adapter.publish_job({"destination": "178414"}, str(media))
    assert "timed out" in str(exc.value)


def test_instagram_rate_limit_and_auth_failure(tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-video-bytes")

    transport_429 = FakeHttpTransport()
    transport_429.register_response("POST", r"/media$", status_code=429, text="Rate limit exceeded")
    adapter_429 = InstagramPublisher(
        config=InstagramConfig(account_id="178", access_token=SecretRef.from_value("tok")),
        transport=transport_429,
    )
    with pytest.raises(RateLimitError):
        adapter_429.publish_job({"destination": "178"}, str(media))

    transport_401 = FakeHttpTransport()
    transport_401.register_response("POST", r"/media$", status_code=401, text="Invalid OAuth token")
    adapter_401 = InstagramPublisher(
        config=InstagramConfig(account_id="178", access_token=SecretRef.from_value("tok")),
        transport=transport_401,
    )
    with pytest.raises(AuthenticationError):
        adapter_401.publish_job({"destination": "178"}, str(media))


# =====================================================================
# Task 3 Tests: LinkedIn Adapter
# =====================================================================

def test_linkedin_text_only_flow():
    transport = FakeHttpTransport()
    # Post creation
    transport.register_response(
        "POST", r"/rest/posts$",
        status_code=201,
        headers={"x-restli-id": "urn:li:share:text-999"},
        json_body={"id": "urn:li:share:text-999"},
    )
    # Post verify
    transport.register_response(
        "GET", r"/rest/posts/",
        status_code=200,
        json_body={"id": "urn:li:share:text-999", "commentary": "Pure text post"},
    )

    adapter = LinkedInPublisher(
        config=LinkedInConfig(
            author_urn="urn:li:person:author-1",
            access_token=SecretRef.from_value("li-token"),
        ),
        transport=transport,
    )

    job = {
        "destination": "urn:li:person:author-1",
        "caption": "Pure text post",
        "flow": "text_only",
    }
    result = adapter.publish_job(job, "")
    assert result["external_id"] == "urn:li:share:text-999"
    assert result["url"] == "https://www.linkedin.com/feed/update/urn:li:share:text-999"
    assert result["flow"] == "text_only"
    assert result["verified"] is True


def test_linkedin_video_flow_success(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake-mp4-stream")

    transport = FakeHttpTransport()
    # 1. Init video
    transport.register_response(
        "POST", r"/rest/videos\?action=initializeUpload",
        status_code=200,
        json_body={
            "value": {
                "uploadInstructions": [{"uploadUrl": "https://upload.linkedin.example/video-1"}],
                "video": "urn:li:video:vid-123",
            }
        },
    )
    # 2. Upload PUT
    transport.register_response(
        "PUT", r"/video-1$",
        status_code=200,
    )
    # 3. Poll video readiness (AVAILABLE)
    transport.register_response(
        "GET", r"/rest/videos/",
        status_code=200,
        json_body={"status": "AVAILABLE", "id": "urn:li:video:vid-123"},
    )
    # 4. Create post
    transport.register_response(
        "POST", r"/rest/posts$",
        status_code=201,
        headers={"x-restli-id": "urn:li:ugcPost:ugc-555"},
        json_body={"id": "urn:li:ugcPost:ugc-555"},
    )
    # 5. Verify post
    transport.register_response(
        "GET", r"/rest/posts/",
        status_code=200,
        json_body={"id": "urn:li:ugcPost:ugc-555"},
    )

    adapter = LinkedInPublisher(
        config=LinkedInConfig(
            author_urn="urn:li:organization:corp-9",
            access_token=SecretRef.from_value("tok"),
        ),
        transport=transport,
    )

    job = {
        "destination": "urn:li:organization:corp-9",
        "caption": "Video announcement",
    }
    result = adapter.publish_job(job, str(media))
    assert result["external_id"] == "urn:li:ugcPost:ugc-555"
    assert result["asset_urn"] == "urn:li:video:vid-123"
    assert result["flow"] == "video"
    assert result["verified"] is True


def test_linkedin_image_flow_success(tmp_path):
    media = tmp_path / "banner.png"
    media.write_bytes(b"fake-png-bytes")

    transport = FakeHttpTransport()
    transport.register_response(
        "POST", r"/rest/images\?action=initializeUpload",
        json_body={"value": {"uploadUrl": "https://upload.linkedin.example/img-1", "image": "urn:li:image:img-456"}},
    )
    transport.register_response("PUT", r"/img-1$", status_code=200)
    transport.register_response(
        "POST", r"/rest/posts$",
        status_code=201,
        headers={"x-restli-id": "urn:li:share:share-img"},
    )
    transport.register_response("GET", r"/rest/posts/", json_body={"id": "urn:li:share:share-img"})

    adapter = LinkedInPublisher(
        config=LinkedInConfig(author_urn="urn:li:person:user-1", access_token=SecretRef.from_value("tok")),
        transport=transport,
    )
    result = adapter.publish_job({"destination": "urn:li:person:user-1"}, str(media))
    assert result["external_id"] == "urn:li:share:share-img"
    assert result["asset_urn"] == "urn:li:image:img-456"
    assert result["flow"] == "image"


def test_linkedin_unsupported_audio_rejected(tmp_path):
    media = tmp_path / "voice.wav"
    media.write_bytes(b"RIFF audio")

    adapter = LinkedInPublisher(
        config=LinkedInConfig(author_urn="urn:li:person:me", access_token=SecretRef.from_value("tok")),
    )
    with pytest.raises(ValidationError) as exc:
        adapter.publish_job({"destination": "urn:li:person:me"}, str(media))
    assert "audio" in str(exc.value).lower()


def test_linkedin_target_validation_errors():
    adapter = LinkedInPublisher(
        config=LinkedInConfig(author_urn="", access_token=SecretRef.from_value("tok")),
    )
    # Missing author
    with pytest.raises(ValidationError):
        adapter.validate_target("")

    # Malformed author (not URN)
    with pytest.raises(ValidationError) as exc:
        adapter.validate_target("my-company-name")
    assert "Expected 'urn:li:" in str(exc.value)


# =====================================================================
# Task 4 Tests: YouTube Adapter
# =====================================================================

def test_youtube_resumable_and_chunked_upload(tmp_path):
    media = tmp_path / "video.mp4"
    # Write 2.5MB to test multi-chunk upload
    data = b"YOUTUBE" * (400 * 1024)
    media.write_bytes(data)
    total_size = len(data)

    transport = FakeHttpTransport()
    # 1. Resumable session init
    session_url = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=sess-abc"
    transport.register_response(
        "POST", r"/upload/youtube/v3/videos\?uploadType=resumable",
        status_code=200,
        headers={"Location": session_url},
    )
    # 2. Chunk upload handler:
    # First chunk (1MB) -> returns 308 with Range: bytes=0-1048575
    # Second chunk (1MB) -> returns 308 with Range: bytes=0-2097151
    # Final chunk -> returns 200 with video resource
    chunk_calls = []

    def chunk_handler(req):
        chunk_calls.append(req)
        content_range = req["headers"].get("Content-Range", "")
        if "2097152-" in content_range:
            # Final chunk
            return TransportResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                text=json.dumps({"id": "yt_video_12345", "snippet": {"title": "My Short"}}),
            )
        elif "1048576-" in content_range:
            return TransportResponse(
                status_code=308,
                headers={"Range": "bytes=0-2097151"},
            )
        else:
            return TransportResponse(
                status_code=308,
                headers={"Range": "bytes=0-1048575"},
            )

    transport.register_handler("PUT", r"upload_id=sess-abc", chunk_handler)

    # 3. Processing poll
    transport.register_response(
        "GET", r"/youtube/v3/videos\?id=yt_video_12345&part=status,processingDetails",
        status_code=200,
        json_body={"items": [{"id": "yt_video_12345", "status": {"uploadStatus": "processed"}}]},
    )
    # 4. Verify
    transport.register_response(
        "GET", r"/youtube/v3/videos\?id=yt_video_12345&part=status,snippet",
        status_code=200,
        json_body={
            "items": [{"id": "yt_video_12345", "snippet": {"title": "My Short"}}],
        },
    )

    adapter = YouTubePublisher(
        config=YouTubeConfig(
            channel_id="channel-123",
            access_token=SecretRef.from_value("yt-token"),
            chunk_size_bytes=1024 * 1024,
        ),
        transport=transport,
    )

    job = {
        "destination": "channel-123",
        "caption": "Check out this AI breakdown #ai #tech",
        "visibility": "unlisted",
    }
    result = adapter.publish_job(job, str(media))

    assert result["external_id"] == "yt_video_12345"
    assert result["url"] == "https://www.youtube.com/watch?v=yt_video_12345"
    assert result["verified"] is True
    assert len(chunk_calls) == 3


def test_youtube_resume_inquiry(tmp_path):
    transport = FakeHttpTransport()
    session_url = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=inquiry-test"
    transport.register_response(
        "PUT", r"upload_id=inquiry-test",
        status_code=308,
        headers={"Range": "bytes=0-524287"},
    )
    adapter = YouTubePublisher(
        config=YouTubeConfig(access_token=SecretRef.from_value("tok")),
        transport=transport,
    )
    offset = adapter.resume_inquiry(session_url, 1048576)
    assert offset == 524288


def test_youtube_processing_delay_and_rejection(tmp_path):
    media = tmp_path / "v.mp4"
    media.write_bytes(b"data")

    # Polling delay: 1st poll uploaded/processing, 2nd poll processed
    transport_delay = FakeHttpTransport()
    transport_delay.register_response("POST", r"/upload/youtube", headers={"Location": "https://upload/yt"})
    transport_delay.register_response("PUT", r"https://upload/yt", json_body={"id": "v-delay"})
    poll_counts = []

    def poll_handler(req):
        poll_counts.append(1)
        if len(poll_counts) == 1:
            return TransportResponse(
                status_code=200,
                json_body={"items": [{"status": {"uploadStatus": "uploaded"}, "processingDetails": {"processingStatus": "processing"}}]},
            )
        return TransportResponse(
            status_code=200,
            json_body={"items": [{"status": {"uploadStatus": "processed"}}]},
        )

    transport_delay.register_handler("GET", r"videos\?id=v-delay&part=status,processingDetails", poll_handler)
    transport_delay.register_response("GET", r"videos\?id=v-delay&part=status,snippet", json_body={"items": [{"id": "v-delay"}]})

    adapter = YouTubePublisher(
        config=YouTubeConfig(access_token=SecretRef.from_value("tok"), max_poll_attempts=5),
        transport=transport_delay,
    )
    res = adapter.publish_job({"destination": "me"}, str(media))
    assert res["external_id"] == "v-delay"
    assert len(poll_counts) == 2

    # Processing rejected
    transport_reject = FakeHttpTransport()
    transport_reject.register_response("POST", r"/upload/youtube", headers={"Location": "https://upload/yt"})
    transport_reject.register_response("PUT", r"https://upload/yt", json_body={"id": "v-rej"})
    transport_reject.register_response(
        "GET", r"videos\?id=v-rej&part=status,processingDetails",
        json_body={"items": [{"status": {"uploadStatus": "rejected", "rejectionReason": "copyright"}}]},
    )
    adapter_rej = YouTubePublisher(
        config=YouTubeConfig(access_token=SecretRef.from_value("tok")),
        transport=transport_reject,
    )
    with pytest.raises(ValidationError) as exc:
        adapter_rej.publish_job({"destination": "me"}, str(media))
    assert "copyright" in str(exc.value)


def test_youtube_rejects_non_video(tmp_path):
    image = tmp_path / "picture.jpg"
    image.write_bytes(b"JFIF")
    adapter = YouTubePublisher(
        config=YouTubeConfig(access_token=SecretRef.from_value("tok")),
    )
    with pytest.raises(ValidationError) as exc:
        adapter.publish_job({"destination": "me", "file_path": str(image)}, str(image))
    assert "video" in str(exc.value)


# =====================================================================
# Task 5 Tests: Discord Adapter
# =====================================================================

def test_discord_success_delivery(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"discord-clip")

    transport = FakeHttpTransport()
    # Channel validation
    transport.register_response(
        "GET", r"/channels/123456789$",
        status_code=200,
        json_body={"id": "123456789", "guild_id": "guild-001", "name": "media-drops"},
    )
    # Message delivery (multipart)
    transport.register_response(
        "POST", r"/channels/123456789/messages$",
        status_code=200,
        json_body={
            "id": "msg-999888",
            "channel_id": "123456789",
            "guild_id": "guild-001",
            "attachments": [
                {
                    "id": "att-1",
                    "filename": "clip.mp4",
                    "url": "https://cdn.discordapp.com/attachments/123456789/att-1/clip.mp4",
                }
            ],
            "content": "Check this update",
        },
    )
    # Message verify
    transport.register_response(
        "GET", r"/channels/123456789/messages/msg-999888$",
        status_code=200,
        json_body={"id": "msg-999888", "channel_id": "123456789"},
    )

    adapter = DiscordPublisher(
        config=DiscordConfig(
            channel_id="123456789",
            bot_token=SecretRef.from_value("bot-token-xyz"),
        ),
        transport=transport,
    )

    job = {
        "destination": "123456789",
        "caption": "Check this update",
    }
    result = adapter.publish_job(job, str(media))

    assert result["external_id"] == "msg-999888"
    assert result["url"] == "https://discord.com/channels/guild-001/123456789/msg-999888"
    assert result["verified"] is True
    # Ephemeral retention guarantee
    assert result["permanence"] == "ephemeral_channel_retention"
    assert "retention policy" in result["retention_note"]
    assert result["attachment_url"] == "https://cdn.discordapp.com/attachments/123456789/att-1/clip.mp4"


def test_discord_missing_channel_raises_validation_error():
    transport = FakeHttpTransport()
    transport.register_response(
        "GET", r"/channels/invalid-channel$",
        status_code=404,
        text="Unknown Channel",
    )
    adapter = DiscordPublisher(
        config=DiscordConfig(
            channel_id="invalid-channel",
            bot_token=SecretRef.from_value("bot-tok"),
        ),
        transport=transport,
    )
    with pytest.raises(ValidationError):
        adapter.validate_target("invalid-channel")


def test_discord_webhook_delivery(tmp_path):
    media = tmp_path / "img.png"
    media.write_bytes(b"png")

    webhook_url = "https://discord.com/api/webhooks/111/abc-token"
    transport = FakeHttpTransport()
    transport.register_response(
        "POST", r"/webhooks/111/abc-token$",
        status_code=200,
        json_body={"id": "hook-msg-1", "channel_id": "chan-webhook"},
    )

    adapter = DiscordPublisher(
        config=DiscordConfig(webhook_url=SecretRef.from_value(webhook_url)),
        transport=transport,
    )
    res = adapter.publish_job({"caption": "Webhook alert"}, str(media))
    assert res["external_id"] == "hook-msg-1"
    assert res["verified"] is True


# =====================================================================
# Task 6 Tests: Cross-Adapter Contract & Orchestration Integration
# =====================================================================

def test_all_adapters_satisfy_publisher_protocol():
    for pub in (
        InstagramPublisher(config=InstagramConfig()),
        LinkedInPublisher(config=LinkedInConfig()),
        YouTubePublisher(config=YouTubeConfig()),
        DiscordPublisher(config=DiscordConfig()),
        FakePublisher(),
    ):
        assert isinstance(pub, Publisher)
        for method in ("validate_target", "create_upload", "upload", "wait_until_ready", "publish", "verify", "rollback_or_cleanup", "publish_job"):
            assert hasattr(pub, method)


def test_orchestration_publishing_with_adapter_and_idempotency(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    reg = Registry(cfg.db_path)

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"RIFF integration")
    asset = ingest(cfg, reg, media, "test-proj")
    derivative = derive(cfg, reg, asset["asset_id"], "instagram_reel_v1")

    job = prepare(reg, derivative["derivative_id"], "instagram", "17841400000000000", caption="Hello world")
    payload = json.loads(job["external_json"])
    approve(reg, job["job_id"], "tester", job_fingerprint(payload))

    # Configure transport for Instagram
    transport = FakeHttpTransport()
    transport.register_response("POST", r"/media$", json_body={"id": "container_orch"})
    transport.register_response("POST", r"/container_orch", json_body={"success": True})
    transport.register_response("GET", r"/container_orch", json_body={"status_code": "FINISHED"})
    transport.register_response("POST", r"/media_publish$", json_body={"id": "media_orch_123"})
    transport.register_response("GET", r"/media_orch_123", json_body={"id": "media_orch_123", "permalink": "https://instagram.com/p/reel123"})

    ig_adapter = InstagramPublisher(
        config=InstagramConfig(account_id="17841400000000000", access_token=SecretRef.from_value("tok")),
        transport=transport,
    )

    first = publish(cfg, reg, job["job_id"], adapter=ig_adapter)
    assert first["external_id"] == "media_orch_123"
    assert first["verified"] == 1
    assert first["platform"] == "instagram"
    assert first["account"] == "17841400000000000"

    # Idempotent second publish returns receipt without re-executing
    second = publish(cfg, reg, job["job_id"], adapter=ig_adapter)
    assert second["receipt_id"] == first["receipt_id"]
    assert second["external_id"] == first["external_id"]


def test_orchestration_error_classification_and_unknown_reconcile(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    reg = Registry(cfg.db_path)

    f = tmp_path / "x.wav"
    f.write_bytes(b"RIFF audio")
    asset = ingest(cfg, reg, f, "p")
    derivative = derive(cfg, reg, asset["asset_id"], "audio_voiceover_v1")

    # Retryable failure
    job1 = prepare(reg, derivative["derivative_id"], "fake", "acct1", "test1")
    approve(reg, job1["job_id"], "op", job_fingerprint(json.loads(job1["external_json"])))
    fake_retry = FakePublisher(outcomes=["rate_limit"])
    with pytest.raises(Exception):
        publish(cfg, reg, job1["job_id"], adapter=fake_retry)
    assert reg.job(job1["job_id"])["state"] == "failed_retryable"

    # Permanent failure
    job2 = prepare(reg, derivative["derivative_id"], "fake", "acct2", "test2")
    approve(reg, job2["job_id"], "op", job_fingerprint(json.loads(job2["external_json"])))
    fake_perm = FakePublisher(outcomes=["auth"])
    with pytest.raises(Exception):
        publish(cfg, reg, job2["job_id"], adapter=fake_perm)
    assert reg.job(job2["job_id"])["state"] == "failed_permanent"

    # Unknown remote state
    job3 = prepare(reg, derivative["derivative_id"], "fake", "acct3", "test3")
    approve(reg, job3["job_id"], "op", job_fingerprint(json.loads(job3["external_json"])))
    fake_unk = FakePublisher(outcomes=["unknown"])
    with pytest.raises(Exception):
        publish(cfg, reg, job3["job_id"], adapter=fake_unk)
    assert reg.job(job3["job_id"])["state"] == "unknown_remote"


    reconciled = reconcile_job(reg, job3["job_id"], "retry", operator="admin", reason="verified safe")
    assert reconciled["state"] == "failed_retryable"


# =====================================================================
# Task 7 Tests: CLI Targets and Validation
# =====================================================================

def test_list_targets_and_validate_configs():
    targets = list_targets()
    platforms = {t["platform"] for t in targets}
    assert {"instagram", "linkedin", "youtube", "discord", "fake"} <= platforms

    # Dry-run validation
    val_fake = validate_target_config("fake")
    assert val_fake["status"] == "valid"

    val_ig = validate_target_config("instagram", "178414")
    assert "platform" in val_ig

    val_li = validate_target_config("linkedin", "urn:li:person:user")
    assert "platform" in val_li

    val_yt = validate_target_config("youtube", "channel")
    assert "platform" in val_yt

    val_dc = validate_target_config("discord", "channel")
    assert "platform" in val_dc
