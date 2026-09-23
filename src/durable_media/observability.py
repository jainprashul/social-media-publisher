from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .security import is_secret_path, redact


class JsonLogger:
    """Configurable, size-bounded JSONL logger with proactive secret redaction and rotation."""

    def __init__(
        self,
        path: Path | str,
        max_bytes: int = 1_000_000,
        backup_count: int = 5,
    ):
        self.path = Path(path).resolve()
        self.max_bytes = max(100, int(max_bytes))
        self.backup_count = max(0, int(backup_count))

    def rotate(self) -> None:
        """Rotate log files within bounded backup_count limits."""
        if self.backup_count <= 0:
            if self.path.exists():
                self.path.unlink(missing_ok=True)
            return

        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        if oldest.exists():
            oldest.unlink(missing_ok=True)

        for i in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            dst = self.path.with_name(f"{self.path.name}.{i + 1}")
            if src.exists():
                os.replace(src, dst)

        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def event(self, **fields: Any) -> dict[str, Any]:
        """Record a structured event, ensuring redaction prior to write and rotation boundaries."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fields["at"] = datetime.now(timezone.utc).isoformat()

        # Strict redaction happens BEFORE writing or sizing
        safe_fields = redact(fields)
        serialized = json.dumps(safe_fields, sort_keys=True) + "\n"
        encoded = serialized.encode("utf-8")

        # Check rotation boundary
        if self.path.exists() and (self.path.stat().st_size + len(encoded) > self.max_bytes):
            self.rotate()

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(serialized)

        return safe_fields


def inspect_logs(
    log_path: Path | str,
    limit: int = 50,
    since: str | None = None,
) -> dict[str, Any]:
    """Inspect structured operational log files, returning bounded, redacted entries."""
    lp = Path(log_path).resolve()
    if not lp.exists():
        return {
            "log_file": str(lp),
            "exists": False,
            "size_bytes": 0,
            "rotated_files": [],
            "total_events": 0,
            "recent_events": [],
        }

    # Find existing rotated backups
    parent = lp.parent
    rotated = []
    i = 1
    while True:
        r_file = parent / f"{lp.name}.{i}"
        if r_file.exists():
            rotated.append(str(r_file))
            i += 1
        else:
            break

    # Read events from current log file
    events = []
    with open(lp, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    if since and item.get("at") and str(item["at"]) < since:
                        continue
                    events.append(redact(item))
            except Exception:
                pass

    total_events = len(events)
    recent = events[-limit:] if limit > 0 else events

    return {
        "log_file": str(lp),
        "exists": True,
        "size_bytes": lp.stat().st_size,
        "rotated_files": rotated,
        "total_events": total_events,
        "recent_events": recent,
    }


def scan_package(root: Path | str) -> list[str]:
    """Scan workspace or package tree for forbidden secret-bearing files or sensitive artifacts."""
    root_path = Path(root).resolve()
    bad: list[str] = []

    # Directories to ignore during scanning
    ignored_parts = {".git", ".pytest_cache", "__pycache__", ".venv", "venv", "node_modules"}

    for p in root_path.rglob("*"):
        if not p.is_file():
            continue
        if any(part in ignored_parts for part in p.parts):
            continue

        name_lower = p.name.lower()
        suffix_lower = p.suffix.lower()

        # Check secret paths
        if is_secret_path(p):
            bad.append(str(p))
        elif name_lower.startswith(".env") or name_lower in ("id_rsa", "id_dsa", "id_ed25519", "credentials.json"):
            bad.append(str(p))
        elif suffix_lower in (".key", ".pem", ".pfx", ".p12"):
            bad.append(str(p))

    return sorted(bad)
