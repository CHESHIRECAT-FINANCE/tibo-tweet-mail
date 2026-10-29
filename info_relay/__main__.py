"""python -m info_relay: configure, preview, check, or run your subscriptions."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import signal
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, config, engine, sources, delivery
from .plugins import default_registry, process_item

_STOP = threading.Event()


def mask_settings(cfg):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    email = cfg.get("email", {})
    for value in [email.get(k, "") for k in ("smtp_user", "smtp_password", "to")] + [
            os.environ.get("INFO_RELAY_SMTP_PASSWORD", ""), os.environ.get("INFO_RELAY_STATE_KEY", "")]:
        if isinstance(value, str) and value:
            for v in (value, "".join(value.split())):
                print("::add-mask::" + v.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="InfoRelay：把你关心的公开信息送到邮箱")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", choices=["init", "check", "preview", "diagnose", "test-email", "run", "keygen", "cloud-restore", "cloud-save", "serve", "watch", "plugins"])
    parser.add_argument("--config", help="本地 JSON 配置路径；默认读取 INFO_RELAY_CONFIG 或 subscriptions.json")
    parser.add_argument("--state", default=os.environ.get("INFO_RELAY_STATE_PATH", ".inforelay/state.json"))
    parser.add_argument("--plugin", action="append", default=[], help="Explicitly load a trusted installed plugin module")
    parser.add_argument("--data-dir", default=".inforelay", help="Local console private data directory")
    parser.add_argument("--port", type=int, default=8766, help="Local console port (127.0.0.1 only)")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--storage", choices=["json", "sqlite"], default="json", help="Checkpoint backend for run/watch")
    parser.add_argument("--interval", type=int, default=600, help="Seconds between completed watch cycles, 60–86400")
    args = parser.parse_args(argv)
    registry = default_registry()
    for module in list(filter(None, os.environ.get("INFO_RELAY_PLUGINS", "").split(","))) + args.plugin:
        registry.load(module.strip())
    if args.command == "plugins":
        print(json.dumps({"api_version": 1, "sources": sorted(registry.sources),
                          "processors": sorted(registry.processors), "channels": sorted(registry.channels)}, indent=2))
        return 0
    if args.command == "serve":
        from .server import serve
        serve(args.data_dir, args.port, not args.no_browser, registry, stop_event=_STOP)
        return 0

    if args.command == "keygen":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return 0
    if args.command == "init":
        path = Path(args.config or "subscriptions.json")
        example = Path(__file__).resolve().parent / "example.json"
        # Exclusive creation protects an existing setup from accidental replacement.
        with path.open("x", encoding="utf-8") as output:
            output.write(example.read_text(encoding="utf-8"))
        path.chmod(0o600)
        print("已生成配置；编辑信息源后运行 preview，邮箱配置完成后运行 diagnose。")
        return 0
    if args.command in ("cloud-restore", "cloud-save"):
        from . import cloud
        directory = os.environ.get("INFO_RELAY_CLOUD_DIR")
        if not directory:
            raise ValueError("缺少 INFO_RELAY_CLOUD_DIR")
        (cloud.restore if args.command == "cloud-restore" else cloud.save)(directory, args.state)
        print("加密检查点同步成功")
        return 0
    cfg = config.load(args.config, registry)
    mask_settings(cfg)
    if args.command == "check":
        print(f"InfoRelay {__version__}：配置有效，{sum(s['enabled'] for s in cfg['sources'])} 个已启用信息源")
        return 0
    now = datetime.now(ZoneInfo(cfg["timezone"]))
    if args.command == "test-email":
        if delivery.spec(cfg)["type"] != "email":
            raise ValueError("test-email requires the email destination")
        from tibo_tweet_mail import deliver
        deliver(config.smtp_config(cfg), "[InfoRelay] 测试邮件", "如果你收到这封邮件，说明 InfoRelay 的邮件通道已连通。\n" + now.isoformat())
        print("测试邮件已提交至服务器；请检查收件箱和垃圾邮件。")
        return 0
    if args.command in ("run", "watch"):
        if args.command == "watch" and not 60 <= args.interval <= 86400:
            raise ValueError("--interval must be between 60 and 86400 seconds")
        store = None
        if args.storage == "sqlite":
            from .storage import SQLiteStore
            if os.environ.get("INFO_RELAY_CLOUD_DIR"):
                raise ValueError("Cloud checkpoint sync requires JSON storage")
            store = SQLiteStore(args.state)
        checkpoint = None
        if os.environ.get("INFO_RELAY_CLOUD_DIR"):
            from .cloud import save
            checkpoint = lambda path: save(os.environ["INFO_RELAY_CLOUD_DIR"], path)
        reports = engine.run(cfg, args.state, now=now, checkpoint=checkpoint, registry=registry, store=store)
        for report in reports:
            print(json.dumps(report, ensure_ascii=False))
        if args.command == "run":
            return int(any(report["status"] == "error" for report in reports))
        while not _STOP.wait(args.interval):
            cfg = config.load(args.config, registry)
            reports = engine.run(cfg, args.state, checkpoint=checkpoint, registry=registry, store=store)
            for report in reports:
                print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0
    errors = 0
    if args.command == "diagnose":
        try:
            print(delivery.diagnose(cfg, registry))
        except Exception as error:
            print(f"Destination check failed ({type(error).__name__}); check settings and network")
            errors += 1
    for source in cfg["sources"]:
        if not source["enabled"]:
            continue
        try:
            # Preview/diagnosis should inspect a market source even before its due hour.
            probe = dict(source, hour=0) if source["type"] == "market" else source
            rows = sources.fetch(probe, now, registry)
            processed = [process_item(row, source.get("processors", []), registry) for row in rows]
            selected = [row for row in processed if row is not None and sources.matches(source, row)]
            print(f"{source['id']}: 获取 {len(rows)} 条，筛选匹配 {len(selected)} 条")
            if args.command == "preview":
                for row in selected[-5:]:
                    print(json.dumps({"title": row["title"], "url": row["url"], "excerpt": row["body"][:400]}, ensure_ascii=False))
        except Exception as error:
            print(f"{source['id']}: 获取失败（{type(error).__name__}）；请核对地址和信息源可用性")
            errors += 1
    print("检查完成；未发送邮件，未修改检查点")
    return int(bool(errors))


def entrypoint():
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_args: _STOP.set())
    try:
        sys.exit(main())
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(130)
    except Exception as error:
        # Settings and source payloads must not end up in public workflow tracebacks.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("InfoRelay 运行失败：" + message, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
