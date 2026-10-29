"""Bind a destination once per run; adapters return only after durable acceptance."""
from .plugins import default_registry
from .sources import digest


def spec(cfg):
    return cfg.get("delivery", {"type": "email", "options": {}})


def batch_id(source_id, rows):
    return digest(source_id + "\n" + "\n".join(row["id"] for row in rows))


def make_sender(cfg, registry=None):
    registry = registry or default_registry()
    destination = spec(cfg)
    if destination["type"] == "email":
        from .config import smtp_config
        from tibo_tweet_mail import deliver
        email = smtp_config(cfg)
        return lambda subject, body, identity: deliver(email, subject, body)
    plugin = registry.channels[destination["type"]]
    return lambda subject, body, identity: plugin.send(destination.get("options", {}), subject, body, identity)


def diagnose(cfg, registry=None):
    registry = registry or default_registry()
    destination = spec(cfg)
    if destination["type"] == "email":
        from .config import smtp_config
        from tibo_tweet_mail import smtp_session
        with smtp_session(smtp_config(cfg)):
            pass
        return "Email connection, TLS and login OK; no message sent"
    plugin = registry.channels[destination["type"]]
    if plugin.diagnose:
        plugin.diagnose(destination.get("options", {}))
        return "Destination check OK; no notification sent"
    return "Destination settings valid; no remote connection check is provided"
