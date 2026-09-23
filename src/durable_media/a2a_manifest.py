"""Agent-to-Agent (A2A) manifest schema and validation.

Defines the contract for multi-agent media handoffs, capturing task IDs, agent
provenance, model parameters, prompt hashes, and parent-child asset relationships
while enforcing strict workspace path containment and rejecting secret files.
"""
import json
import os
from pathlib import Path
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .security import is_secret_path, redact

# Whitelist of permissible file extensions for ingested A2A media and sidecars
SUPPORTED_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.webp', '.gif',
    '.mp4', '.webm', '.mov', '.m4v',
    '.mp3', '.wav', '.m4a', '.aac', '.ogg',
    '.srt', '.vtt', '.txt', '.json'
}


def validate_prompt_hash(h: str | None) -> str | None:
    """Validate that prompt_hash is a canonical 64-character hex SHA-256 or None."""
    if h is None:
        return None
    if not isinstance(h, str):
        raise ValueError('prompt_hash must be a string or None')
    val = h.strip()
    if not val:
        return None
    if not re.fullmatch(r'^[a-fA-F0-9]{64}$', val):
        raise ValueError('malformed prompt_hash: must be 64-character hex sha256')
    return val.lower()


def _check_safe_relative_path(path_str: str, label: str = 'path') -> str:
    """Validate path syntax, disallowing traversal ('..'), secrets, and unsupported extensions."""
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError(f'{label} is required and must be non-empty')
    cleaned = path_str.strip()
    p = Path(cleaned)
    parts = p.parts
    if any(part == '..' for part in parts):
        raise ValueError(f'workspace escape detected in {label}: {cleaned}')
    if is_secret_path(cleaned):
        raise ValueError(f'secret-bearing path rejected in {label}: {cleaned}')
    ext = p.suffix.lower()
    if ext and ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f'unsupported file extension {ext} in {label}: {cleaned}')
    return cleaned


@dataclass
class A2AArtifact:
    """Declaration of an individual media asset produced during an agent task."""

    path: str
    kind: str | None = None
    role: str = 'master'
    parent_path: str | None = None
    caption_path: str | None = None
    thumbnail_path: str | None = None
    model: str | None = None
    tool: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.path = _check_safe_relative_path(self.path, 'artifact path')
        if self.parent_path:
            self.parent_path = _check_safe_relative_path(self.parent_path, 'parent_path')
        if self.caption_path:
            self.caption_path = _check_safe_relative_path(self.caption_path, 'caption_path')
        if self.thumbnail_path:
            self.thumbnail_path = _check_safe_relative_path(self.thumbnail_path, 'thumbnail_path')
        if not self.kind and Path(self.path).suffix:
            ext = Path(self.path).suffix.lower()
            if ext in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
                self.kind = 'image'
            elif ext in {'.mp4', '.webm', '.mov', '.m4v'}:
                self.kind = 'video'
            elif ext in {'.mp3', '.wav', '.m4a', '.aac', '.ogg'}:
                self.kind = 'audio'
            elif ext in {'.srt', '.vtt', '.txt'}:
                self.kind = 'caption'

    def to_dict(self) -> dict[str, Any]:
        """Convert artifact metadata to dictionary with proactive redaction."""
        return redact(asdict(self))


@dataclass
class A2AManifest:
    """Structured envelope encapsulating agent-generated assets and lineage."""

    task_id: str
    agent: str
    project: str | None = None
    prompt_hash: str | None = None
    artifacts: list[A2AArtifact] = field(default_factory=list)
    context_id: str | None = None
    model: str | None = None
    tool: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError('task_id is required and cannot be empty')
        self.task_id = self.task_id.strip()

        if not isinstance(self.agent, str) or not self.agent.strip():
            raise ValueError('agent is required and cannot be empty')
        self.agent = self.agent.strip()

        self.prompt_hash = validate_prompt_hash(self.prompt_hash)

        if not self.artifacts:
            raise ValueError('artifacts must contain at least one artifact')

        # Check duplicate paths
        seen = set()
        for art in self.artifacts:
            norm = os.path.normpath(art.path)
            if norm in seen:
                raise ValueError(f'duplicate artifact path: {art.path}')
            seen.add(norm)

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to dictionary with credentials and secrets redacted."""
        d = {
            'task_id': self.task_id,
            'agent': self.agent,
            'project': self.project,
            'prompt_hash': self.prompt_hash,
            'context_id': self.context_id,
            'model': self.model,
            'tool': self.tool,
            'artifacts': [a.to_dict() for a in self.artifacts],
            'metadata': self.metadata,
            'created_at': self.created_at,
        }
        return redact(d)

    def to_json(self) -> str:
        """Serialize manifest to deterministic formatted JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'A2AManifest':
        """Parse and validate manifest from dictionary data."""
        if not isinstance(data, dict):
            raise ValueError('manifest data must be a dictionary')
        task_id = data.get('task_id')
        agent = data.get('agent') or data.get('source_agent')
        project = data.get('project')
        prompt_hash = data.get('prompt_hash')
        context_id = data.get('context_id')
        model = data.get('model')
        tool = data.get('tool')
        created_at = data.get('created_at')
        metadata = data.get('metadata') or {}

        raw_artifacts = data.get('artifacts', [])
        if not isinstance(raw_artifacts, list):
            raise ValueError('artifacts must be a list')

        artifacts = []
        for a in raw_artifacts:
            if isinstance(a, A2AArtifact):
                artifacts.append(a)
            elif isinstance(a, dict):
                artifacts.append(A2AArtifact(
                    path=a.get('path', ''),
                    kind=a.get('kind'),
                    role=a.get('role', 'master'),
                    parent_path=a.get('parent_path'),
                    caption_path=a.get('caption_path'),
                    thumbnail_path=a.get('thumbnail_path'),
                    model=a.get('model'),
                    tool=a.get('tool'),
                    metadata=a.get('metadata') or {},
                ))
            else:
                raise ValueError(f'invalid artifact item: {a}')

        return cls(
            task_id=task_id,
            agent=agent,
            project=project,
            prompt_hash=prompt_hash,
            artifacts=artifacts,
            context_id=context_id,
            model=model,
            tool=tool,
            metadata=metadata,
            created_at=created_at,
        )

    @classmethod
    def loads(cls, content: str) -> 'A2AManifest':
        """Parse and validate manifest from JSON string."""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f'invalid JSON manifest: {exc}') from exc
        return cls.from_dict(parsed)

    @classmethod
    def load(cls, path: str | Path) -> 'A2AManifest':
        """Load and validate manifest from filesystem path."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise ValueError(f'manifest file not found: {path}')
        return cls.loads(p.read_text(encoding='utf-8'))
