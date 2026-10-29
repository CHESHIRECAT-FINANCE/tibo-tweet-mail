"""Persist only delivery checkpoints on an isolated Git branch in Actions."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BRANCH = "mail-state"
FILES = ("state.json", "market-state.json")


def checked_post(item: dict) -> dict:
    """Persist only the public fields needed to retry an already selected notice."""
    if not isinstance(item, dict):
        raise ValueError('Invalid pending notice')
    post_id = str(item.get('id') or '')
    text = item.get('text')
    stamp = item.get('created_timestamp')
    author = item.get('author') or {}
    name = author.get('screen_name', '') if isinstance(author, dict) else None
    if (not re.fullmatch(r'[0-9]{1,30}', post_id)
            or not isinstance(text, str) or len(text) > 50_000
            or type(stamp) not in (int, float) or not math.isfinite(stamp)
            or not 0 <= stamp < 253402214400
            or not isinstance(name, str) or (name and not re.fullmatch(r'[A-Za-z0-9_]{1,15}', name))):
        raise ValueError('Invalid pending notice fields')
    return {'id': post_id, 'text': text, 'created_timestamp': stamp,
            'url': f'https://x.com/i/status/{post_id}', 'author': {'screen_name': name}}


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
        pending = data.get('pending', [])
        if not isinstance(pending, list):
            raise ValueError('Invalid pending notice queue')
        cleaned_pending = [checked_post(item) for item in pending]
        ids = [item['id'] for item in cleaned_pending]
        if len(ids) != len(set(ids)) or set(ids).intersection(seen) or (pending and not seeded):
            raise ValueError('Inconsistent pending notice checkpoint')
        result = {"seeded": seeded, "seen": seen[-1000:]}
        if cleaned_pending:
            result['pending'] = cleaned_pending
        return result
    if name == "market-state.json":
        from datetime import date

        value = data.get("sent_date")
        if value is None:
            return {}
        try:
            valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Invalid market checkpoint")
        return {"sent_date": value}
    raise ValueError("Unexpected state file")


def write_state(directory: Path, name: str, data: dict) -> None:
    cleaned = checked_state(name, data)
    path = directory / name
    temporary = path.with_suffix('.json.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(cleaned, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save(directory: Path) -> None:
    cleaned = {}
    for name in FILES:
        path = directory / name
        if not path.is_file():
            raise RuntimeError(f"Missing checkpoint: {name}; refusing to persist incomplete state")
        cleaned[name] = checked_state(name, json.loads(path.read_text(encoding="utf-8")))
    tracked = set(git("ls-files", cwd=directory).stdout.splitlines())
    if tracked - set(FILES):
        raise RuntimeError("Unexpected tracked files in state branch; refusing to publish")
    for name, payload in cleaned.items():
        write_state(directory, name, payload)
        git("add", "--", name, cwd=directory)
    # Never stage configuration, logs, lock files, or arbitrary directory contents.
    diff = git("diff", "--cached", "--quiet", cwd=directory, check=False)
    if diff.returncode == 1:
        git("-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", "Update mail delivery checkpoints", cwd=directory)
    elif diff.returncode != 0:
        diff.check_returncode()
    # Retrying the same commit is safe, including a lost push acknowledgement.
    # Never force-push or merge stale checkpoints over another runner's deliveries.
    for attempt in range(3):
        try:
            git("push", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=directory)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Avoid exposing URLs, tokens, or arbitrary git output in public logs.
        raise SystemExit("State synchronization failed; inspect repository permissions and connectivity") from None
