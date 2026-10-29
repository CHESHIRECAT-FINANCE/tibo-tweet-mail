#!/usr/bin/env python3
"""Forward reset notices from an X account to an email inbox.

Polls the public FxTwitter timeline. The first successful run only records
posts that already exist. Later runs mail a post only when it says the
usage limit is resetting.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import smtplib
import socket
import ssl
import tempfile
import plistlib
from contextlib import contextmanager
import sys
import time
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from http_client import fetch_json
from cloud_state import checked_post, checked_state

API_URL = "https://api.fxtwitter.com/2/profile/{handle}/statuses?count=20"
CONFIG_PATH = Path.home() / ".config" / "tibo-tweet-mail" / "config.json"
STATE_PATH = Path(os.environ.get("TIBO_STATE_DIR") or Path.home() / ".local" / "share" / "tibo-tweet-mail") / "state.json"
PLIST_LABEL = os.environ.get("TIBO_LAUNCH_AGENT_LABEL", "com.tibo-tweet-mail")
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "tibo-tweet-mail.log"
SHANGHAI = ZoneInfo("Asia/Shanghai")
SEEN_LIMIT = 1000
RESET_PATTERNS = (
    r"\breset day\b",
    r"\bit'?s reset\b",
    r"\blimits? reset\b",
    r"\busage reset\b",
    r"\breset(?:ting|s)? (?:today|tonight|now|soon)\b",
    r"\band reset\b",
    r"重置",
)


def log(message: str) -> None:
    stamp = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", flush=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def screen_name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("screen_name") or "")
    return ""


def is_repost(item: dict, handle: str) -> bool:
    """A repost shows the original author, with this account in reposted_by."""
    author = screen_name(item.get("author")).lower()
    watched = handle.lower()
    return bool(author) and author != watched


def is_forwardable(item: dict, config: dict) -> bool:
    """Keep original posts, plus reposts when include_reposts is set.

    Replies and quote posts stay off unless their own flags say otherwise.
    """
    if not isinstance(item, dict):
        return False
    if item.get("type") not in (None, "status"):
        return False

    handle = str(config.get("handle") or "")
    if is_repost(item, handle) and not config.get("include_reposts"):
        return False

    reply = item.get("replying_to")
    if isinstance(reply, dict) and screen_name(reply):
        if not config.get("include_replies"):
            return False

    if item.get("quote") and not config.get("include_quotes"):
        return False
    return True


def is_reset_notice(text: str) -> bool:
    """A reset notice says the usage limit is resetting, not a normal Day post."""
    normalized = " ".join((text or "").replace("’", "'").replace("‘", "'").split()).lower()
    if re.search(r"\basking\b.{0,40}\bfor resets?\b", normalized):
        return False
    return any(re.search(pattern, normalized) for pattern in RESET_PATTERNS)


def created_at(item: dict) -> datetime:
    raw = item.get("created_timestamp")
    if type(raw) in (int, float) and math.isfinite(raw):
        seconds = raw / 1000 if raw >= 1_000_000_000_000 else raw
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    text = str(item.get("created_at") or "")
    if text:
        from email.utils import parsedate_to_datetime
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (ValueError, TypeError, OverflowError):
                parsed = None
        if parsed is not None:
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    # Snowflake timestamps provide a stable fallback instead of silently using today.
    post_id = str(item.get('id') or '')
    if re.fullmatch(r'[0-9]{15,20}', post_id):
        return datetime.fromtimestamp(((int(post_id) >> 22) + 1288834974657) / 1000, timezone.utc)
    raise ValueError('帖子缺少有效发布时间')


def fetch_posts(handle: str) -> list[dict]:
    url = API_URL.format(handle=urllib.request.quote(handle))
    payload = fetch_json(url, source="时间线请求失败", retry_not_found=True)
    if not isinstance(payload, dict) or payload.get("code") not in (200, None):
        raise RuntimeError("时间线返回异常，保留原有检查点")
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("时间线结果格式无法识别")
    posts = {}
    for item in results:
        if not isinstance(item, dict) or item.get("type") not in (None, "status"):
            continue
        post_id = str(item.get("id") or "")
        if not post_id.isascii() or not post_id.isdigit() or not isinstance(item.get("text", ""), str):
            raise RuntimeError("时间线帖子格式异常，保留原有检查点")
        posts[post_id] = item
    return list(posts.values())


def subject_line(display_name: str, text: str, *, repost: bool = False, reset: bool = False) -> str:
    first = " ".join((text or "").split())
    if not first:
        first = "新帖子"
    if len(first) > 90:
        first = first[:89].rstrip() + "…"
    if repost and reset:
        label = f"{display_name} 转发 重置"
    elif repost:
        label = f"{display_name} 转发"
    elif reset:
        label = f"{display_name} 重置"
    else:
        label = display_name
    return f"[{label}] {first}"


def message_body(item: dict, config: dict) -> str:
    text = (item.get("text") or "").strip() or "（这条帖子没有正文）"
    when = created_at(item).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M 北京时间")
    url = item.get("url") or ""
    handle = str(config.get("handle") or "")
    display_name = str(config.get("display_name") or handle or "X")
    if is_repost(item, handle):
        author = screen_name(item.get("author")) or "某人"
        lead = f"{display_name} 转发了 @{author} 的帖子\n\n"
    else:
        lead = ""
    return f"{lead}{text}\n\n{url}\n{when}\n"


def smtp_error(error: Exception, stage: str) -> str:
    # SMTP replies can contain addresses, credentials or remote-controlled text.
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return f"SMTP 登录失败（{error.smtp_code}）：检查账号和应用专用密码"
    if isinstance(error, ssl.SSLCertVerificationError):
        return "SMTP TLS 证书校验失败"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "SMTP 收件地址被拒绝"
    if isinstance(error, smtplib.SMTPResponseException):
        return f"SMTP {stage}失败（{error.smtp_code}）"
    if isinstance(error, socket.gaierror):
        return "SMTP DNS 解析失败"
    if isinstance(error, smtplib.SMTPNotSupportedError):
        return f"SMTP 不支持所需功能（{stage}）"
    return f"SMTP 在{stage}阶段中断或超时（{type(error).__name__}）"


def smtp_connection_lost(error: Exception) -> bool:
    # SMTPException itself inherits OSError, including explicit server rejections.
    return (isinstance(error, smtplib.SMTPServerDisconnected)
            or (isinstance(error, OSError) and not isinstance(error, smtplib.SMTPException)))


@contextmanager
def smtp_session(config: dict):
    host = config.get("smtp_host", "smtp.gmail.com")
    port = int(config.get("smtp_port", 587))
    password = config.get("smtp_password", "")
    # Gmail displays app passwords in groups; other providers may use real spaces.
    if host == "smtp.gmail.com":
        password = "".join(password.split())
    if not password or not config.get("smtp_user") or not config.get("to"):
        raise ValueError("邮箱配置不完整：需要 smtp_user、smtp_password 和 to")
    smtp = None
    for attempt in range(3):
        stage = "连接"
        try:
            context = ssl.create_default_context()
            if port == 465:
                smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
            else:
                smtp = smtplib.SMTP(host, port, timeout=15)
                stage = "TLS 握手"
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            stage = "登录"
            smtp.login(config["smtp_user"], password)
            break
        except (OSError, smtplib.SMTPException) as error:
            if smtp is not None:
                smtp.close()
            retry = smtp_connection_lost(error)
            if isinstance(error, (ssl.SSLCertVerificationError, smtplib.SMTPAuthenticationError)):
                retry = False
            if not retry or attempt == 2:
                raise RuntimeError(smtp_error(error, stage)) from None
            time.sleep(2 ** attempt)
    try:
        yield smtp
    finally:
        # A QUIT failure after server acceptance must not cause a duplicate send.
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            pass
        finally:
            smtp.close()


def deliver(config: dict, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.get("smtp_user", "")
    message["To"] = config.get("to", "")
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    with smtp_session(config) as smtp:
        try:
            refused = smtp.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        except (OSError, smtplib.SMTPException) as error:
            detail = smtp_error(error, "提交邮件")
            if smtp_connection_lost(error):
                detail += "；服务器是否接收未知，本轮不立即重发"
            raise RuntimeError(detail) from None


def send_email(config: dict, item: dict, *, reset: bool = False) -> None:
    handle = str(config.get("handle") or "")
    display_name = str(config.get("display_name") or handle or "X")
    deliver(
        config,
        subject_line(display_name, item.get("text") or "", repost=is_repost(item, handle), reset=reset),
        message_body(item, config),
    )


def remember(state: dict, post_id: str) -> None:
    seen = [str(item) for item in state.get("seen") or [] if str(item) != post_id]
    seen.append(post_id)
    state["seen"] = seen[-SEEN_LIMIT:]
    state["seeded"] = True


def acquire_lock(path: Path | None = None):
    import fcntl

    path = path or STATE_PATH.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def selected_posts(config: dict) -> list[dict]:
    posts = [item for item in fetch_posts(str(config["handle"])) if is_forwardable(item, config)]
    posts.sort(key=created_at)
    return posts


def run_once(config: dict) -> int:
    lock = acquire_lock()
    if lock is None:
        log("上一次检查还在进行，跳过")
        return 0
    try:
        state = checked_state("state.json", load_json(STATE_PATH, {"seeded": False, "seen": []}))
        fetch_error = None
        try:
            posts = selected_posts(config)
        except Exception as error:
            if not state.get('pending'):
                raise
            posts, fetch_error = [], error
        seen = {str(item) for item in state.get("seen") or []}
        if not state.get("seeded"):
            if not posts:
                log("时间线为空，等待有效帖子后再初始化")
                return 0
            for item in posts:
                remember(state, str(item.get("id")))
            save_json(STATE_PATH, state)
            log(f"已记录现有帖子 {len(posts)} 条，不发送历史帖子")
            return 0

        pending = state.get('pending', [])
        queued = {item['id'] for item in pending}
        fresh = [item for item in posts if str(item.get("id")) not in seen | queued]
        if not fresh and not pending:
            log("没有新帖")
            return 0
        # Save a public snapshot before SMTP, so failed notices survive the 20-post window.
        for item in fresh:
            post_id = str(item['id'])
            if post_id in seen | queued:
                continue
            if is_reset_notice(item.get('text') or ''):
                pending.append(checked_post(dict(item, created_timestamp=created_at(item).timestamp())))
                queued.add(post_id)
            else:
                remember(state, post_id)
                seen.add(post_id)
        state['pending'] = pending
        state = checked_state('state.json', state)
        save_json(STATE_PATH, state)
        pending = state.get('pending', [])
        had_pending = bool(pending)
        while pending:
            item = pending[0]
            send_email(config, item, reset=True)
            pending.pop(0)
            remember(state, item['id'])
            save_json(STATE_PATH, state)
            log(f"已提交重置通知 {item['id']} 至邮件服务器")
        if fetch_error:
            raise fetch_error
        if not had_pending:
            log("没有需要通知的重置")
        return 0
    finally:
        lock.close()


def preview(config: dict) -> int:
    posts = selected_posts(config)
    if not posts:
        print("这一页没有符合条件的帖子")
        return 0
    for item in posts:
        when = created_at(item).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
        text = " ".join((item.get("text") or "").split())
        print(f"{when}  {text[:140]}")
        print(f"  {item.get('url')}")
    return 0


def send_test(config: dict) -> int:
    sample = {
        "id": "test",
        "text": "这是一封测试信。Tibo 之后发布的用量重置通知会用同样的方式送到这个邮箱。",
        "url": "https://x.com/thsottiaux",
        "created_timestamp": time.time(),
    }
    send_email(config, sample)
    log("测试信已提交至邮件服务器")
    return 0


def plist_text(label: str, command: str, interval: int | None = None, *, hours: tuple[int, ...] = ()) -> str:
    script = Path(__file__).resolve()
    payload = {
        "Label": label,
        "ProgramArguments": [sys.executable, str(script), command],
        "WorkingDirectory": str(script.parent),
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
        "EnvironmentVariables": {"TIBO_LAUNCH_AGENT_LABEL": label},
    }
    if hours:
        payload["StartCalendarInterval"] = [{"Hour": hour, "Minute": 0} for hour in hours]
    else:
        payload["StartInterval"] = interval or 300
    return plistlib.dumps(payload).decode("utf-8")


def launch_agent(label: str, path: Path) -> None:
    import os
    import subprocess

    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False, capture_output=True)
    subprocess.run(["launchctl", "enable", f"{domain}/{label}"], check=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)


def agent_settings() -> tuple[str, Path]:
    """Reuse an existing installation's label instead of creating a second scheduler."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', PLIST_LABEL):
        raise ValueError('定时任务标签格式无效')
    if 'TIBO_LAUNCH_AGENT_LABEL' in os.environ:
        return PLIST_LABEL, PLIST_PATH
    matches = []
    script = Path(__file__).resolve()
    for path in PLIST_PATH.parent.glob('*tibo-tweet-mail*.plist'):
        try:
            data = plistlib.loads(path.read_bytes())
            if not isinstance(data, dict):
                continue
            args = data.get('ProgramArguments', [])
            label = data.get('Label')
            if (isinstance(args, list) and len(args) >= 3 and isinstance(args[1], str)
                    and Path(args[1]).resolve() == script and args[2] == 'run'
                    and isinstance(label, str) and label == path.stem):
                matches.append((label, path))
        except (ValueError, TypeError, OSError, plistlib.InvalidFileException):
            continue
    if len(matches) > 1:
        raise ValueError('发现多个本项目定时任务，请用 TIBO_LAUNCH_AGENT_LABEL 指定要管理的任务')
    return matches[0] if matches else (PLIST_LABEL, PLIST_PATH)


