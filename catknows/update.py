"""One-command update for a local catknows checkout.

    python -m catknows.update

Pulls the latest from GitHub (main), reinstalls so new dependencies land,
and runs the offline self-check. Tool-agnostic sibling of the Claude Code
/catknows-update skill — and the engine behind the `update_catknows` MCP
tool, so ChatGPT & friends can update too. Refuses to touch a dirty
working tree and only fast-forwards; it never discards local work.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(*cmd: str, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run(
        cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def check() -> dict:
    """What would an update do? Makes no changes."""
    if not (REPO / ".git").exists():
        return {"error": "Not a git checkout — clone from GitHub to use updates."}
    _, head = _run("git", "rev-parse", "--short", "HEAD")
    rc, dirty = _run("git", "status", "--short")
    _run("git", "fetch", "origin")
    _, new = _run("git", "log", "--oneline", "HEAD..origin/main")
    return {
        "current": head,
        "dirty": dirty or None,
        "new_commits": new.splitlines() if new else [],
    }


def update() -> dict:
    """Fast-forward to origin/main, reinstall, self-check. Safe by refusal:
    dirty tree or diverged history -> error out, change nothing."""
    result = check()
    if result.get("error"):
        return result
    if result["dirty"]:
        result["error"] = ("Local changes present — commit or stash them first. "
                          "Nothing was touched.")
        return result

    if result["new_commits"]:
        rc, out = _run("git", "pull", "--ff-only", "origin", "main")
        if rc != 0:
            result["error"] = f"Pull failed (diverged history?): {out[-300:]}"
            return result
        rc, out = _run(sys.executable, "-m", "pip", "install", "-e", ".[mcp]",
                       timeout=600)
        if rc != 0:
            result["error"] = f"Reinstall failed: {out[-500:]}"
            return result

    # Self-check runs in a fresh process so it exercises the NEW code.
    rc, out = _run(sys.executable, "-m", "catknows.normalize")
    result["health"] = out if rc == 0 else f"SELF-CHECK FAILED: {out[-300:]}"
    _, result["now_at"] = _run("git", "rev-parse", "--short", "HEAD")
    result["restart_reminder"] = (
        "Reconnect/restart your AI client — a running MCP server keeps the "
        "old code loaded until it restarts."
    )
    return result


def main() -> int:
    result = update()
    print(json.dumps(result, indent=2))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
