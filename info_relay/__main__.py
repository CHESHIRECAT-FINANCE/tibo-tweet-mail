"""python -m info_relay: configure, preview, check, or run your subscriptions."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, config, engine, sources


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
    parser.add_argument("command", choices=["init", "check", "preview", "diagnose", "test-email", "run", "keygen", "cloud-restore", "cloud-save"])
    parser.add_argument("--config", help="本地 JSON 配置路径；默认读取 INFO_RELAY_CONFIG 或 subscriptions.json")
    parser.add_argument("--state", default=os.environ.get("INFO_RELAY_STATE_PATH", ".inforelay/state.json"))
    args = parser.parse_args(argv)
    if args.command == "keygen":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return 0
    if args.command == "init":
        path = Path(args.config or "subscriptions.json")
        example = Path(__file__).resolve().parent.parent / "subscriptions.example.json"
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
    cfg = config.load(args.config)
    mask_settings(cfg)
    if args.command == "check":
        print(f"InfoRelay {__version__}：配置有效，{sum(s['enabled'] for s in cfg['sources'])} 个已启用信息源")
        return 0
    now = datetime.now(ZoneInfo(cfg["timezone"]))
    if args.command == "test-email":
        from tibo_tweet_mail import deliver
        deliver(config.smtp_config(cfg), "[InfoRelay] 测试邮件", "如果你收到这封邮件，说明 InfoRelay 的邮件通道已连通。\n" + now.isoformat())
        print("测试邮件已提交至服务器；请检查收件箱和垃圾邮件。")
        return 0
    if args.command == "run":
        checkpoint = None
        if os.environ.get("INFO_RELAY_CLOUD_DIR"):
            from .cloud import save
            checkpoint = lambda path: save(os.environ["INFO_RELAY_CLOUD_DIR"], path)
        reports = engine.run(cfg, args.state, now=now, checkpoint=checkpoint)
        for report in reports:
            print(json.dumps(report, ensure_ascii=False))
        return int(any(report["status"] == "error" for report in reports))
    errors = 0
    if args.command == "diagnose":
        from tibo_tweet_mail import smtp_session
        try:
            with smtp_session(config.smtp_config(cfg)):
                pass
            print("邮箱连接、TLS 和登录通过（未发送邮件）")
        except Exception as error:
            print(f"邮箱检查失败（{type(error).__name__}）；请核对邮箱配置和网络")
            errors += 1
    for source in cfg["sources"]:
        if not source["enabled"]:
            continue
        try:
            # Preview/diagnosis should inspect a market source even before its due hour.
            probe = dict(source, hour=0) if source["type"] == "market" else source
            rows = sources.fetch(probe, now)
            selected = [row for row in rows if sources.matches(source, row)]
            print(f"{source['id']}: 获取 {len(rows)} 条，筛选匹配 {len(selected)} 条")
            if args.command == "preview":
                for row in selected[-5:]:
                    print(json.dumps({"title": row["title"], "url": row["url"], "excerpt": row["body"][:400]}, ensure_ascii=False))
        except Exception as error:
            print(f"{source['id']}: 获取失败（{type(error).__name__}）；请核对地址和信息源可用性")
            errors += 1
    print("检查完成；未发送邮件，未修改检查点")
    return int(bool(errors))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(130)
    except Exception as error:
        # Settings and source payloads must not end up in public workflow tracebacks.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("InfoRelay 运行失败：" + message, file=sys.stderr)
        sys.exit(1)
