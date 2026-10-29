"""Read public HTTP sources with bounded responses and validated redirects."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from http_client import retry_delay

MAX_BYTES = 2_000_000


def public_url(url, *, resolve=False):
    try:
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme not in ("https", "http") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 80, 443)
                or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError()
        host = parts.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError()
        if resolve:
            addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError()
    except (ValueError, TypeError, OSError):
        raise ValueError("仅支持可访问的公网 HTTP(S) 地址，不支持内网地址、账号密码或特殊端口") from None
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl, resolve=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(url):
    public_url(url, resolve=True)
    request = urllib.request.Request(url, headers={
        "User-Agent": "InfoRelay/0.1 (+personal information subscriptions)",
        "Accept": "application/atom+xml, application/rss+xml, application/json, text/html, */*",
    })
    opener = urllib.request.build_opener(PublicRedirect())
    for attempt in range(3):
        delay, retry = 2 ** attempt, True
        try:
            with opener.open(request, timeout=15) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("信息源响应超过 2 MB")
                if not body:
                    raise ValueError("信息源返回空内容")
                return body, response.headers.get_content_charset() or "utf-8", response.geturl()
        except urllib.error.HTTPError as error:
            retry = error.code in (408, 429, 500, 502, 503, 504)
            delay = retry_delay(error.headers.get("Retry-After", ""), delay)
            retry = retry and delay <= 30
            reason = f"HTTP {error.code}"
            error.close()
        except ssl.SSLCertVerificationError:
            reason, retry = "TLS 证书校验失败", False
        except urllib.error.URLError as error:
            retry = not isinstance(error.reason, ssl.SSLCertVerificationError)
            reason = "连接失败" if retry else "TLS 证书校验失败"
        except (OSError, http.client.HTTPException):
            reason = "连接中断或超时"
        if not retry or attempt == 2:
            raise RuntimeError(f"数据源请求失败：{reason}") from None
        time.sleep(delay)
