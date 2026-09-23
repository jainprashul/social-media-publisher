from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig
from .obsidian_links import AI_OWNED_MARKER
from .registry import Registry
from .reports import generate_nightly_report, report_to_json, report_to_markdown
from .security import redact


class SchedulerError(Exception):
    """Base error for scheduled operations."""
    pass


class SchedulerLockError(SchedulerError):
    """Raised when an overlapping scheduler job is already running."""
    pass


class FileLock:
    """Advisory file lock using fcntl.flock to prevent overlapping scheduled runs."""

    def __init__(self, lock_path: Path | str, timeout: float = 0.0):
        self.lock_path = Path(lock_path).resolve()
        self.timeout = timeout
        self._fd: int | None = None
        self._locked = False

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Acquired successfully
                self._fd = fd
                self._locked = True
                os.ftruncate(fd, 0)
                os.write(fd, f"pid={os.getpid()}\nacquired_at={datetime.now(timezone.utc).isoformat()}\n".encode("utf-8"))
                return True
            except (BlockingIOError, OSError):
                if fd is not None:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                if time.monotonic() - start_time >= self.timeout:
                    raise SchedulerLockError(
                        f"Could not acquire lock on '{self.lock_path}'; another process is currently running."
                    )
                time.sleep(0.05)

    def release(self) -> None:
        if self._locked and self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
            self._locked = False

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def clean_old_reports(directory: Path, retention_days: int = 30) -> list[str]:
    """Safely purge AI-owned operational reports older than retention_days."""
    if not directory.exists() or not directory.is_dir() or retention_days <= 0:
        return []

    cutoff = time.time() - (retention_days * 86400)
    purged = []

    for item in directory.glob("*"):
        if not item.is_file():
            continue
        try:
            mtime = item.stat().st_mtime
            if mtime < cutoff:
                # Protection check: only delete markdown if AI-owned
                if item.suffix == ".md":
                    first_chunk = item.read_text(encoding="utf-8", errors="ignore")[:256]
                    if AI_OWNED_MARKER not in first_chunk:
                        continue
                elif item.suffix == ".json":
                    try:
                        data = json.loads(item.read_text(encoding="utf-8"))
                        if not isinstance(data, dict) or "report_type" not in data:
                            continue
                    except Exception:
                        continue
                item.unlink(missing_ok=True)
                purged.append(str(item))
        except Exception:
            pass

    return purged


def run_nightly_report(
    cfg: WorkspaceConfig,
    registry: Registry,
    output_path: Path | str | None = None,
    since: str | None = None,
    output_format: str = "json",
    lock_path: Path | str | None = None,
    lock_timeout: float = 0.0,
    retention_days: int = 30,
) -> tuple[dict[str, Any], Path | None]:
    """Execute nightly report with concurrency locking, atomic write, and human-note protection."""
    lp = Path(lock_path).resolve() if lock_path else (cfg.root / "data" / "locks" / "nightly_report.lock").resolve()

    with FileLock(lp, timeout=lock_timeout):
        # Human-note protection check before generating report
        out_p: Path | None = None
        if output_path:
            out_p = Path(output_path).resolve()
            if out_p.exists():
                first_chunk = out_p.read_text(encoding="utf-8", errors="ignore")[:256]
                if output_format == "markdown" and AI_OWNED_MARKER not in first_chunk:
                    raise PermissionError(
                        f"Target file {out_p} exists and is not AI-owned. Refusing to overwrite human note."
                    )

        # Generate report data
        rep = generate_nightly_report(cfg, registry, since=since)
        content = report_to_markdown(rep) if output_format == "markdown" else report_to_json(rep)

        if out_p:
            out_p.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write via temporary file in same directory
            tmp_p = out_p.with_name(f".{out_p.name}.tmp.{os.getpid()}")
            try:
                tmp_p.write_text(content, encoding="utf-8")
                os.replace(tmp_p, out_p)
            except Exception:
                if tmp_p.exists():
                    tmp_p.unlink(missing_ok=True)
                raise

            # Retention-safe cleanup in the destination directory
            clean_old_reports(out_p.parent, retention_days=retention_days)
            return rep, out_p

        return rep, None
