"""Storage SPI. A store owns validation, atomic persistence and an exclusive lease."""
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Protocol
from . import state


class CheckpointStore(Protocol):
    def load(self) -> dict: ...
    def save(self, data: dict) -> None: ...
    def lease(self) -> ContextManager[None]: ...


@contextmanager
def file_lease(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # Windows: release the single byte lock by closing the handle.
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0"); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except BlockingIOError:
            raise RuntimeError("Another InfoRelay process is using this state") from None
        yield
    finally:
        handle.close()


class JsonStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return state.read(self.path)

    def save(self, data):
        state.write(self.path, data)

    def lease(self):
        return file_lease(self.path.with_suffix(".lock"))


class SQLiteStore:
    """Single-writer SQLite backend, using the same portable checkpoint schema.

    The database is local; cloud.py intentionally supports JsonStore only. A lease
    spans the whole delivery cycle, not just the SQL transaction.
    """
    def __init__(self, path):
        self.path = Path(path)

    def lease(self):
        return file_lease(self.path.with_suffix(".lock"))

    def load(self):
        import json
        import sqlite3
        if not self.path.exists():
            return state.empty()
        try:
            with sqlite3.connect(str(self.path)) as connection:
                rows = connection.execute("SELECT payload FROM checkpoints WHERE id=1").fetchall()
            if len(rows) != 1 or len(rows[0][0].encode()) > state.MAX_STATE_BYTES:
                raise ValueError("Invalid checkpoint row")
            return state.checked(json.loads(rows[0][0]))
        except (sqlite3.Error, ValueError, TypeError):
            raise ValueError("SQLite checkpoint is unreadable; restore a backup, do not delete it") from None

    def save(self, data):
        import json
        import sqlite3
        payload = json.dumps(state.checked(data), ensure_ascii=False, allow_nan=False)
        if len(payload.encode()) > state.MAX_STATE_BYTES:
            raise ValueError("Checkpoint exceeds size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create restrictive file before SQLite opens it.
        if not self.path.exists():
            import os
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS checkpoints (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            connection.execute("INSERT OR REPLACE INTO checkpoints (id,payload) VALUES (1,?)", (payload,))
