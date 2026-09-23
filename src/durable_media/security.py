"""Security, secret redaction, and path sanitization boundaries.

Ensures that authentication tokens, webhook secrets, private keys, signed URLs,
and sensitive filesystem paths are never leaked into logs, manifests, receipts,
or error payloads.
"""
import json
import re
from typing import Any

# Pattern matching sensitive keys, headers, and parameter names.
SECRET = re.compile(
    r'(authorization|access[_-]?token|refresh[_-]?token|bot[_-]?token|'
    r'webhook[_-]?url|client[_-]?secret|cookie|password|private[_-]?key|'
    r'signed[_-]?url|oauth[_-]?code|api[_-]?key|credential|token|secret)',
    re.I,
)


def redact(value: Any) -> Any:
    """Recursively scrub sensitive information across nested structures and strings.

    Supports custom redaction hooks (__redact__, to_redacted_dict), dictionaries,
    sequences, Authorization Bearer headers, and URL query strings containing
    tokens or cloud storage signatures (AWS SigV4, GCS signatures).
    """
    if hasattr(value, '__redact__'):
        return value.__redact__()
    if hasattr(value, 'to_redacted_dict'):
        return redact(value.to_redacted_dict())
    if isinstance(value, dict):
        return {
            k: ('[REDACTED]' if SECRET.search(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, tuple):
        return tuple(redact(x) for x in value)
    if isinstance(value, str):
        # Redact Authorization: Bearer <token>
        if re.search(r'Bearer\s+\S+', value, re.I):
            return re.sub(r'Bearer\s+\S+', 'Bearer [REDACTED]', value, flags=re.I)
        # Redact signature/token query parameters in URLs (e.g. S3 presigned URLs, OAuth codes)
        if re.search(r'([?&](?:token|access_token|code|sig|signature|key|x-amz-signature|x-amz-credential|x-goog-signature|auth)[=])[^&#\s]+', value, re.I):
            return re.sub(
                r'([?&](?:token|access_token|code|sig|signature|key|x-amz-signature|x-amz-credential|x-goog-signature|auth)[=])[^&#\s]+',
                r'\1[REDACTED]',
                value,
                flags=re.I,
            )
    return value


# Standard sensitive file names and key extensions denied from ingestion/packaging
SECRET_PATH_NAMES = {'.env', 'id_rsa', 'id_dsa', 'id_ed25519', 'credentials', 'credentials.json', 'secret.key'}
SECRET_PATH_EXTS = {'.pem', '.key', '.pfx', '.p12'}


def is_secret_path(path: Any) -> bool:
    """Determine if a file path points to a known secret or credential location."""
    from pathlib import Path
    p = Path(path)
    if p.name.lower() in SECRET_PATH_NAMES:
        return True
    if p.suffix.lower() in SECRET_PATH_EXTS:
        return True
    for part in p.parts:
        if SECRET.search(part):
            return True
    return False


def canonical(value: Any) -> str:
    """Produce deterministic, sorted JSON with secrets redacted for hashing/audit."""
    return json.dumps(redact(value), sort_keys=True, separators=(',', ':'))
