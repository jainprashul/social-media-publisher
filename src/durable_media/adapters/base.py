from dataclasses import dataclass
from typing import Protocol, runtime_checkable

class AdapterError(RuntimeError):
    error_class = 'adapter_error'
class TimeoutError(AdapterError): error_class='timeout'
class RateLimitError(AdapterError): error_class='rate_limit'
class ServerError(AdapterError): error_class='5xx'
class AuthenticationError(AdapterError): error_class='authentication'
class ValidationError(AdapterError): error_class='validation'
class MalformedResponseError(AdapterError): error_class='malformed_response'
class UnknownRemoteStateError(AdapterError): error_class='unknown_remote'
class LiveChecksDisabledError(AdapterError): error_class='live_checks_disabled'

def is_live_allowed() -> bool:
    import os
    val = os.environ.get("DURABLE_MEDIA_ALLOW_LIVE_CHECKS", "").strip().lower()
    return val in ("1", "true", "yes")


@dataclass(frozen=True)
class AdapterResult:
    external_id: str
    url: str
    verified: bool = True
    payload: dict | None = None

    def __getitem__(self, key: str):
        if key == 'external_id': return self.external_id
        if key == 'url': return self.url
        if key == 'verified': return self.verified
        if key == 'payload': return self.payload
        if self.payload and key in self.payload: return self.payload[key]
        raise KeyError(key)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> dict:
        base = {'external_id': self.external_id, 'url': self.url, 'verified': self.verified}
        if self.payload:
            base.update(self.payload)
        return base

    def __redact__(self):
        from ..security import redact
        return {'external_id': self.external_id, 'url': self.url, 'verified': self.verified, 'payload': redact(self.payload)}

@runtime_checkable
class Publisher(Protocol):
    def validate_target(self, target): ...
    def create_upload(self, job): ...
    def upload(self, remote, file_path): ...
    def wait_until_ready(self, remote): ...
    def publish(self, remote, job): ...
    def verify(self, result): ...
    def rollback_or_cleanup(self, remote): ...
    def publish_job(self, job, file_path): ...

class BasePublisher:
    """Base publisher implementing the 7-step publishing lifecycle."""
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
