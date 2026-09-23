"""Deterministic offline publishing adapter for local development and testing.

Simulates the complete 7-step adapter lifecycle, records invocation traces,
and injects deterministic error outcomes (timeouts, rate limits, 5xx, auth errors,
malformed responses, unknown remote states) or sequenced failure-then-recovery scenarios.
"""
from typing import Any

from .base import (
    AdapterResult,
    AuthenticationError,
    MalformedResponseError,
    RateLimitError,
    ServerError,
    TimeoutError,
    UnknownRemoteStateError,
    ValidationError,
)

_ERRORS = {
    'timeout': TimeoutError,
    'rate_limit': RateLimitError,
    '5xx': ServerError,
    'server_error': ServerError,
    'auth': AuthenticationError,
    'authentication': AuthenticationError,
    'validation': ValidationError,
    'malformed': MalformedResponseError,
    'unknown': UnknownRemoteStateError,
    'unknown_remote': UnknownRemoteStateError,
}


class FakePublisher:
    """Offline adapter simulating platform publishing with controllable outcomes."""

    def __init__(self, mode: str = 'success', outcomes: list[Any] | None = None):
        self.mode = mode
        self.outcomes = list(outcomes) if outcomes is not None else None
        self.uploads = 0
        self.calls: list[str] = []

    def _mode(self) -> Any:
        return self.outcomes.pop(0) if self.outcomes else self.mode

    def validate_target(self, target: Any) -> bool:
        self.calls.append('validate_target')
        return True

    def publish_job(self, job: dict[str, Any], path: str) -> dict[str, Any]:
        """Execute mock publish job according to configured outcome mode."""
        self.uploads += 1
        self.calls.append('publish_job')
        mode = self._mode()
        if mode in _ERRORS:
            raise _ERRORS[mode](mode)
        if mode == 'success':
            return {
                'external_id': 'fake-' + job['job_id'],
                'url': 'https://fake.invalid/' + job['job_id'],
                'verified': True,
                'platform': job.get('platform'),
                'account': job.get('destination'),
            }
        if isinstance(mode, dict):
            return mode
        return {
            'external_id': 'fake-' + job['job_id'],
            'url': 'https://fake.invalid/' + job['job_id'],
            'verified': True,
        }

    def create_upload(self, job: dict[str, Any]) -> dict[str, Any]:
        self.calls.append('create_upload')
        return {'remote_id': 'remote-' + job['job_id']}

    def upload(self, remote: Any, file_path: str) -> Any:
        self.calls.append('upload')
        return remote

    def wait_until_ready(self, remote: Any) -> Any:
        self.calls.append('wait_until_ready')
        return remote

    def publish(self, remote: Any, job: dict[str, Any]) -> Any:
        self.calls.append('publish')
        return self.publish_job(job, '')

    def verify(self, result: dict[str, Any]) -> bool:
        self.calls.append('verify')
        return bool(result.get('verified'))

    def rollback_or_cleanup(self, remote: Any) -> None:
        self.calls.append('rollback_or_cleanup')

    def check_readiness(self, live: bool = False, timeout: float = 5.0) -> dict[str, Any]:
        """Simulate readiness check, enforcing live gating if live=True."""
        self.calls.append('check_readiness')
        if not live:
            return {'status': 'ready', 'live': False, 'error_class': None}
        from .base import is_live_allowed
        if not is_live_allowed():
            return {
                'status': 'gated',
                'live': True,
                'error_class': 'LiveChecksDisabledError',
                'message': 'Live checks require DURABLE_MEDIA_ALLOW_LIVE_CHECKS=1 environment variable',
            }
        mode = self._mode()
        if mode in _ERRORS:
            return {'status': 'error', 'live': True, 'error_class': _ERRORS[mode].__name__}
        return {'status': 'ok', 'live': True, 'error_class': None}
