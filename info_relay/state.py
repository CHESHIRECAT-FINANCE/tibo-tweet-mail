"""Atomic local checkpoints; cloud persistence encrypts this entire schema."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tibo_tweet_mail import save_json
from .net import public_url

MAX_PENDING = 500
MAX_SEEN = 5000
MAX_STATE_BYTES = 20_000_000


def hashed(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def checked_item(row):
    if not isinstance(row, dict) or set(row) != {"id", "title", "body", "url", "published"}:
        raise ValueError("待发条目格式无效")
    if not hashed(row["id"]):
        raise ValueError("待发条目 ID 无效")
    for key, limit in (("title", 300), ("body", 10000), ("url", 2000), ("published", 100)):
        if not isinstance(row[key], str) or len(row[key]) > limit:
            raise ValueError("待发条目字段无效")
    if not row["title"] or any(ord(c) < 32 for c in row["title"]):
        raise ValueError("待发标题无效")
    if row["url"]:
        public_url(row["url"])
    return dict(row)


def empty():
    return {"version": 1, "sources": {}}


def checked(data):
    if (not isinstance(data, dict) or set(data) != {"version", "sources"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["sources"], dict) or len(data["sources"]) > 200):
        raise ValueError("订阅检查点格式无效；请恢复备份，勿直接删除状态")
    for sid, entry in data["sources"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", sid):
            raise ValueError("检查点信息源 ID 无效")
        if (not isinstance(entry, dict)
                or set(entry) != {"fingerprint", "seeded", "seen", "pending", "cursor"}
                or not hashed(entry["fingerprint"]) or type(entry["seeded"]) is not bool
                or not isinstance(entry["seen"], list) or len(entry["seen"]) > MAX_SEEN
                or any(not hashed(x) for x in entry["seen"])
                or len(set(entry["seen"])) != len(entry["seen"])
                or not isinstance(entry["pending"], list) or len(entry["pending"]) > MAX_PENDING
                or (entry["cursor"] != "" and not hashed(entry["cursor"]))):
            raise ValueError("信息源检查点格式无效")
        ids = [checked_item(row)["id"] for row in entry["pending"]]
        if len(ids) != len(set(ids)) or (ids and not entry["seeded"]):
            raise ValueError("待发队列状态不一致")
    return data


def read(path):
    path = Path(path)
    if not path.exists():
        return empty()
    if path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("订阅状态过大，请减少订阅或处理待发队列")
    try:
        return checked(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise ValueError("订阅状态无法读取或已损坏；请恢复备份，勿直接删除状态") from None


def write(path, data):
    checked(data)
    if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_STATE_BYTES:
        raise ValueError("订阅状态超过容量限制，暂不继续发送")
    save_json(Path(path), data)
