"""docker compose wrappers for the stack lifecycle commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

from botmgr.theme import console, fail, ok, warn


def _compose(root: Path, *args: str) -> int:
    cmd = ["docker", "compose", "--env-file", ".env", *args]
    return subprocess.run(cmd, cwd=root).returncode


def up(root: Path) -> int:
    if not (root / ".env").is_file():
        fail("No .env file — run [accent]botmgr setup[/accent] first")
        return 1
    console.print("[muted]Building and starting the stack…[/muted]")
    code = _compose(root, "up", "-d", "--build")
    if code == 0:
        ok("Stack is up. Watch logs with [accent]botmgr logs[/accent].")
    return code


def down(root: Path) -> int:
    return _compose(root, "down")


def restart(root: Path) -> int:
    code = _compose(root, "down")
    if code != 0:
        return code
    return _compose(root, "up", "-d", "--build")


def logs(root: Path, follow: bool = True, tail: int = 200) -> int:
    args = ["logs", f"--tail={tail}"]
    if follow:
        args.append("-f")
    return _compose(root, *args)


def status(root: Path) -> int:
    code = _compose(root, "ps")
    try:
        resp = httpx.get("http://127.0.0.1:8081/health", timeout=httpx.Timeout(3.0))
        if resp.status_code == 200:
            ok("Bridge health: OK")
        else:
            warn(f"Bridge health: HTTP {resp.status_code}")
    except Exception:
        warn("Bridge health endpoint not reachable on 127.0.0.1:8081")
    return code