def install(config_template: Path) -> int:
    import os

    label, path = agent_settings()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(config_template.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(CONFIG_PATH, 0o600)
        log(f"已创建配置 {CONFIG_PATH}")
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist_text(label, "run", 300), encoding="utf-8")
    launch_agent(label, path)
    log("定时任务已安装，每 5 分钟检查一次重置通知")
    return 0


def uninstall() -> int:
    import os
    import subprocess

    label, path = agent_settings()
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
    if path.exists():
        path.unlink()
    log("定时任务已移除")
    return 0


def load_config() -> dict:
    # GitHub Actions supplies the complete JSON as a secret, without a disk file.
    if "TIBO_MAIL_CONFIG" in os.environ:
        try:
            config = json.loads(os.environ["TIBO_MAIL_CONFIG"])
        except (ValueError, TypeError):
            raise SystemExit("TIBO_MAIL_CONFIG 必须是有效的 JSON 配置") from None
    else:
        if not CONFIG_PATH.exists():
            raise SystemExit(f"还没有配置文件：{CONFIG_PATH}")
        try:
            config = load_json(CONFIG_PATH, {})
        except (ValueError, OSError):
            raise SystemExit("本地配置无法读取或不是有效 JSON") from None
    if not isinstance(config, dict):
        raise SystemExit("邮箱配置必须是 JSON 对象")
    try:
        return validate_config(config)
    except ValueError as error:
        raise SystemExit(str(error)) from None


