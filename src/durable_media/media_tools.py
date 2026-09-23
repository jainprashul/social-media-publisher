"""Safe, bounded ffmpeg/ffprobe execution with redacted provenance."""
import json, os, re, shutil, subprocess
from pathlib import Path

_SECRET = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization)=?[^\s]+")

def redact_text(value, limit=4096):
    return _SECRET.sub(r"\1=<redacted>", str(value))[:limit]

def tool_path(name):
    return shutil.which(name)

def version(name="ffmpeg"):
    path=tool_path(name)
    if not path: return None
    p=subprocess.run([path,"-version"],capture_output=True,text=True,check=False,timeout=10)
    return redact_text((p.stdout or p.stderr).splitlines()[0] if (p.stdout or p.stderr) else "")

def run_tool(name,args,timeout=120,workspace=None):
    path=tool_path(name)
    if not path: raise RuntimeError(f"{name} is not installed")
    argv=[path,*[str(x) for x in args]]
    if workspace:
        root=Path(workspace).resolve()
        for x in argv[1:]:
            if x.startswith('/') and Path(x).exists():
                p=Path(x).resolve()
                if root != p and root not in p.parents: raise ValueError("media tool path escapes workspace")
    try:
        p=subprocess.run(argv,capture_output=True,text=True,check=False,timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{name} timed out after {timeout}s") from e
    return {"command":argv,"tool":name,"version":version(name),"returncode":p.returncode,"stdout":redact_text(p.stdout),"stderr":redact_text(p.stderr)}

def run_ffprobe(path, timeout=30, workspace=None):
    result=run_tool("ffprobe",["-v","error","-print_format","json","-show_format","-show_streams",str(path)],timeout,workspace)
    if result["returncode"]: raise ValueError("ffprobe could not decode media: "+result["stderr"][:300])
    try: result["data"]=json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError as e: raise ValueError("ffprobe returned malformed JSON") from e
    return result

def run_ffmpeg(args, timeout=300, workspace=None):
    return run_tool("ffmpeg",args,timeout,workspace)
