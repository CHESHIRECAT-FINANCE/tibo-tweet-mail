"""Local application service: one operation at a time, explicit scheduler lifecycle."""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, engine, sources, delivery
from .plugins import default_registry, process_item
from .storage import file_lease


def console_editable(cfg):
    return (delivery.spec(cfg)["type"] == "email" and all(
        source["type"] in ("rss", "webpage", "github", "x", "market")
        and not source.get("processors") and not source.get("options")
        for source in cfg["sources"]))


class BusyError(RuntimeError):
    pass


class Controller:
    def __init__(self, directory, registry=None, run_cycle=None):
        self.directory = Path(directory)
        self.config_path = self.directory / "subscriptions.json"
        self.state_path = self.directory / "state.json"
        self.registry = registry or default_registry()
        self.run_cycle = run_cycle or engine.run
        self._guard = threading.RLock()
        self._operation = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._task_thread = None
        self._status = {"running": False, "busy": False, "operation": None, "last_run": None,
                        "next_run": None, "interval": 600, "result": None}

    def status(self):
        with self._guard:
            return copy.deepcopy(self._status)

    def read_config(self):
        if not self.config_path.exists():
            return {"config": None, "has_password": False}
        cfg = config.load(self.config_path, self.registry)
        password = bool(cfg.get("email", {}).get("smtp_password"))
        cfg.setdefault("email", {})["smtp_password"] = ""
        return {"config": cfg, "has_password": password}

    def save_config(self, raw):
        with self._guard:
            if self._status["running"] or self._status["busy"]:
                raise BusyError("Stop automatic checks and wait for the current task before editing")
            cfg = config.validate(raw, self.registry)
            if delivery.spec(cfg)["type"] != "email":
                raise ValueError("The beginner console supports email; use the CLI for other destinations")
            old = config.load(self.config_path, self.registry) if self.config_path.exists() else None
            if not cfg["email"].get("smtp_password") and old:
                if all(cfg["email"].get(k) == old["email"].get(k) for k in ("smtp_user", "smtp_host", "smtp_port")):
                    cfg["email"]["smtp_password"] = old["email"].get("smtp_password", "")
            if not console_editable(cfg) or (old and not console_editable(old)):
                raise ValueError("Advanced configurations must be edited with the CLI; the wizard will not overwrite them")
            config.smtp_config(cfg)
            self.directory.mkdir(parents=True, exist_ok=True)
            from tibo_tweet_mail import save_json
            save_json(self.config_path, cfg)
            self.config_path.chmod(0o600)
            return {"saved": True}

    def _perform(self, operation):
        if not self._operation.acquire(blocking=False):
            raise BusyError("Another task is still running")
        with self._guard:
            self._status.update(busy=True, operation=operation)
        try:
            cfg = config.load(self.config_path, self.registry)
            now = datetime.now(ZoneInfo(cfg["timezone"]))
            if operation == "run":
                reports = self.run_cycle(cfg, self.state_path, registry=self.registry)
                result = {"ok": all(r["status"] == "ok" for r in reports), "reports": reports}
            elif operation == "test-email":
                if delivery.spec(cfg)["type"] != "email":
                    raise ValueError("test-email requires the email destination")
                send = delivery.make_sender(cfg, self.registry)
                send("[InfoRelay] Test / 测试邮件", "Your mailbox is connected. 邮箱已连通。\n" + now.isoformat(), "test")
                result = {"ok": True, "code": "test_submitted"}
            elif operation in ("diagnose", "preview"):
                reports = []
                if operation == "diagnose":
                    try:
                        delivery.diagnose(cfg, self.registry)
                        reports.append({"id": "email", "status": "ok"})
                    except Exception:
                        reports.append({"id": "email", "status": "error", "error": "Check SMTP host, port, application password and network / 请核对邮箱授权码、服务器与网络"})
                for source in cfg["sources"]:
                    if not source["enabled"]:
                        continue
                    try:
                        probe = dict(source, hour=0) if source["type"] == "market" else source
                        rows = sources.fetch(probe, now, self.registry)
                        selected = []
                        for row in rows:
                            row = process_item(row, source.get("processors", []), self.registry)
                            if row is not None and sources.matches(source, row):
                                selected.append(row)
                        report = {"id": source["id"], "status": "ok", "fetched": len(rows), "matched": len(selected)}
                        if operation == "preview":
                            report["items"] = [{"title": r["title"], "url": r["url"], "excerpt": r["body"][:400]} for r in selected[-5:]]
                        reports.append(report)
                    except Exception:
                        reports.append({"id": source["id"], "status": "error", "error": "Check the public source address and its availability / 请核对公开地址与来源可用性"})
                result = {"ok": all(r["status"] == "ok" for r in reports), "reports": reports}
            else:
                raise ValueError("Unknown operation")
        except Exception as error:
            # Provider exceptions can contain credentials/response bodies. Keep them out of UI/logs.
            result = {"ok": False, "code": "operation_failed", "error_type": type(error).__name__}
        finally:
            with self._guard:
                self._status.update(busy=False, operation=None)
            self._operation.release()
        with self._guard:
            self._status.update(last_run=datetime.now(timezone.utc).isoformat(), result=result)
        return result

    def submit(self, operation):
        if operation not in ("preview", "diagnose", "test-email", "run"):
            raise ValueError("Unknown operation")
        with self._guard:
            if self._status["running"] or self._status["busy"]:
                raise BusyError("Stop automatic checks before a manual operation")
            if not self.config_path.exists():
                raise ValueError("Save your settings first / 请先保存配置")
            # Reserve before spawning: two near-simultaneous requests cannot start two jobs.
            self._status.update(busy=True, operation=operation)
            self._task_thread = threading.Thread(target=self._perform, args=(operation,), daemon=True)
            self._task_thread.start()
        return {"accepted": True}

    def start(self, interval=600):
        if type(interval) is not int or not 60 <= interval <= 86400:
            raise ValueError("Interval must be 60–86400 seconds")
        with self._guard:
            if self._status["busy"] or (self._thread and self._thread.is_alive()):
                raise BusyError("A task or scheduler is already running")
            cfg = config.load(self.config_path, self.registry)
            delivery.make_sender(cfg, self.registry)  # Fail before starting if destination settings are incomplete.
            if not any(s["enabled"] for s in cfg["sources"]):
                raise ValueError("Enable at least one source / 请至少启用一个订阅")
            self._stop.clear()
            self._status.update(running=True, interval=interval)
            self._thread = threading.Thread(target=self._loop, args=(interval,), daemon=True)
            self._thread.start()
        return {"running": True}

    def _loop(self, interval):
        try:
            while not self._stop.is_set():
                self._perform("run")
                with self._guard:
                    self._status["next_run"] = datetime.fromtimestamp(datetime.now().timestamp() + interval, timezone.utc).isoformat()
                if self._stop.wait(interval):
                    break
        finally:
            with self._guard:
                self._status.update(running=False, next_run=None)

    def stop(self):
        self._stop.set()
        with self._guard:
            self._status.update(running=False, next_run=None)
        return {"running": False, "finishing_current_task": self.status()["busy"]}
