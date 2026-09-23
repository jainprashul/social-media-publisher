"""Base publishing adapter interfaces, error taxonomy, and lifecycle protocol.

Defines the canonical 7-step publishing lifecycle:
1. validate_target: preflight validation of target account/channel/URN.
2. create_upload: initialization of media container or resumable session.
3. upload: streaming/chunked transmission of media bytes.
4. wait_until_ready: polling remote transcoding/processing completion.
5. publish: final post publication or container delivery.
6. verify: independent read-back of published permalink and external ID.
7. rollback_or_cleanup: best-effort cleanup of partial remote state on error.

Also defines the standardized error hierarchy with error_class attributes used
by the retry classifier and scheduler.
"""
from dataclasses import dataclass
import os
from typing import Any, Protocol, runtime_checkable


class AdapterError(RuntimeError):
    """Base error for all provider adapter operations."""
    error_class = 'adapter_error'


class TimeoutError(AdapterError):
    """Network connection, read, or polling timeout."""
    error_class = 'timeout'


class RateLimitError(AdapterError):
    """Provider rate limit or quota exceeded (HTTP 429)."""
    error_class = 'rate_limit'


class ServerError(AdapterError):
    """Provider internal server error (HTTP 5xx)."""
    error_class = '5xx'


class AuthenticationError(AdapterError):
    """Invalid, expired, or missing credentials/tokens (HTTP 401/403)."""
    error_class = 'authentication'


class ValidationError(AdapterError):
    """Malformed request payload, invalid destination, or rejected media format."""
    error_class = 'validation'


class MalformedResponseError(AdapterError):
    """Provider response missing expected fields or returned unparseable payload."""
    error_class = 'malformed_response'


class UnknownRemoteStateError(AdapterError):
    """Network dropped mid-flight; remote publication status is ambiguous."""
    error_class = 'unknown_remote'


class LiveChecksDisabledError(AdapterError):
    """Raised when live network checks are attempted without opt-in flag."""
    error_class = 'live_checks_disabled'


def is_live_allowed() -> bool:
    """Check whether external live network calls are enabled via environment variable.

    Guards against accidental live calls or token consumption during dry-runs/tests.
    Requires DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1 (or 'true', 'yes').
    """
    val = os.environ.get("DURABLE_MEDIA_ALLOW_LIVE_CHECKS", "").strip().lower()
    return val in ("1", "true", "yes")


@dataclass(frozen=True)
class AdapterResult:
    """Immutable result from a verified adapter publication."""

    external_id: str
    url: str
    verified: bool = True
    payload: dict | None = None

    def __getitem__(self, key: str) -> Any:
        if key == 'external_id':
            return self.external_id
        if key == 'url':
            return self.url
        if key == 'verified':
            return self.verified
        if key == 'payload':
            return self.payload
        if self.payload and key in self.payload:
            return self.payload[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> dict[str, Any]:
        """Convert result to flat dictionary for receipt serialization."""
        base = {'external_id': self.external_id, 'url': self.url, 'verified': self.verified}
        if self.payload:
            base.update(self.payload)
        return base

    def __redact__(self) -> dict[str, Any]:
        """Security boundary: redact any tokens or secrets present in response payload."""
        from ..security import redact
        return {'external_id': self.external_id, 'url': self.url, 'verified': self.verified, 'payload': redact(self.payload)}


@runtime_checkable
class Publisher(Protocol):
    """Structural protocol defining the publisher interface."""

    def validate_target(self, target: Any) -> Any: ...
    def create_upload(self, job: dict[str, Any]) -> Any: ...
    def upload(self, remote: Any, file_path: str) -> Any: ...
    def wait_until_ready(self, remote: Any) -> Any: ...
    def publish(self, remote: Any, job: dict[str, Any]) -> Any: ...
    def verify(self, result: Any) -> Any: ...
    def rollback_or_cleanup(self, remote: Any) -> None: ...
    def publish_job(self, job: dict[str, Any], file_path: str) -> dict[str, Any]: ...
    def check_readiness(self, live: bool = False, timeout: float = 5.0) -> dict[str, Any]: ...


class BasePublisher:
    """Base publisher implementing the 7-step publishing lifecycle template."""

    def validate_target(self, target):
        raise NotImplementedError

    def create_upload(self, job):
        raise NotImplementedError

    def upload(self, remote, file_path):
        raise NotImplementedError

    def wait_until_ready(self, remote):
        raise NotImplementedError

    def publish(self, remote, job):
        raise NotImplementedError

    def verify(self, result):
        raise NotImplementedError

    def rollback_or_cleanup(self, remote):
        pass

    def publish_job(self, job: dict, file_path: str) -> dict:
        self.validate_target(job.get('destination'))
        job_with_file = dict(job)
        if file_path and not job_with_file.get('file_path'):
            job_with_file['file_path'] = file_path
        remote = self.create_upload(job_with_file)
        try:
            uploaded = self.upload(remote, file_path)
            ready = self.wait_until_ready(uploaded)
            published = self.publish(ready, job_with_file)
            verified = self.verify(published)
            if isinstance(verified, AdapterResult):
                return verified.to_dict()
            if isinstance(verified, dict):
                return verified
            raise MalformedResponseError('verification returned malformed result')
        except Exception:
            try:
                self.rollback_or_cleanup(remote)
            except Exception:
                pass
            raise

    def check_readiness(self, live: bool = False, timeout: float = 5.0) -> dict:
        """Perform dry-run or opt-in live readiness check."""
        if not live:
            return self._check_readiness_dry_run()
        if not is_live_allowed():
            return {
                "status": "gated",
                "live": True,
                "error_class": "LiveChecksDisabledError",
                "message": "Live checks require DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1 environment variable",
            }
        return self._check_readiness_live(timeout=timeout)

    def _check_readiness_dry_run(self) -> dict:
        try:
            target = getattr(self.config, "account_id", None) or getattr(self.config, "author_urn", None) or getattr(self.config, "channel_id", None) or ""
            self.validate_target(target)
            return {"status": "ready", "live": False, "error_class": None}
        except Exception as e:
            return {"status": "unconfigured", "live": False, "error_class": e.__class__.__name__}

    def _check_readiness_live(self, timeout: float = 5.0) -> dict:
        return {"status": "ready", "live": True, "error_class": None}
