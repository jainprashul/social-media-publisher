from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from .config import WorkspaceConfig

REQUIRED_TABLES = frozenset({
    "assets",
    "derivatives",
    "artifacts",
    "jobs",
    "approvals",
    "receipts",
    "transitions",
})


class BackupError(Exception):
    """Base error for database backup and restore operations."""
    pass


class BackupVerificationError(BackupError):
    """Raised when backup verification (checksum, integrity, schema) fails."""
    pass


class SafeOverwriteError(BackupError):
    """Raised when attempting to overwrite an existing non-empty target without --force."""
    pass


def sha256_file(path: Path | str) -> str:
    """Compute SHA-256 hex digest of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_sidecar_checksum_path(backup_path: Path) -> Path:
    """Standard sidecar path for a backup file."""
    return backup_path.with_name(backup_path.name + ".sha256")


def find_checksum_sidecar(backup_path: Path) -> Path | None:
    """Locate existing checksum sidecar under common naming conventions."""
    candidates = [
        backup_path.with_name(backup_path.name + ".sha256"),
        backup_path.with_suffix(backup_path.suffix + ".sha256"),
        backup_path.with_suffix(".sha256"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def create_backup(
    cfg: WorkspaceConfig,
    db_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create a consistent SQLite snapshot backup and SHA-256 sidecar."""
    src_p = Path(db_path).resolve() if db_path else cfg.db_path.resolve()
    if not src_p.exists():
        raise BackupError(f"Source database file does not exist: {src_p}")

    if output_path:
        out_p = Path(output_path).resolve()
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_p = (cfg.root / "data" / "backups" / f"registry_backup_{ts}.sqlite3").resolve()

    out_p.parent.mkdir(parents=True, exist_ok=True)

    # Use SQLite online backup API for snapshot consistency
    src_conn = sqlite3.connect(src_p)
    dst_conn = sqlite3.connect(out_p)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    # Immediate integrity check
    check_conn = sqlite3.connect(out_p)
    try:
        check_conn.row_factory = sqlite3.Row
        res = check_conn.execute("PRAGMA integrity_check").fetchall()
        if not res or res[0][0] != "ok":
            raise BackupVerificationError(f"Integrity check failed for newly created backup: {res}")

        # Gather table list and row counts
        tables_res = check_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = [r["name"] for r in tables_res]
        counts = {}
        for t in tables:
            counts[t] = check_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    finally:
        check_conn.close()

    # Calculate checksum and generate sidecar file
    digest = sha256_file(out_p)
    sidecar_p = get_sidecar_checksum_path(out_p)
    sidecar_p.write_text(f"{digest}  {out_p.name}\n", encoding="utf-8")

    return {
        "status": "created",
        "backup_path": str(out_p),
        "checksum_path": str(sidecar_p),
        "sha256": digest,
        "bytes": out_p.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": sorted(tables),
        "counts": counts,
    }


def verify_backup(
    backup_path: Path | str,
    checksum_path: Path | str | None = None,
) -> dict[str, Any]:
    """Verify backup existence, checksum match, SQLite integrity, and schema compatibility."""
    bp = Path(backup_path).resolve()
    if not bp.exists():
        raise BackupVerificationError(f"Backup file not found: {bp}")

    # Checksum sidecar resolution
    if checksum_path:
        cp = Path(checksum_path).resolve()
        if not cp.exists():
            raise BackupVerificationError(f"Specified checksum sidecar not found: {cp}")
    else:
        cp = find_checksum_sidecar(bp)
        if not cp or not cp.exists():
            raise BackupVerificationError(f"Missing checksum sidecar for backup '{bp.name}'")

    # Read and parse sidecar
    sidecar_text = cp.read_text(encoding="utf-8").strip()
    if not sidecar_text:
        raise BackupVerificationError(f"Checksum sidecar is empty: {cp}")
    expected_sha = sidecar_text.split()[0].lower()

    # Verify checksum
    actual_sha = sha256_file(bp).lower()
    if actual_sha != expected_sha:
        raise BackupVerificationError(
            f"Checksum verification failed for {bp.name}: expected {expected_sha}, got {actual_sha}"
        )

    # Verify SQLite file integrity
    try:
        conn = sqlite3.connect(bp)
        conn.row_factory = sqlite3.Row
        int_res = conn.execute("PRAGMA integrity_check").fetchall()
        if not int_res or int_res[0][0] != "ok":
            raise BackupVerificationError(f"SQLite integrity check failed: {int_res}")

        quick_res = conn.execute("PRAGMA quick_check").fetchall()
        if not quick_res or quick_res[0][0] != "ok":
            raise BackupVerificationError(f"SQLite quick check failed: {quick_res}")

        # Verify schema compatibility
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {r["name"] for r in table_rows}
        missing_tables = REQUIRED_TABLES - tables
        if missing_tables:
            raise BackupVerificationError(
                f"Incompatible database schema: missing required tables {sorted(list(missing_tables))}"
            )

        counts = {}
        for t in tables:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

    except sqlite3.DatabaseError as err:
        raise BackupVerificationError(f"Corrupt or non-SQLite database file: {err}") from err
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "status": "valid",
        "backup_path": str(bp),
        "checksum_path": str(cp),
        "sha256": actual_sha,
        "integrity_check": "ok",
        "tables": sorted(list(tables)),
        "counts": counts,
    }


def restore_backup(
    backup_path: Path | str,
    target_db: Path | str,
    force: bool = False,
    checksum_path: Path | str | None = None,
) -> dict[str, Any]:
    """Safely restore a verified backup to the target database."""
    bp = Path(backup_path).resolve()
    tp = Path(target_db).resolve()

    # Step 1: Verify the backup before touching the target destination
    ver = verify_backup(bp, checksum_path=checksum_path)

    # Step 2: Safe overwrite rules
    was_existing = tp.exists() and tp.stat().st_size > 0
    if was_existing and not force:
        raise SafeOverwriteError(
            f"Target database '{tp}' exists and is non-empty ({tp.stat().st_size} bytes). "
            f"Refusing to overwrite without --force."
        )

    # Step 3: Atomic restore via temp file
    tp.parent.mkdir(parents=True, exist_ok=True)
    tmp_restore = tp.with_name(f".{tp.name}.restore_tmp_{os.getpid()}")

    try:
        src_conn = sqlite3.connect(bp)
        tmp_conn = sqlite3.connect(tmp_restore)
        try:
            src_conn.backup(tmp_conn)
        finally:
            tmp_conn.close()
            src_conn.close()

        # Quick validation on the restored file
        verify_conn = sqlite3.connect(tmp_restore)
        try:
            res = verify_conn.execute("PRAGMA integrity_check").fetchone()
            if not res or res[0] != "ok":
                raise BackupVerificationError("Restored snapshot failed integrity check")
        finally:
            verify_conn.close()

        # Atomic replacement
        os.replace(tmp_restore, tp)
    finally:
        if tmp_restore.exists():
            tmp_restore.unlink(missing_ok=True)

    return {
        "status": "restored",
        "source_backup": str(bp),
        "target_db": str(tp),
        "sha256": ver["sha256"],
        "bytes": tp.stat().st_size,
        "tables": ver["tables"],
        "counts": ver["counts"],
        "overwritten": was_existing,
    }
