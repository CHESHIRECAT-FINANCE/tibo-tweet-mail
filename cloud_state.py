"""Persist only delivery checkpoints on an isolated Git branch in Actions."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BRANCH = "mail-state"
FILES = ("state.json", "market-state.json")


def git(*args: str, cwd: Path | None = None, check: bool = True):
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                          text=True, capture_output=True, timeout=60)


def checked_state(name: str, data: dict) -> dict:
    """Whitelist fields so credentials can never enter the public state branch."""
    if not isinstance(data, dict):
        raise ValueError(f"Invalid {name}")
    if name == "state.json":
        seen = data.get("seen", [])
        seeded = data.get("seeded", False)
        if not isinstance(seeded, bool) or not isinstance(seen, list):
            raise ValueError("Invalid timeline checkpoint")
        if any(not isinstance(item, str) or not item.isascii() or not item.isdigit() for item in seen):
            raise ValueError("Post IDs must be numeric strings")
        return {"seeded": seeded, "seen": seen[-1000:]}
    if name == "market-state.json":
        from datetime import date

        value = data.get("sent_date")
        if value is None:
            return {}
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("Invalid market checkpoint")
        return {"sent_date": value}
    raise ValueError("Unexpected state file")


def write_state(directory: Path, name: str, data: dict) -> None:
    cleaned = checked_state(name, data)
    (directory / name).write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")


def save(directory: Path) -> None:
    for name in FILES:
        path = directory / name
        if path.exists():
            write_state(directory, name, json.loads(path.read_text(encoding="utf-8")))
            git("add", "--", name, cwd=directory)
    # Never stage configuration, logs, lock files, or arbitrary directory contents.
    diff = git("diff", "--cached", "--quiet", cwd=directory, check=False)
    if diff.returncode == 1:
        git("-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", "Update mail delivery checkpoints", cwd=directory)
    elif diff.returncode != 0:
        diff.check_returncode()
    git("push", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=directory)


def restore(directory: Path) -> None:
    probe = git("ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{BRANCH}", check=False)
    if probe.returncode == 0:
        git("fetch", "--no-tags", "origin", f"refs/heads/{BRANCH}")
        git("worktree", "add", "--detach", str(directory), "FETCH_HEAD")
        for name in FILES:
            path = directory / name
            if not path.is_file():
                raise RuntimeError(f"Missing persisted checkpoint: {name}; restore it before sending")
            write_state(directory, name, json.loads(path.read_text(encoding="utf-8")))
    elif probe.returncode == 2:
        # An absent branch is distinct from network or permission failures.
        git("worktree", "add", "--detach", str(directory), "HEAD")
        git("switch", "--orphan", BRANCH, cwd=directory)
        initial = json.loads(os.environ.get("TIBO_INITIAL_STATE") or "{}")
        if not isinstance(initial, dict):
            raise ValueError("Initial state must be a JSON object")
        for name in FILES:
            write_state(directory, name, initial.get(name, {}))
    else:
        probe.check_returncode()
    # Verify persistence permissions before any email is sent.
    save(directory)


if __name__ == "__main__":
    try:
        directory = Path(os.environ["TIBO_STATE_DIR"]).resolve()
        if sys.argv[1:] == ["restore"]:
            restore(directory)
        elif sys.argv[1:] == ["save"]:
            save(directory)
        else:
            raise ValueError("Usage: cloud_state.py restore|save")
    except subprocess.CalledProcessError:
        # Avoid exposing URLs, tokens, or arbitrary git output in public logs.
        raise SystemExit("State synchronization failed; inspect repository permissions and connectivity") from None
