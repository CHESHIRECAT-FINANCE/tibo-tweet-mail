"""Adapters turn public data into small, stable items. Never execute source content."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin

from .net import get, public_url


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "tr", "section") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self.hidden:
            self.hidden -= 1
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "tr", "section") and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(value or "")
    return "\n".join(line for line in (" ".join(s.split()) for s in "".join(parser.parts).splitlines()) if line)


def item(identity, title, body, url="", published=""):
    # Untrusted content is text, including in notification subjects and previews.
    title = " ".join(str(title or "未命名更新").split())[:300]
    body = str(body or "")[:10000]
    url = url if isinstance(url, str) else ""
    if url:
        try:
            url = public_url(url)
        except ValueError:
            url = ""
    return {"id": digest(str(identity)), "title": title, "body": body,
            "url": url[:2000], "published": " ".join(str(published or "").split())[:100]}


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def value(node, *names):
    for name in names:
        for child in node:
            if local_name(child.tag) == name:
                return "".join(child.itertext())
    return ""


def rss(source, now):
    body, charset, final_url = get(source["url"])
    # Reject DTD/entity declarations, including UTF-16/32 encoded declarations.
    if b"<!doctype" in body.replace(b"\0", b"").lower() or b"<!entity" in body.replace(b"\0", b"").lower():
        raise ValueError("订阅源包含不支持的 DTD 或实体声明")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ValueError("无法解析 RSS / Atom，请填写订阅源地址而不是网站首页") from None
    if local_name(root.tag) not in ("rss", "feed", "RDF"):
        raise ValueError("地址没有返回 RSS / Atom 内容")
    entries = [node for node in root.iter() if local_name(node.tag) in ("item", "entry")]
    if len(entries) > 1000:
        raise ValueError("订阅源超过 1000 条，请使用较小的订阅窗口")
    result = []
    for node in entries:
        link = value(node, "link")
        for child in node:
            if local_name(child.tag) == "link" and child.get("href") and child.get("rel", "alternate") == "alternate":
                link = child.get("href")
                break
        link = urljoin(final_url, link.strip()) if link.strip() else ""
        title = plain(value(node, "title"))
        content = plain(value(node, "encoded", "content", "description", "summary"))
        identity = value(node, "guid", "id").strip() or link or title + "\n" + content
        if not title and not content:
            continue
        result.append(item(identity, title, content, link, value(node, "published", "pubDate", "updated", "date")))
    # Most feeds are newest-first. New items are sent oldest-first within this window.
    return list(reversed(result))


def github(source, now):
    # Release API responses include every binary asset, even though we only need notes.
    body, _, _ = get("https://api.github.com/repos/" + source["repository"] + "/releases?per_page=10")
    try:
        releases = json.loads(body)
        if not isinstance(releases, list):
            raise ValueError()
        result = []
        for release in reversed(releases):
            if release.get("draft") or (release.get("prerelease") and not source["prereleases"]):
                continue
            if type(release.get("id")) is not int or not isinstance(release.get("body", ""), (str, type(None))):
                raise ValueError()
            result.append(item(str(release["id"]), release.get("name") or release.get("tag_name"),
                               release.get("body") or "", release.get("html_url", ""), release.get("published_at", "")))
        return result
    except (ValueError, TypeError, AttributeError):
        raise ValueError("GitHub 发布数据格式异常") from None


def webpage(source, now):
    body, charset, final_url = get(source["url"])
    try:
        content = plain(body.decode(charset))
    except (UnicodeError, LookupError):
        raise ValueError("无法读取网页编码") from None
    if not content:
        raise ValueError("网页没有可读取的文字；暂不支持依赖登录或 JavaScript 加载的页面")
    return [item(content, source["name"] + "：网页内容更新", content, final_url)]


def x(source, now):
    import tibo_tweet_mail as mail
    posts = mail.selected_posts(source)
    if source["reset_only"]:
        posts = [p for p in posts if mail.is_reset_notice(p.get("text", ""))]
    return [item(p["id"], p.get("text", ""), p.get("text", ""),
                 "https://x.com/i/status/" + str(p["id"]), mail.created_at(p).isoformat()) for p in posts]


def market(source, now):
    if now.hour < source["hour"]:
        return []
    from market_digest import digest_body
    from tibo_tweet_mail import SHANGHAI
    # The established quote renderer explicitly labels its times as Beijing time.
    body = digest_body(now.astimezone(SHANGHAI))
    return [item(now.date().isoformat(), "每日行情 " + now.date().isoformat(), body)]


ADAPTERS = {"rss": rss, "github": github, "webpage": webpage, "x": x, "market": market}


def fetch(source, now, registry=None):
    from .plugins import default_registry
    from .state import checked_item
    registry = registry or default_registry()
    rows = registry.sources[source["type"]].fetch(source, now)
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Source adapters must return at most 1000 items")
    rows = [checked_item(row) for row in rows]
    # Avoid duplicate items in one source response, even when feed GUIDs repeat.
    return list({row["id"]: row for row in rows}.values())


def matches(source, row):
    text = (row["title"] + "\n" + row["body"]).casefold()
    if any(word.casefold() in text for word in source["exclude"]):
        return False
    words = source["keywords"]
    return not words or (all if source["match"] == "all" else any)(word.casefold() in text for word in words)
