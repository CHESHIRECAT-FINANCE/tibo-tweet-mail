"""Built-in adapters use the same public contracts as third-party extensions."""
from __future__ import annotations

import re
from pathlib import Path
from .plugins import SourcePlugin, ProcessorPlugin, ChannelPlugin


def source_settings(kind):
    def validate(source):
        from .config import text
        from .net import public_url
        if kind in ("rss", "webpage"):
            source["url"] = public_url(text(source.get("url"), "url", 2000))
        elif kind == "github":
            if not isinstance(source.get("repository"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["repository"]):
                raise ValueError("repository must use owner/repository")
        elif kind == "x":
            if not isinstance(source.get("handle"), str) or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", source["handle"]):
                raise ValueError("handle must be an X username without @")
        elif kind == "market":
            source.setdefault("hour", 8)
            if type(source["hour"]) is not int or not 0 <= source["hour"] <= 23:
                raise ValueError("hour must be an integer from 0 to 23")
        if source.get("options"):
            raise ValueError("Built-in sources do not accept extra options")
    return validate


def _empty(options):
    if options:
        raise ValueError("This extension has no options")


def _file_options(options):
    if set(options) != {"directory"} or not isinstance(options["directory"], str) or not options["directory"].strip():
        raise ValueError("file channel requires options.directory")


def file_delivery(options, subject, body, batch_id):
    from tibo_tweet_mail import save_json
    directory = Path(options["directory"]).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    # Stable filename makes retries overwrite the same batch, never append duplicates.
    save_json(directory / (batch_id + ".json"), {"id": batch_id, "subject": subject, "body": body})


def _prefix_options(options):
    from .config import text
    if set(options) != {"text"}:
        raise ValueError("prefix requires options.text")
    text(options["text"], "prefix.text", 100)


def register(registry):
    from . import sources
    for kind in ("rss", "webpage", "github", "x", "market"):
        # Resolve at invocation time so adapters remain replaceable in offline tests.
        registry.source(kind, SourcePlugin(lambda source, now, k=kind: sources.ADAPTERS[k](source, now),
                        source_settings(kind), "snapshot" if kind == "webpage" else "daily" if kind == "market" else "stream"))
    registry.channel("email", ChannelPlugin(_email_send, _empty, _email_diagnose))
    registry.channel("file", ChannelPlugin(file_delivery, _file_options))
    registry.processor("prefix", ProcessorPlugin(lambda row, opts: dict(row, title=(opts["text"] + row["title"])[:300]), _prefix_options))


def _email_send(options, subject, body, batch_id):
    # Email credentials live in cfg.email; delivery.make_sender binds them.
    raise RuntimeError("Email channel must be bound using make_sender")


def _email_diagnose(options):
    raise RuntimeError("Email channel must be bound using diagnose")
