"""Independent sources, persistent pending queues, and one digest per source."""
from __future__ import annotations

import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tibo_tweet_mail import deliver
from .config import smtp_config
from .sources import digest, fetch, matches
from . import state, delivery
from .plugins import default_registry, process_item
from .storage import JsonStore


def fingerprint(source, timezone, destination=None):
    identity = {k: v for k, v in source.items() if k not in ("name", "enabled")}
    data = [identity, timezone]
    if destination and destination.get("type") != "email":
        data.append(destination)
    return digest(json.dumps(data, sort_keys=True, ensure_ascii=False))


def fresh(fp):
    return {"fingerprint": fp, "seeded": False, "seen": [], "pending": [], "cursor": ""}


def collect(entry, source, rows, registry=None):
    registry = registry or default_registry()
    mode = registry.sources[source["type"]].mode
    def selected(row):
        result = process_item(row, source.get("processors", []), registry)
        return result if result is not None and matches(source, result) else None
    rows = [state.checked_item(row) for row in rows]
    if not rows:
        # A valid empty feed/release list is a baseline too: its first future item is new.
        # Adapters raise on network/format errors; market-before-hour isn't a snapshot.
        updated = copy.deepcopy(entry)
        if mode != "daily":
            updated["seeded"] = True
        return updated, 0
    updated = copy.deepcopy(entry)
    first = not entry["seeded"]
    queued = []
    if mode == "snapshot":
        row = rows[0]
        if row["id"] != entry["cursor"]:
            updated["cursor"] = row["id"]
            result = selected(row)
            if (not first or source["initial"] == "send") and result is not None:
                # A -> B -> A is a new change, not an already seen feed entry.
                row = dict(result, id=digest(uuid.uuid4().hex))
                queued.append(row)
    else:
        known = set(entry["seen"]) | {p["id"] for p in entry["pending"]}
        for row in rows:
            if row["id"] not in known:
                known.add(row["id"])
                result = selected(row)
                if (not first or source["initial"] == "send" or mode == "daily") and result is not None:
                    queued.append(result)
        updated["seen"] = list(dict.fromkeys(entry["seen"] + [p["id"] for p in rows]))[-state.MAX_SEEN:]
    if len(entry["pending"]) + len(queued) > state.MAX_PENDING:
        raise ValueError("待发队列已满；先修复邮箱并处理积压，再继续获取新内容")
    updated["pending"].extend(queued)
    updated["seeded"] = True
    return updated, len(queued)


def message(source, rows, now):
    subject = f"[InfoRelay] {source['name']} · {len(rows)} 条更新"
    parts = [source["name"], "", f"整理时间：{now:%Y-%m-%d %H:%M %Z}", ""]
    for row in rows:
        parts.extend([row["title"], row["body"], row["url"],
                      "发布时间：" + row["published"] if row["published"] else "", "", "—", ""])
    parts.append("来自你配置的公开信息源。InfoRelay 不执行、核实或背书来源内容。")
    return subject, "\n".join(parts)


def run(cfg, path=".inforelay/state.json", *, now=None, fetcher=None, sender=None,
        checkpoint=None, registry=None, store=None):
    """One durable cycle. Custom transports/stores use the same queue semantics."""
    registry = registry or default_registry()
    if store is not None and checkpoint is not None:
        raise ValueError("Custom stores own persistence; checkpoint is only for JsonStore")
    storage = store or JsonStore(path)
    fetcher = fetcher or (lambda source, when: fetch(source, when, registry))
    # Preserve the legacy injected SMTP callable used by existing integrations/tests.
    if sender is not None:
        email = smtp_config(cfg)
        send = lambda subject, body, identity: sender(email, subject, body)
    else:
        send = delivery.make_sender(cfg, registry)
    with storage.lease():
        return _run(cfg, path, storage, now=now, fetcher=fetcher, send=send,
                    checkpoint=checkpoint, registry=registry)


def _run(cfg, path, storage, *, now, fetcher, send, checkpoint, registry):
    now = now or datetime.now(ZoneInfo(cfg["timezone"]))
    if now.tzinfo is None:
        raise ValueError("运行时间必须包含时区")
    now = now.astimezone(ZoneInfo(cfg["timezone"]))
    data = state.checked(storage.load())
    def persist():
        state.checked(data)
        storage.save(data)
        if checkpoint is not None:
            checkpoint(path)
    active, reports = [], []
    for source in cfg["sources"]:
        if not source["enabled"]:
            continue
        sid = source["id"]
        fp = fingerprint(source, cfg["timezone"], cfg.get("delivery"))
        entry = data["sources"].get(sid, fresh(fp))
        if entry["fingerprint"] != fp:
            if entry["pending"]:
                reports.append({"id": sid, "status": "error", "error": "配置改变但仍有待发内容；请用原配置清空队列后再修改"})
                continue
            entry = fresh(fp)
        data["sources"][sid] = entry
        active.append(source)
    persist()  # Validate local and cloud persistence before any submission.

    def load(source):
        entry = data["sources"][source["id"]]
        if registry.sources[source["type"]].mode == "daily" and digest(now.date().isoformat()) in entry["seen"]:
            return [], None
        try:
            return fetcher(source, now), None
        except Exception as error:
            # Never copy arbitrary provider messages, URLs, or credentials to public logs.
            return [], f"获取失败（{type(error).__name__}）；请运行本地 preview 检查地址和数据源"

    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(load, active))
    for source, (rows, error) in zip(active, fetched):
        sid = source["id"]
        entry = data["sources"][sid]
        original = entry
        new_count = 0
        if not error:
            try:
                entry, new_count = collect(entry, source, rows, registry)
                data["sources"][sid] = entry
            except Exception:
                error = "条目格式或队列容量异常；已保留原检查点"
        try:
            storage.save(data)
        except ValueError:
            # A full global queue must still allow already queued mail to drain.
            entry = original
            data["sources"][sid] = original
            new_count = 0
            error = "状态容量不足；本轮先处理已有待发内容"
            storage.save(data)
        if checkpoint is not None:
            checkpoint(path)  # Push encrypted snapshots before SMTP starts.
        batch = entry["pending"][:cfg["batch_size"]]
        sent = 0
        if batch:
            try:
                subject, body = message(source, batch, now)
                send(subject, body, delivery.batch_id(sid, batch))
            except Exception as failure:
                error = (error + "；" if error else "") + f"发送失败（{type(failure).__name__}）；待发内容已保留"
            else:
                sent = len(batch)
                entry["pending"] = entry["pending"][sent:]
                # A write failure is fatal; continuing could send with stale checkpoints.
                persist()
        report = {"id": sid, "status": "error" if error else "ok", "new": new_count,
                  "sent": sent, "pending": len(entry["pending"])}
        if error:
            report["error"] = error
        reports.append(report)
    return reports
