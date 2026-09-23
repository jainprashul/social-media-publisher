"""Publish receipt validation and external verification proofs.

A render is not considered published until the target platform returns a durable
external identifier and URL, recorded locally as an immutable, verified receipt.
"""
from typing import Any
from .security import redact


def redact_payload(value: Any) -> Any:
    """Scrub sensitive credentials from a receipt response payload."""
    return redact(value)


def verified_receipt(registry: Any, idempotency_key: str) -> dict[str, Any] | None:
    """Retrieve an existing verified receipt matching the idempotency key.

    Returns None if the receipt is absent, unverified, or lacks external identity.
    """
    row = registry.receipt(idempotency_key)
    if not row or not row['verified'] or not row['external_id'] or not row['url']:
        return None
    return dict(row)


def receipt_proves_external_response(row: Any) -> bool:
    """Verify that a receipt record contains complete proof of external publication.

    Requires positive verification status, platform external ID, published URL,
    and persisted response payload.
    """
    return bool(row and row['verified'] and row['external_id'] and row['url'] and row['payload_json'])