def validate_config(config: dict, *, require_smtp: bool = False) -> dict:
    config = dict(config)
    defaults = {"smtp_host": "smtp.gmail.com", "smtp_port": 587,
                "include_quotes": False, "include_replies": False, "include_reposts": True,
                "market_digest_enabled": True, "market_digest_hour": 8,
                "market_digest_end_hour": 24}
    for key, value in defaults.items():
        config.setdefault(key, value)
    for key in ("handle", "to", "smtp_host", "smtp_user", "display_name", "smtp_password"):
        value = config.get(key, "")
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"配置 {key} 必须是无控制字符的字符串")
        config[key] = value if key == "smtp_password" else value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", config["handle"]):
        raise ValueError("配置 handle 必须是有效的 X 用户名（不含 @）")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", config["smtp_host"]):
        raise ValueError("配置 smtp_host 必须是主机名")
    for key in ("to", "smtp_user"):
        value = config[key]
        if value or key == "to" or require_smtp:
            if parseaddr(value)[1] != value or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", value):
                raise ValueError(f"配置 {key} 必须是单个邮箱地址")
    for key in ("include_quotes", "include_replies", "include_reposts", "market_digest_enabled"):
        if not isinstance(config[key], bool):
            raise ValueError(f"配置 {key} 必须为 true 或 false")
    for key, low, high in (("smtp_port", 1, 65535), ("market_digest_hour", 0, 23),
                           ("market_digest_end_hour", 1, 24)):
        value = config[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"配置 {key} 必须是 {low} 到 {high} 的整数")
    if config["market_digest_hour"] >= config["market_digest_end_hour"]:
        raise ValueError("行情截止小时必须晚于开始小时")
    if require_smtp and not config["smtp_password"].strip():
        raise ValueError("配置缺少 smtp_password")
    return config


