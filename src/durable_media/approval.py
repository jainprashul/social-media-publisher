"""Human approval gates and cryptographic job fingerprinting.

Enforces explicit human approval policies before publishing. Approvals bind
cryptographically to the exact derivative media content hash (derivative_sha256)
and target publishing parameters (platform, destination, caption, visibility,
scheduled_at). Any alteration to the media or publication settings invalidates
the approval fingerprint, preventing time-of-check to time-of-use (TOCTOU) drift.
"""
import hashlib
import json
from typing import Any

# Canonical fields contributing to the cryptographic approval fingerprint.
APPROVAL_FIELDS = (
    'derivative_sha256',
    'platform',
    'destination',
    'caption',
    'visibility',
    'scheduled_at',
)


def fingerprint(d: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of canonical dictionary."""
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def canonical_approval_payload(d: dict[str, Any]) -> dict[str, Any]:
    """Filter dictionary to only the fields bound to the approval contract."""
    return {k: d.get(k) for k in APPROVAL_FIELDS}


def job_fingerprint(d: dict[str, Any]) -> str:
    """Generate cryptographic fingerprint for a publication job's approval fields."""
    return fingerprint(canonical_approval_payload(d))


def approval_payload(job: Any) -> dict[str, Any]:
    """Extract canonical approval payload from a job row's external_json."""
    raw = job['external_json']
    data = json.loads(raw) if isinstance(raw, str) else raw
    return canonical_approval_payload(data)


def approve(registry: Any, job_id: str, actor: str, fp: str) -> None:
    """Approve a publication job with fingerprint verification.

    Transition requires the job to be in 'awaiting_approval' state.
    Raises ValueError if the provided fingerprint does not match the current
    canonical job parameters (preventing stale approvals).
    """
    j = registry.job(job_id)
    if not j or j['state'] != 'awaiting_approval':
        raise ValueError('job not awaiting approval')
    payload = approval_payload(j)
    expected = job_fingerprint(payload)
    if fp != expected:
        raise ValueError('stale or invalid approval fingerprint')
    registry.approve(job_id, actor, fp, payload)
