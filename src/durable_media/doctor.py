from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .adapters.registry import list_targets
from .config import WorkspaceConfig
from .media_tools import tool_path, version as tool_version
from .profiles import PROFILES
from .security import redact

EXPECTED_SECRET_ENVS = (
    "INSTAGRAM_ACCESS_TOKEN",
    "LINKEDIN_ACCESS_TOKEN",
    "YOUTUBE_ACCESS_TOKEN",
    "DISCORD_BOT_TOKEN",
    "DISCORD_WEBHOOK_URL",
)

EXPECTED_MIGRATIONS = {
    "assets": ["metadata_json"],
    "derivatives": ["validation_json"],
    "jobs": ["attempt_count", "next_attempt_at", "retry_limit", "last_error_class", "remote_metadata_json", "approval_payload_json", "approval_fingerprint"],
    "transitions": ["attempt", "retryable"],
    "receipts": ["platform", "account", "verified_at"],
}


def run_doctor(
    cfg: WorkspaceConfig,
    live: bool = False,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Inspect environment, workspace, database, profiles, tools, and adapters."""
    # 1. Python Environment
    py_info = {
        "version": sys.version.split()[0],
        "major": sys.version_info.major,
        "minor": sys.version_info.minor,
        "micro": sys.version_info.micro,
        "supported": bool(sys.version_info >= (3, 11)),
        "executable": sys.executable,
    }

    # 2. Workspace Safety and Permissions
    ws_exists = cfg.root.exists()
    ws_is_dir = cfg.root.is_dir() if ws_exists else False
    ws_writable = os.access(cfg.root, os.W_OK) if ws_exists else False
    ws_perms = oct(cfg.root.stat().st_mode & 0o777) if ws_exists else None

    # Verify subdirectory status
    subdirs = {}
    for sub in ("data/assets", "data/derivatives", "data/previews", "data/receipts", "data/logs", "data/backups", "manifests", "projects"):
        sub_p = cfg.root / sub
        subdirs[sub] = {
            "exists": sub_p.exists(),
            "writable": os.access(sub_p, os.W_OK) if sub_p.exists() else False,
        }

    # Safety check (path traversal)
    is_safe = True
    try:
        cfg.safe(cfg.root)
    except Exception:
        is_safe = False

    ws_info = {
        "root": str(cfg.root),
        "exists": ws_exists,
        "is_directory": ws_is_dir,
        "writable": ws_writable,
        "permissions": ws_perms,
        "safe": is_safe,
        "subdirectories": subdirs,
    }

    # 3. Media Tools
    ffmpeg_bin = tool_path("ffmpeg")
    ffprobe_bin = tool_path("ffprobe")
    tools_info = {
        "ffmpeg": {
            "available": bool(ffmpeg_bin),
            "path": ffmpeg_bin,
            "version": tool_version("ffmpeg") if ffmpeg_bin else None,
        },
        "ffprobe": {
            "available": bool(ffprobe_bin),
            "path": ffprobe_bin,
            "version": tool_version("ffprobe") if ffprobe_bin else None,
        },
    }

    # 4. Registry Readability, Migrations, and Integrity
    reg_db = cfg.db_path
    reg_exists = reg_db.exists()
    reg_readable = os.access(reg_db, os.R_OK) if reg_exists else False
    reg_writable = os.access(reg_db, os.W_OK) if reg_exists else False
    reg_integrity = "not_found"
    reg_tables: list[str] = []
    reg_counts: dict[str, int] = {}
    migrations_status = "uninitialized"

    if reg_exists and reg_readable:
        try:
            conn = sqlite3.connect(reg_db)
            conn.row_factory = sqlite3.Row
            int_res = conn.execute("PRAGMA integrity_check").fetchone()
            reg_integrity = int_res[0] if int_res else "unknown"

            tables_res = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            reg_tables = sorted([r["name"] for r in tables_res])

            for t in reg_tables:
                try:
                    reg_counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except Exception:
                    pass

            # Check migrations
            missing_cols = []
            for table, cols in EXPECTED_MIGRATIONS.items():
                if table in reg_tables:
                    existing_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    for col in cols:
                        if col not in existing_cols:
                            missing_cols.append(f"{table}.{col}")
            migrations_status = "up_to_date" if not missing_cols else f"missing_{len(missing_cols)}_columns"

            conn.close()
        except Exception as err:
            reg_integrity = f"error: {err}"
            migrations_status = "error"

    reg_info = {
        "db_path": str(reg_db),
        "exists": reg_exists,
        "readable": reg_readable,
        "writable": reg_writable,
        "integrity": reg_integrity,
        "migrations": migrations_status,
        "tables": reg_tables,
        "counts": reg_counts,
    }

    # 5. Profiles
    profiles_info = {
        "count": len(PROFILES),
        "available_profiles": sorted(list(PROFILES.keys())),
    }

    # 6. Adapter Targets and Readiness
    targets = list_targets(live=live, timeout=timeout)
    adapter_info = {
        "live_checks_requested": live,
        "targets": targets,
    }

    # 7. Secret References and Configuration Readiness (strictly presence-only, never values)
    env_vars_list = []
    for s_name in EXPECTED_SECRET_ENVS:
        env_vars_list.append({
            "variable": s_name,
            "configured": bool(os.environ.get(s_name)),
            "source": "env" if os.environ.get(s_name) else "missing",
        })

    secrets_dir_path = Path(os.environ.get("HERMES_SECRETS_DIR", "/root/.hermes/secrets")).resolve()
    vault_dir_info = {
        "path": str(secrets_dir_path),
        "exists": secrets_dir_path.exists(),
        "is_dir": secrets_dir_path.is_dir() if secrets_dir_path.exists() else False,
    }
    if secrets_dir_path.exists() and secrets_dir_path.is_dir():
        mode = secrets_dir_path.stat().st_mode & 0o777
        vault_dir_info["permissions"] = oct(mode)
        vault_dir_info["secure"] = (mode & 0o077) == 0

    config_readiness = {
        "expected_environment_variables": env_vars_list,
        "vault_directory": vault_dir_info,
    }

    # 8. Overall Status Evaluation
    status = "healthy"
    issues = []
    if not py_info["supported"]:
        status = "error"
        issues.append(f"Python >=3.11 is required, found {py_info['version']}")
    if not ws_writable:
        status = "error"
        issues.append(f"Workspace directory {cfg.root} is not writable")
    if reg_exists and reg_integrity != "ok":
        status = "error"
        issues.append(f"Registry integrity check failed: {reg_integrity}")
    if not tools_info["ffmpeg"]["available"]:
        if status != "error":
            status = "warning"
        issues.append("ffmpeg is not installed or not in PATH")
    if not tools_info["ffprobe"]["available"]:
        if status != "error":
            status = "warning"
        issues.append("ffprobe is not installed or not in PATH")

    report = {
        "overall_status": status,
        "issues": issues,
        "python": py_info,
        "workspace": ws_info,
        "tools": tools_info,
        "registry": reg_info,
        "profiles": profiles_info,
        "adapters": adapter_info,
        "configuration_readiness": config_readiness,
        "secrets": "[REDACTED]",
    }

    return redact(report)
