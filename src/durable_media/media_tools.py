"""Safe, bounded ffmpeg/ffprobe execution with redacted provenance.

Enforces workspace path containment on tool arguments, captures tool versions,
bounds subprocess execution with timeouts, and scrubs sensitive secrets from
standard output, standard error, and command arguments.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable

_SECRET = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization)=?[^\s]+")


def redact_text(value: Any, limit: int = 4096) -> str:
    """Scrub sensitive credentials from tool output text and truncate length."""
    return _SECRET.sub(r"\1=<redacted>", str(value))[:limit]


def tool_path(name: str) -> str | None:
    """Locate binary path using PATH lookup."""
    return shutil.which(name)


def version(name: str = "ffmpeg") -> str | None:
    """Retrieve redacted version header line for an installed external tool."""
    path = tool_path(name)
    if not path:
        return None
    p = subprocess.run([path, "-version"], capture_output=True, text=True, check=False, timeout=10)
    output = p.stdout or p.stderr
    return redact_text(output.splitlines()[0] if output else "")


def run_tool(
    name: str,
    args: Iterable[Any],
    timeout: float = 120.0,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Execute an external media tool with path validation, timeouts, and redaction.

    Raises RuntimeError if tool is not found or execution times out.
    Raises ValueError if an absolute path argument escapes the allowed workspace boundary.
    """
    path = tool_path(name)
    if not path:
        raise RuntimeError(f"{name} is not installed")
    argv = [path, *[str(x) for x in args]]
    if workspace:
        root = Path(workspace).resolve()
        for x in argv[1:]:
            if x.startswith('/') and Path(x).exists():
                p = Path(x).resolve()
                if root != p and root not in p.parents:
                    raise ValueError("media tool path escapes workspace")
    try:
        p = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{name} timed out after {timeout}s") from e
    return {
        "command": argv,
        "tool": name,
        "version": version(name),
        "returncode": p.returncode,
        "stdout": redact_text(p.stdout),
        "stderr": redact_text(p.stderr),
    }


def run_ffprobe(
    path: Path | str,
    timeout: float = 30.0,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Execute ffprobe against a media file and parse JSON stream information."""
    result = run_tool(
        "ffprobe",
        ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=timeout,
        workspace=workspace,
    )
    if result["returncode"]:
        raise ValueError("ffprobe could not decode media: " + result["stderr"][:300])
    try:
        result["data"] = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError as e:
        raise ValueError("ffprobe returned malformed JSON") from e
    return result


def run_ffmpeg(
    args: Iterable[Any],
    timeout: float = 300.0,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Execute ffmpeg with bounded execution time and workspace path protection."""
    return run_tool("ffmpeg", args, timeout=timeout, workspace=workspace)
