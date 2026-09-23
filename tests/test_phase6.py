from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import pytest

from durable_media.adapters.base import (
    AuthenticationError,
    LiveChecksDisabledError,
    RateLimitError,
    ServerError,
    TimeoutError,
    ValidationError,
    is_live_allowed,
)
from durable_media.adapters.fake import FakePublisher
from durable_media.adapters.registry import list_targets, validate_target_config
from durable_media.adapters.transport import FakeHttpTransport
from durable_media.backup import (
    BackupError,
    BackupVerificationError,
    SafeOverwriteError,
    create_backup,
    restore_backup,
    verify_backup,
)
from durable_media.config import WorkspaceConfig
from durable_media.doctor import run_doctor
from durable_media.observability import JsonLogger, inspect_logs, scan_package
from durable_media.obsidian_links import AI_OWNED_MARKER
from durable_media.registry import Registry
from durable_media.scheduler import (
    FileLock,
    SchedulerLockError,
    clean_old_reports,
    run_nightly_report,
)


def run_cli_cmd(workspace: Path | str, *args: str) -> dict:
    env = {**os.environ, "PYTHONPATH": "src"}
    res = subprocess.run(
        [sys.executable, "-m", "durable_media.cli", "--workspace", str(workspace), *args],
        cwd=str(Path(__file__).parent.parent),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(res.stdout)


# =====================================================================
# Task 1 & 2: Packaging, CI Quality Gates & File Hygiene
# =====================================================================

def test_packaging_metadata():
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    assert pyproject_path.exists()
    content = pyproject_path.read_text(encoding="utf-8")
    assert 'name = "durable-media-publishing"' in content
    assert 'requires-python = ">=3.11"' in content
    assert 'media-pipeline = "durable_media.cli:main"' in content
    assert "build-backend" in content


def test_ci_workflow_definition():
    ci_path = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists()
    ci_content = ci_path.read_text(encoding="utf-8")
    assert "quality-and-offline-tests" in ci_content
    assert "live-provider-checks" in ci_content
    assert "DURABLE_MEDIA_ALLOW_LIVE_CHECKS" in ci_content
    assert "compileall" in ci_content
    assert "git diff --check" in ci_content


def test_file_hygiene_and_scan_package(tmp_path):
    # Clean repo should contain zero secret files
    repo_root = Path(__file__).parent.parent
    bad = scan_package(repo_root)
    assert bad == [], f"Found forbidden secret or generated files: {bad}"

    # Verify detection of secret files in a test directory
    secret_env = tmp_path / ".env"
    secret_env.write_text("API_KEY=xyz")
    secret_key = tmp_path / "server.key"
    secret_key.write_text("dummy key")
    id_rsa = tmp_path / "id_rsa"
    id_rsa.write_text("ssh key")
    normal_file = tmp_path / "normal.txt"
    normal_file.write_text("hello")

    detected = scan_package(tmp_path)
    detected_names = {Path(p).name for p in detected}
    assert {".env", "server.key", "id_rsa"} <= detected_names
    assert "normal.txt" not in detected_names


# =====================================================================
# Task 3: SQLite Backup and Restore
# =====================================================================

def test_backup_create_verify_and_restore_roundtrip(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    reg = Registry(cfg.db_path)

    # Populate registry with some data
    reg.add_asset(
        asset_id="asset_test1",
        project="p1",
        kind="video",
        role="master",
        path="data/assets/test.mp4",
        sha256="abc12345",
        mime="video/mp4",
        bytes=1024,
        created_at="2026-09-23T00:00:00Z",
        source_json="{}",
        metadata_json="{}",
    )
    reg.add_job(
        job_id="job_test1",
        derivative_id="deriv_test1",
        platform="fake",
        destination="dest1",
        caption="hello",
        visibility="public",
        scheduled_at=None,
        idempotency_key="idem1",
        state="approved",
        external_json="{}",
        created_at="2026-09-23T00:00:00Z",
    )

    # 1. Create backup
    bk_info = create_backup(cfg)
    assert bk_info["status"] == "created"
    backup_file = Path(bk_info["backup_path"])
    checksum_file = Path(bk_info["checksum_path"])
    assert backup_file.exists()
    assert checksum_file.exists()
    assert bk_info["counts"]["assets"] == 1
    assert bk_info["counts"]["jobs"] == 1

    # 2. Verify backup
    ver_info = verify_backup(backup_file)
    assert ver_info["status"] == "valid"
    assert ver_info["integrity_check"] == "ok"
    assert "assets" in ver_info["tables"]

    # 3. Restore to a new location
    target_new = tmp_path / "restored.sqlite3"
    rest_info = restore_backup(backup_file, target_new)
    assert rest_info["status"] == "restored"
    assert target_new.exists()

    # Verify restored database content
    reg_restored = Registry(target_new)
    assert len(reg_restored.assets()) == 1
    assert reg_restored.asset("asset_test1")["project"] == "p1"
    assert reg_restored.job("job_test1")["destination"] == "dest1"


def test_backup_checksum_drift_detection(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    Registry(cfg.db_path)

    bk_info = create_backup(cfg)
    backup_file = Path(bk_info["backup_path"])

    # Tamper with backup file
    with open(backup_file, "ab") as f:
        f.write(b"corruption")

    with pytest.raises(BackupVerificationError, match="Checksum verification failed"):
        verify_backup(backup_file)

    target_new = tmp_path / "fail.sqlite3"
    with pytest.raises(BackupVerificationError):
        restore_backup(backup_file, target_new)


def test_backup_missing_checksum_sidecar(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    Registry(cfg.db_path)

    bk_info = create_backup(cfg)
    backup_file = Path(bk_info["backup_path"])
    checksum_file = Path(bk_info["checksum_path"])
    checksum_file.unlink()

    with pytest.raises(BackupVerificationError, match="Missing checksum sidecar"):
        verify_backup(backup_file)


def test_backup_corrupt_non_sqlite_file(tmp_path):
    fake_backup = tmp_path / "corrupt.sqlite3"
    fake_backup.write_bytes(b"NOT A SQLITE DATABASE")
    fake_sidecar = tmp_path / "corrupt.sqlite3.sha256"
    import hashlib
    digest = hashlib.sha256(b"NOT A SQLITE DATABASE").hexdigest()
    fake_sidecar.write_text(f"{digest}  corrupt.sqlite3\n")

    with pytest.raises(BackupVerificationError, match="Corrupt or non-SQLite"):
        verify_backup(fake_backup)


def test_backup_incompatible_schema_missing_tables(tmp_path):
    incomp_db = tmp_path / "incompatible.sqlite3"
    conn = sqlite3.connect(incomp_db)
    conn.execute("CREATE TABLE some_random_table (id INT)")
    conn.commit()
    conn.close()

    import hashlib
    digest = hashlib.sha256(incomp_db.read_bytes()).hexdigest()
    (tmp_path / "incompatible.sqlite3.sha256").write_text(f"{digest}  incompatible.sqlite3\n")

    with pytest.raises(BackupVerificationError, match="Incompatible database schema"):
        verify_backup(incomp_db)


def test_backup_safe_overwrite_protection(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    Registry(cfg.db_path)
    bk_info = create_backup(cfg)
    backup_file = Path(bk_info["backup_path"])

    existing_target = tmp_path / "existing.sqlite3"
    existing_target.write_bytes(b"existing data payload")

    # Overwrite without --force must fail
    with pytest.raises(SafeOverwriteError, match="Refusing to overwrite without --force"):
        restore_backup(backup_file, existing_target, force=False)

    # File must not have been modified
    assert existing_target.read_bytes() == b"existing data payload"

    # Overwrite with force=True must succeed
    res = restore_backup(backup_file, existing_target, force=True)
    assert res["status"] == "restored"
    assert res["overwritten"] is True


def test_backup_cli_commands(tmp_path):
    ws = tmp_path / "ws"
    init_res = run_cli_cmd(ws, "init")
    assert init_res["workspace"] == str(ws)

    # CLI create
    create_res = run_cli_cmd(ws, "backup", "create")
    assert create_res["status"] == "created"
    bp = create_res["backup_path"]

    # CLI verify
    ver_res = run_cli_cmd(ws, "backup", "verify", bp)
    assert ver_res["status"] == "valid"

    # CLI restore with force
    rest_res = run_cli_cmd(ws, "backup", "restore", bp, "--force")
    assert rest_res["status"] == "restored"


# =====================================================================
# Task 4: Operational Diagnostics (Doctor)
# =====================================================================

def test_doctor_healthy_workspace(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    Registry(cfg.db_path)

    doc = run_doctor(cfg, live=False)
    assert doc["overall_status"] in ("healthy", "warning")
    assert doc["python"]["supported"] is True
    assert doc["workspace"]["exists"] is True
    assert doc["workspace"]["writable"] is True
    assert doc["workspace"]["safe"] is True
    assert doc["registry"]["integrity"] == "ok"
    assert doc["registry"]["migrations"] == "up_to_date"
    assert doc["profiles"]["count"] == 5
    assert doc["adapters"]["live_checks_requested"] is False
    assert doc["secrets"] == "[REDACTED]"
    assert isinstance(doc["configuration_readiness"]["expected_environment_variables"], list)


def test_doctor_uninitialized_workspace(tmp_path):
    non_existent = tmp_path / "missing_dir"
    cfg = WorkspaceConfig(non_existent)
    doc = run_doctor(cfg, live=False)
    assert doc["workspace"]["exists"] is False
    assert doc["registry"]["integrity"] == "not_found"


def test_doctor_corrupt_registry(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    cfg.db_path.write_bytes(b"NOT A VALID SQLITE DATABASE")

    doc = run_doctor(cfg, live=False)
    assert doc["overall_status"] == "error"
    assert "error" in doc["registry"]["integrity"]


def test_doctor_missing_tools_warning(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    Registry(cfg.db_path)

    import durable_media.doctor as doc_mod
    monkeypatch.setattr(doc_mod, "tool_path", lambda name: None)

    doc = run_doctor(cfg, live=False)
    assert doc["tools"]["ffmpeg"]["available"] is False
    assert doc["tools"]["ffprobe"]["available"] is False
    assert doc["overall_status"] == "warning"
    assert any("ffmpeg is not installed" in issue for issue in doc["issues"])


def test_doctor_cli_output(tmp_path):
    ws = tmp_path / "ws"
    run_cli_cmd(ws, "init")
    doc = run_cli_cmd(ws, "doctor")
    assert "overall_status" in doc
    assert "python" in doc
    assert "tools" in doc
    assert "registry" in doc


# =====================================================================
# Task 5: Provider Readiness & Opt-In Live Checks
# =====================================================================

def test_dry_run_validation_makes_zero_transport_calls():
    transport = FakeHttpTransport()

    for plat in ("instagram", "linkedin", "youtube", "discord", "fake"):
        res = validate_target_config(plat, destination="dummy", live=False, transport=transport)
        assert res["status"] in ("valid", "invalid")
        # Absolutely zero transport network calls should occur
        assert len(transport.calls) == 0


def test_live_checks_gated_by_default(monkeypatch):
    monkeypatch.delenv("DURABLE_MEDIA_ALLOW_LIVE_CHECKS", raising=False)
    assert not is_live_allowed()

    transport = FakeHttpTransport()
    for plat in ("instagram", "linkedin", "youtube", "discord", "fake"):
        res = validate_target_config(plat, destination="dummy", live=True, transport=transport)
        assert res["status"] == "gated"
        assert res["error_class"] == "LiveChecksDisabledError"
        assert len(transport.calls) == 0


def test_live_check_opt_in_success(monkeypatch):
    monkeypatch.setenv("DURABLE_MEDIA_ALLOW_LIVE_CHECKS", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "fake-token")

    transport = FakeHttpTransport()
    transport.register_response(
        "GET",
        r".*me\?fields=id,name.*",
        status_code=200,
        json_body={"id": "178414", "name": "Test Account"},
    )

    res = validate_target_config("instagram", destination="178414", live=True, transport=transport)
    assert res["status"] == "ok"
    assert res["error_class"] is None
    assert len(transport.calls) == 1
    # Check bounded timeout <= 5.0s
    assert transport.calls[0]["timeout"] <= 5.0


def test_live_check_opt_in_handles_errors_without_leaking_secrets(monkeypatch):
    monkeypatch.setenv("DURABLE_MEDIA_ALLOW_LIVE_CHECKS", "1")
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "super-secret-linkedin-token")

    transport = FakeHttpTransport()
    # 401 Unauthorized
    transport.register_response(
        "GET",
        r".*v2/userinfo.*",
        status_code=401,
        json_body={"error": "unauthorized", "message": "Expired token"},
    )

    res = validate_target_config("linkedin", destination="urn:li:person:123", live=True, transport=transport)
    assert res["status"] == "error"
    assert res["error_class"] == "AuthenticationError"
    # Never leak token in error report
    assert "super-secret-linkedin-token" not in json.dumps(res)


def test_fake_publisher_readiness():
    fake = FakePublisher(mode="success")
    # Dry-run
    ready = fake.check_readiness(live=False)
    assert ready["status"] == "ready"

    # Live without env
    gated = fake.check_readiness(live=True)
    assert gated["status"] == "gated"


# =====================================================================
# Task 6: Scheduler-Safe Nightly Reporting
# =====================================================================

def test_nightly_report_repeated_runs_idempotent(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    reg = Registry(cfg.db_path)

    out_file = tmp_path / "nightly.json"

    # First run
    rep1, p1 = run_nightly_report(cfg, reg, output_path=out_file, output_format="json")
    assert p1 == out_file
    assert out_file.exists()
    content1 = out_file.read_text(encoding="utf-8")

    # Second run with same state
    rep2, p2 = run_nightly_report(cfg, reg, output_path=out_file, output_format="json")
    content2 = out_file.read_text(encoding="utf-8")

    assert rep1["summary"] == rep2["summary"]
    # Both produced valid JSON
    assert json.loads(content1)["summary"] == json.loads(content2)["summary"]


def test_nightly_report_concurrent_locking(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    reg = Registry(cfg.db_path)
    lock_file = tmp_path / "test.lock"

    # Hold lock in first context
    with FileLock(lock_file):
        # Attempting to run report with same lock file must fail with SchedulerLockError
        with pytest.raises(SchedulerLockError, match="Could not acquire lock"):
            run_nightly_report(cfg, reg, lock_path=lock_file, lock_timeout=0.0)


def test_nightly_report_human_note_protection(tmp_path):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    reg = Registry(cfg.db_path)

    human_note = tmp_path / "my_notes.md"
    human_note.write_text("# My Personal Thoughts\nDo not overwrite this note!\n")

    with pytest.raises(PermissionError, match="not AI-owned"):
        run_nightly_report(cfg, reg, output_path=human_note, output_format="markdown")

    # Ensure content was untouched
    assert "My Personal Thoughts" in human_note.read_text()


def test_nightly_report_atomic_failure_cleanup(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    cfg = WorkspaceConfig(ws).ensure()
    reg = Registry(cfg.db_path)
    out_file = tmp_path / "failed_report.json"

    import durable_media.scheduler as sched_mod

    def fail_json(r):
        raise RuntimeError("Simulated serialization crash")

    monkeypatch.setattr(sched_mod, "report_to_json", fail_json)

    with pytest.raises(RuntimeError, match="Simulated serialization crash"):
        run_nightly_report(cfg, reg, output_path=out_file)

    # Output file and temporary files should not exist
    assert not out_file.exists()
    temp_files = list(tmp_path.glob(".*.tmp.*"))
    assert len(temp_files) == 0


def test_clean_old_reports_retention(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    old_ai_report = reports_dir / "old_report.md"
    old_ai_report.write_text(f"{AI_OWNED_MARKER}\nOld report\n")
    # Set mtime to 40 days ago
    past_time = time.time() - (40 * 86400)
    os.utime(old_ai_report, (past_time, past_time))

    old_human_note = reports_dir / "old_human.md"
    old_human_note.write_text("Old human thoughts\n")
    os.utime(old_human_note, (past_time, past_time))

    fresh_ai_report = reports_dir / "fresh_report.md"
    fresh_ai_report.write_text(f"{AI_OWNED_MARKER}\nFresh report\n")

    purged = clean_old_reports(reports_dir, retention_days=30)
    assert str(old_ai_report) in purged
    assert not old_ai_report.exists()
    assert old_human_note.exists()
    assert fresh_ai_report.exists()


# =====================================================================
# Task 7: Operational Log Rotation and Bounded Storage
# =====================================================================

def test_json_logger_redaction_before_write(tmp_path):
    log_file = tmp_path / "events.jsonl"
    logger = JsonLogger(log_file)

    logger.event(
        action="publish_attempt",
        auth_header="Bearer secret-token-12345",
        signed_url="https://s3.amazonaws.com/bucket/video.mp4?signature=secret_sig_xyz&token=secret_tok",
        client_secret="my_super_secret",
        normal_key="normal_value",
    )

    content = log_file.read_text(encoding="utf-8")
    assert "secret-token-12345" not in content
    assert "secret_sig_xyz" not in content
    assert "secret_tok" not in content
    assert "my_super_secret" not in content
    assert "[REDACTED]" in content
    assert "normal_value" in content


def test_json_logger_rotation_size_boundary(tmp_path):
    log_file = tmp_path / "bounded.jsonl"
    # Set a tiny max_bytes (300 bytes) and backup_count = 2
    logger = JsonLogger(log_file, max_bytes=300, backup_count=2)

    for i in range(10):
        logger.event(i=i, msg="filling up log bytes with structured event")

    assert log_file.exists()
    backup_1 = tmp_path / "bounded.jsonl.1"
    backup_2 = tmp_path / "bounded.jsonl.2"
    assert backup_1.exists()
    # Number of backups should not exceed backup_count (2)
    backup_3 = tmp_path / "bounded.jsonl.3"
    assert not backup_3.exists()


def test_logs_inspect_cli(tmp_path):
    ws = tmp_path / "ws"
    log_file = ws / "data" / "logs" / "events.jsonl"
    logger = JsonLogger(log_file)
    logger.event(event="boot", secret_token="abc")
    logger.event(event="ready", password="xyz")

    out = run_cli_cmd(ws, "logs", "inspect", "--log-file", str(log_file))
    assert out["exists"] is True
    assert out["total_events"] == 2
    assert len(out["recent_events"]) == 2
    assert out["recent_events"][0]["secret_token"] == "[REDACTED]"
    assert out["recent_events"][1]["password"] == "[REDACTED]"
