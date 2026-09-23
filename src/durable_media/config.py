"""Workspace configuration and filesystem isolation boundary.

Manages the directory taxonomy for durable media storage (raw assets, derived media,
previews, receipts, logs, manifests, and projects) and enforces path containment
to prevent directory traversal outside the configured root workspace.
"""
from pathlib import Path


class WorkspaceConfig:
    """Manages workspace paths, database location, and containment validation."""

    def __init__(self, root: Path | str):
        # Resolve user home and symlinks to establish a canonical root boundary.
        self.root = Path(root).expanduser().resolve()

    @property
    def db_path(self) -> Path:
        """Primary SQLite database tracking assets, derivatives, jobs, and receipts."""
        return self.root / 'data' / 'registry.sqlite3'

    def ensure(self) -> 'WorkspaceConfig':
        """Initialize standard workspace directories if they do not exist."""
        self.root.mkdir(parents=True, exist_ok=True)
        for p in ('data/assets', 'data/derivatives', 'data/previews', 'data/receipts', 'data/logs', 'manifests', 'projects'):
            (self.root / p).mkdir(parents=True, exist_ok=True)
        return self

    def safe(self, path: Path | str) -> Path:
        """Enforce path containment within the workspace root.

        Raises ValueError if the resolved path escapes the workspace root,
        preventing directory traversal attacks or accidental operations outside
        the designated project workspace.
        """
        p = Path(path).expanduser().resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError('path escapes workspace')
        return p
