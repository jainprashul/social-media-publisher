"""Project manifest generation and asset lineage tracking.

Combines master assets, derivatives, publishing states, and agent-to-agent (A2A)
provenance into an immutable, redacted audit record.
"""
import json
from typing import Any, Iterable
from .security import redact


def build(
    project: str,
    source: Any,
    assets: Iterable[Any],
    derivatives: Iterable[Any] = (),
    publishing: Iterable[Any] = (),
    a2a_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a complete project manifest with full redaction boundaries."""
    out = {
        'schema_version': 1,
        'project_id': project,
        'source': source,
        'assets': [dict(x) for x in assets],
        'derivatives': [dict(x) for x in derivatives],
        'publishing': [dict(x) for x in publishing],
    }
    if a2a_metadata:
        out['a2a'] = redact(a2a_metadata)
    return redact(out)


def dumps(manifest: dict[str, Any]) -> str:
    """Serialize a project manifest to deterministic, indented JSON with trailing newline."""
    return json.dumps(redact(manifest), sort_keys=True, indent=2) + "\n"


def lineage(registry: Any, asset_id: str) -> list[dict[str, Any]]:
    """Retrieve the master asset and all child derivatives derived from it."""
    a = registry.asset(asset_id)
    out = [dict(a)] if a else []
    for d in registry.conn.execute('SELECT * FROM derivatives WHERE asset_id=?', (asset_id,)):
        out.append(dict(d))
    return out
