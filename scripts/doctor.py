"""준비 전용 읽기 진단. 인증 파일/환경변수 값은 수집하지 않는다."""
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def probe(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return {"argv": argv, "exit_code": p.returncode,
                "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "error": type(exc).__name__}


def main():
    native = platform.system() == "Linux"
    result = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "machine": platform.machine(),
              "python": sys.version, "cwd": str(Path.cwd()),
              "home": str(Path.home()), "logical_cpu_available": os.cpu_count(),
              "tools": {}, "checks": {}, "readiness": "NOT_EVALUATED"}
    for name in ("python3", "git", "uv", "node", "npm", "ffmpeg", "ffprobe", "codex"):
        path = shutil.which(name)
        entry = {"path": path, "native": bool(path and (not native or not path.startswith('/mnt/')))}
        if path and entry["native"]:
            entry["version"] = probe([path, "-version" if name in ("ffmpeg", "ffprobe") else "--version"])
        result["tools"][name] = entry
    if native:
        result["os_release"] = Path('/etc/os-release').read_text()
        result["memory"] = probe(["free", "-b"])
        result["disk"] = probe(["df", "-B1", "/"])
        result["identity"] = probe(["id"])
        result["sudo_noninteractive"] = probe(["sudo", "-n", "true"])
        result["project_exists"] = (Path.home() / "src/local-meeting-minutes").exists()
        for key, path in {"development_codex_home": Path.home()/'.codex',
                          "runtime_codex_home": Path.home()/'.local/share/local-meeting-minutes/codex-home',
                          "hf_cache": Path.home()/'.cache/huggingface'}.items():
            result[key] = {"path": str(path), "exists": path.exists()}
        result["checks"]["linux_repo"] = "FAIL" if str(Path.cwd()).startswith('/mnt/') else "NOT_RUN"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
