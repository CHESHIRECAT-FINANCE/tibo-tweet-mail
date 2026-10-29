"""Validate user settings before touching a source, mailbox, or checkpoint."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .plugins import default_registry

KINDS = {"rss", "webpage", "github", "x", "market"}
SOURCE_FIELDS = {"id", "name", "type", "enabled", "url", "repository", "handle",
                 "keywords", "exclude", "match", "initial", "prereleases", "reset_only",
                 "include_reposts", "include_replies", "include_quotes", "hour", "options", "processors"}
EMAIL_FIELDS = {"smtp_host", "smtp_port", "smtp_user", "smtp_password", "to"}


def text(value, label, maximum=200):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"{label} 必须是非空、无控制字符的文本")
    return value.strip()


def validate(raw, registry=None):
    registry = registry or default_registry()
    if not isinstance(raw, dict) or set(raw) - {"version", "timezone", "sources", "email", "batch_size", "delivery"}:
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
        if not isinstance(kind, str) or kind not in registry.sources:
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
        if "options" in source and not isinstance(source["options"], dict):
            raise ValueError("source.options must be an object")
        registry.sources[kind].validate(source)
        pipeline = source.get("processors", [])
        if not isinstance(pipeline, list) or len(pipeline) > 10:
            raise ValueError("processors must be a list of at most 10 steps")
        for step in pipeline:
            validate_extension(step, registry.processors, "processor")
    if "delivery" in cfg:
        validate_extension(cfg["delivery"], registry.channels, "delivery")
    email = cfg.setdefault("email", {})
    if not isinstance(email, dict) or set(email) - EMAIL_FIELDS:
        raise ValueError("email 包含未知字段")
    return cfg


def validate_extension(spec, plugins, label):
    if not isinstance(spec, dict) or set(spec) - {"type", "options"}:
        raise ValueError(f"Invalid {label} definition")
    if not isinstance(spec.get("type"), str) or spec["type"] not in plugins:
        raise ValueError(f"Unknown {label}; explicitly register its plugin first")
    if not isinstance(spec.get("options", {}), dict):
        raise ValueError(f"{label}.options must be an object")
    plugins[spec["type"]].validate(spec.get("options", {}))


def smtp_config(cfg):
    from tibo_tweet_mail import validate_config
    email = dict(cfg.get("email", {}))
    if not email.get("smtp_password"):
        email["smtp_password"] = os.environ.get("INFO_RELAY_SMTP_PASSWORD", "")
    return validate_config({"handle": "inforelay", **email}, require_smtp=True)


def load(path=None, registry=None):
    try:
        if path is not None:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        elif "INFO_RELAY_CONFIG" in os.environ:
            raw = json.loads(os.environ["INFO_RELAY_CONFIG"])
        else:
            raw = json.loads(Path("subscriptions.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ValueError("无法读取配置。请设置 INFO_RELAY_CONFIG 或使用 --config 指定 JSON 文件") from None
    return validate(raw, registry=registry)