def diagnose(config: dict) -> int:
    """Check each dependency without sending mail or modifying checkpoints."""
    from market_digest import digest_body

    def check_smtp():
        with smtp_session(validate_config(config, require_smtp=True)):
            pass

    def check_state():
        for name in ("state.json", "market-state.json"):
            path = STATE_PATH.with_name(name)
            if path.exists():
                checked_state(name, load_json(path, {}))
            else:
                log(f"诊断：{name} 尚未初始化")

    def check_timeline():
        if not fetch_posts(config['handle']):
            raise RuntimeError('时间线为空；不能确认数据源正常')

    checks = [("发送状态", check_state), ("SMTP 连接、TLS、登录", check_smtp),
              ("时间线", check_timeline)]
    if config.get("market_digest_enabled", True):
        checks.append(("行情数据", lambda: digest_body(datetime.now(SHANGHAI))))
    status = 0
    for name, check in checks:
        try:
            check()
            log(f"诊断通过：{name}")
        except Exception as error:
            log(f"诊断失败：{name}：{error}")
            status = 1
    log("诊断结束；未发送邮件，未修改检查点")
    return status


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="在 X 账号宣布用量重置时发到邮箱")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "run-notices", "run-market", "diagnose", "check-config", "preview", "test-email", "install", "uninstall", "market-preview"])
    args = parser.parse_args(argv)
    if args.command == "uninstall":
        return uninstall()
    if args.command == "install":
        template = Path(__file__).resolve().parent / "config.example.json"
        return install(template)
    config = load_config()
    if args.command == "diagnose":
        return diagnose(config)
    if args.command == "check-config":
        validate_config(config, require_smtp=True)
        log("配置检查通过")
        return 0
    if args.command == "market-preview":
        from market_digest import digest_body

        print(digest_body(datetime.now(SHANGHAI)))
        return 0
    if args.command == "preview":
        return preview(config)
    if args.command == "test-email":
        return send_test(config)
    from market_digest import run_daily
    status = 0
    jobs = [("行情", run_daily), ("重置通知", run_once)]
    for name, job in jobs:
        if args.command == "run-notices" and job == run_daily:
            continue
        if args.command == "run-market" and job == run_once:
            continue
        try:
            status = max(status, job(config))
        except Exception as error:
            log(f"{name}失败，将在后续运行重试：{error}")
            status = 1
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as error:
        log(f"失败：{error}")
        raise SystemExit(1)
