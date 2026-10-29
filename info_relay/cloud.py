"""Store authenticated ciphertext only on an isolated Git state branch."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from cloud_state import git
from . import state

BRANCH = "inforelay-state"
BLOB = "subscriptions.enc"


def cipher():
    from cryptography.fernet import Fernet
    try:
        return Fernet(os.environ["INFO_RELAY_STATE_KEY"].encode("ascii"))
    except (KeyError, ValueError, UnicodeError):
        raise ValueError("请设置有效的 INFO_RELAY_STATE_KEY；已有状态时不能随意更换密钥") from None


def decode(blob):
    from cryptography.fernet import InvalidToken
    if len(blob) > state.MAX_STATE_BYTES * 2:
        raise ValueError("云端检查点过大")
    try:
        return state.checked(json.loads(cipher().decrypt(blob)))
    except (InvalidToken, ValueError):
        raise ValueError("云端状态无法验证或解密；检查密钥或恢复备份，勿删除状态分支") from None


def publish(directory, data):
    state.checked(data)
    directory = Path(directory)
    tracked = set(git("ls-files", cwd=directory).stdout.splitlines())
    if tracked - {BLOB}:
        raise ValueError("状态分支包含非加密检查点文件，已停止发布")
    target = directory / BLOB
    if target.exists() and decode(target.read_bytes()) == data:
        pass  # Do not create a new encryption token/commit for unchanged state.
    else:
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > state.MAX_STATE_BYTES:
            raise ValueError("云端检查点超过容量限制")
        token = cipher().encrypt(payload)
        temporary = target.with_suffix(".tmp")
        try:
            with temporary.open("wb") as output:
                output.write(token)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    git("add", "--", BLOB, cwd=directory)
    diff = git("diff", "--cached", "--quiet", cwd=directory, check=False)
    if diff.returncode == 1:
        git("-c", "user.name=github-actions[bot]", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", "Update encrypted subscription checkpoints", cwd=directory)
    elif diff.returncode != 0:
        diff.check_returncode()
    for attempt in range(3):
        try:
            git("push", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=directory)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def restore(directory, local_path):
    cipher()  # Fail before making a branch when no usable key is configured.
    directory = Path(directory).resolve()
    probe = git("ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{BRANCH}", check=False)
    if probe.returncode == 0:
        git("fetch", "--no-tags", "origin", f"refs/heads/{BRANCH}")
        git("worktree", "add", "--detach", str(directory), "FETCH_HEAD")
        tracked = set(git("ls-files", cwd=directory).stdout.splitlines())
        if tracked != {BLOB}:
            raise ValueError("云端检查点缺失或含意外文件，已停止发送")
        data = decode((directory / BLOB).read_bytes())
    elif probe.returncode == 2:
        git("worktree", "add", "--detach", str(directory), "HEAD")
        git("switch", "--orphan", BRANCH, cwd=directory)
        data = state.empty()
    else:
        probe.check_returncode()
    publish(directory, data)  # Prove write permission before delivery starts.
    state.write(Path(local_path), data)


def save(directory, local_path):
    if not Path(local_path).is_file():
        raise ValueError("本地检查点缺失，拒绝覆盖云端状态")
    publish(directory, state.read(local_path))
