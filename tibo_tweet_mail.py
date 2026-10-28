#!/usr/bin/env python3
"""Forward reset notices from an X account to an email inbox.

Polls the public FxTwitter timeline. The first successful run only records
posts that already exist. Later runs mail a post only when it says the
usage limit is resetting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

API_URL = "https://api.fxtwitter.com/2/profile/{handle}/statuses?count=20"
USER_AGENT = "tibo-tweet-mail/1.0"
CONFIG_PATH = Path.home() / ".config" / "tibo-tweet-mail" / "config.json"
STATE_PATH = Path(os.environ.get("TIBO_STATE_DIR") or Path.home() / ".local" / "share" / "tibo-tweet-mail") / "state.json"
LOCK_PATH = STATE_PATH.with_suffix(".lock")
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


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
    if is_repost(item, handle):
        return bool(config.get("include_reposts"))

    reply = item.get("replying_to")
    if isinstance(reply, dict) and screen_name(reply):
        return bool(config.get("include_replies"))

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
    if isinstance(raw, (int, float)):
        seconds = raw / 1000 if raw >= 1_000_000_000_000 else raw
        return datetime.fromtimestamp(seconds, timezone.utc)
    text = str(item.get("created_at") or "")
    if text:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return datetime.now(timezone.utc)


def fetch_posts(handle: str) -> list[dict]:
    url = API_URL.format(handle=urllib.request.quote(handle))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            status = getattr(response, "status", 200)
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"时间线请求失败：HTTP {error.code} {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"时间线请求失败：{error.reason}") from error

    if status == 204 or not body:
        return []
    payload = json.loads(body.decode("utf-8"))
    if payload.get("code") not in (200, None):
        raise RuntimeError(f"时间线返回异常：{payload.get('message') or payload.get('code')}")
    results = payload.get("results") or []
    if not isinstance(results, list):
        raise RuntimeError("时间线结果格式无法识别")
    return [item for item in results if isinstance(item, dict)]


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


def deliver(config: dict, subject: str, body: str) -> None:
    password = "".join(str(config.get("smtp_password") or "").split())
    user = str(config.get("smtp_user") or "")
    host = str(config.get("smtp_host") or "smtp.gmail.com")
    port = int(config.get("smtp_port") or 587)
    recipient = str(config.get("to") or "")
    if not password or not user or not recipient:
        raise RuntimeError("邮箱配置不完整")

    message = EmailMessage()
    message["From"] = user
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(message)


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
        posts = selected_posts(config)
        state = load_json(STATE_PATH, {"seeded": False, "seen": []})
        seen = {str(item) for item in state.get("seen") or []}
        if not state.get("seeded"):
            for item in posts:
                remember(state, str(item.get("id")))
            save_json(STATE_PATH, state)
            log(f"已记录现有原创 {len(posts)} 条，不发送历史帖子")
            return 0

        fresh = [item for item in posts if str(item.get("id")) not in seen]
        if not fresh:
            log("没有新帖")
            return 0

        resets = [item for item in fresh if is_reset_notice(item.get("text") or "")]
        password = "".join(str(config.get("smtp_password") or "").split())
        if resets and not password:
            for item in fresh:
                if item not in resets:
                    remember(state, str(item.get("id")))
            if any(item not in resets for item in fresh):
                save_json(STATE_PATH, state)
            log(
                f"有 {len(resets)} 条重置通知还没发。把 Gmail 应用专用密码写进 {CONFIG_PATH} 的 smtp_password 后再等下一轮"
            )
            return 1

        for item in fresh:
            if item in resets:
                send_email(config, item, reset=True)
                log(f"已发送重置通知 {item.get('url')}")
            remember(state, str(item.get("id")))
            save_json(STATE_PATH, state)
        if not resets:
            log("没有需要通知的重置")
        return 0
    finally:
        lock.close()


def preview(config: dict) -> int:
    posts = selected_posts(config)
    if not posts:
        print("这一页没有符合条件的原创帖")
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
        "text": "这是一封测试信。Tibo 之后发的原创帖会用同样的方式送到这个邮箱。",
        "url": "https://x.com/thsottiaux",
        "created_timestamp": time.time(),
    }
    send_email(config, sample)
    log(f"测试信已发给 {config.get('to')}")
    return 0


def plist_text(label: str, command: str, interval: int | None = None, *, hours: tuple[int, ...] = ()) -> str:
    script = Path(__file__).resolve()
    python = sys.executable
    if hours:
        slots = "\n".join(
            f"""    <dict>
      <key>Hour</key>
      <integer>{hour}</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>"""
            for hour in hours
        )
        schedule = f"""  <key>StartCalendarInterval</key>
  <array>
{slots}
  </array>"""
    else:
        schedule = f"""  <key>StartInterval</key>
  <integer>{interval}</integer>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
    <string>{command}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{script.parent}</string>
{schedule}
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{LOG_PATH}</string>
  <key>StandardErrorPath</key>
  <string>{LOG_PATH}</string>
</dict>
</plist>
"""


def launch_agent(label: str, path: Path) -> None:
    import os
    import subprocess

    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    subprocess.run(["launchctl", "enable", f"{domain}/{label}"], check=False)


def install(config_template: Path) -> int:
    import os

    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(config_template.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(CONFIG_PATH, 0o600)
        log(f"已创建配置 {CONFIG_PATH}")
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_text(PLIST_LABEL, "run", 300), encoding="utf-8")
    launch_agent(PLIST_LABEL, PLIST_PATH)
    log("定时任务已安装，每 5 分钟检查一次重置通知")
    return 0


def uninstall() -> int:
    import os
    import subprocess

    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{PLIST_LABEL}"], check=False)
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
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
        config = load_json(CONFIG_PATH, {})
    if not isinstance(config, dict):
        raise SystemExit("邮箱配置必须是 JSON 对象")
    if not config.get("handle") or not config.get("to"):
        raise SystemExit(f"配置缺少 handle 或 to：{CONFIG_PATH}")
    return config


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="在 X 账号宣布用量重置时发到邮箱")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "preview", "test-email", "install", "uninstall", "market-preview"])
    args = parser.parse_args(argv)
    if args.command == "uninstall":
        return uninstall()
    if args.command == "install":
        template = Path(__file__).resolve().parent / "config.example.json"
        return install(template)
    config = load_config()
    if args.command == "market-preview":
        from market_digest import digest_body

        print(digest_body(datetime.now(SHANGHAI)))
        return 0
    if args.command == "preview":
        return preview(config)
    if args.command == "test-email":
        return send_test(config)
    from market_digest import run_daily

    market_status = 0
    try:
        market_status = run_daily(config)
    except Exception as error:
        log(f"行情推送失败，将在下一轮重试：{error}")
        market_status = 1
    return max(run_once(config), market_status)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as error:
        log(f"失败：{error}")
        raise SystemExit(1)
