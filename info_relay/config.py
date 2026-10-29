"""Validate user settings before touching a source, mailbox, or checkpoint."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .net import public_url

KINDS = {"rss", "webpage", "github", "x", "market"}
SOURCE_FIELDS = {"id", "name", "type", "enabled", "url", "repository", "handle",
                 "keywords", "exclude", "match", "initial", "prereleases", "reset_only",
                 "include_reposts", "include_replies", "include_quotes", "hour"}
EMAIL_FIELDS = {"smtp_host", "smtp_port", "smtp_user", "smtp_password", "to"}


def text(value, label, maximum=200):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"{label} 必须是非空、无控制字符的文本")
    return value.strip()


def validate(raw):
    if not isinstance(raw, dict) or set(raw) - {"version", "timezone", "sources", "email", "batch_size"}:
        raise ValueError("配置必须是对象，且不能包含未知顶层字段")
    cfg = copy.deepcopy(raw)
    if type(cfg.get("version", 1)) is not int or cfg.get("version", 1) != 1:
        raise ValueError("只支持 version: 1")
    cfg["version"] = 1
    cfg.setdefault("timezone", "Asia/Shanghai")
    try:
        ZoneInfo(text(cfg["timezone"], "timezone"))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone 必须是有效时区，例如 Asia/Shanghai") from None
    cfg.setdefault("batch_size", 20)
    if type(cfg["batch_size"]) is not int or not 1 <= cfg["batch_size"] <= 50:
        raise ValueError("batch_size 必须是 1 到 50 的整数")
    sources = cfg.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError("请配置 1 到 20 个信息源")
    ids = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) - SOURCE_FIELDS:
            raise ValueError("信息源包含未知字段")
        sid = source.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", sid) or sid in ids:
            raise ValueError("信息源 id 必须唯一，只能包含字母、数字、下划线和短横线")
        ids.add(sid)
        kind = source.get("type")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError(f"{sid}: 不支持的信息源类型")
        source["name"] = text(source.get("name", sid), f"{sid}.name", 80)
        for key, default in (("enabled", True), ("prereleases", False), ("reset_only", False),
                             ("include_reposts", True), ("include_replies", False), ("include_quotes", False)):
            source.setdefault(key, default)
            if type(source[key]) is not bool:
                raise ValueError(f"{sid}.{key} 必须为 true 或 false")
        source.setdefault("initial", "skip")
        source.setdefault("match", "any")
        if source["initial"] not in ("skip", "send") or source["match"] not in ("any", "all"):
            raise ValueError(f"{sid}: initial 或 match 无效")
        for key in ("keywords", "exclude"):
            source.setdefault(key, [])
            if not isinstance(source[key], list) or len(source[key]) > 30:
                raise ValueError(f"{sid}.{key} 必须是最多 30 个词的列表")
            source[key] = [text(word, f"{sid}.{key}", 100) for word in source[key]]
        if kind in ("rss", "webpage"):
            source["url"] = public_url(text(source.get("url"), f"{sid}.url", 2000))
        if kind == "github":
            repo = source.get("repository", "")
            if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                raise ValueError(f"{sid}.repository 使用 owner/repository 格式")
        if kind == "x":
            handle = source.get("handle", "")
            if not isinstance(handle, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
                raise ValueError(f"{sid}.handle 使用不含 @ 的 X 用户名")
        if kind == "market":
            source.setdefault("hour", 8)
            if type(source["hour"]) is not int or not 0 <= source["hour"] <= 23:
                raise ValueError(f"{sid}.hour 必须为 0 到 23 的整数")
    email = cfg.setdefault("email", {})
    if not isinstance(email, dict) or set(email) - EMAIL_FIELDS:
        raise ValueError("email 包含未知字段")
    return cfg


def smtp_config(cfg):
    from tibo_tweet_mail import validate_config
    email = dict(cfg.get("email", {}))
    if not email.get("smtp_password"):
        email["smtp_password"] = os.environ.get("INFO_RELAY_SMTP_PASSWORD", "")
    return validate_config({"handle": "inforelay", **email}, require_smtp=True)


def load(path=None):
    try:
        if path is not None:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        elif "INFO_RELAY_CONFIG" in os.environ:
            raw = json.loads(os.environ["INFO_RELAY_CONFIG"])
        else:
            raw = json.loads(Path("subscriptions.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ValueError("无法读取配置。请设置 INFO_RELAY_CONFIG 或使用 --config 指定 JSON 文件") from None
    return validate(raw)
