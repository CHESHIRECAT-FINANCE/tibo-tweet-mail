"""Bounded retries for read-only public JSON sources; never retry mail here."""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_delay(value: str, fallback: int) -> float:
    """Respect both Retry-After formats; never shorten the server's requested wait."""
    if value.isdigit():
        return max(fallback, int(value))
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(fallback, (stamp - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return fallback


def fetch_json(url: str, *, source: str, retry_not_found: bool = False,
               user_agent: str = 'tibo-tweet-mail/1.1'):
    request = urllib.request.Request(url, headers={
        'User-Agent': user_agent, 'Accept': 'application/json',
    })
    for attempt in range(3):
        retry = True
        delay = 2 ** attempt
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise RuntimeError(f'{source}：响应过大')
                if response.status == 204 or not body:
                    raise ValueError('Empty source response')
                return json.loads(body)
        except urllib.error.HTTPError as error:
            code = error.code
            retry = code in (408, 429, 500, 502, 503, 504) or (retry_not_found and code == 404)
            retry_after = error.headers.get('Retry-After', '') if error.headers else ''
            delay = retry_delay(retry_after, delay)
            if delay > 30:
                retry = False
            error.close()
            reason = f'HTTP {code}'
            if delay > 30:
                reason += '，数据源要求稍后重试'
        except ssl.SSLCertVerificationError:
            reason, retry = 'TLS 证书校验失败', False
        except urllib.error.URLError as error:
            retry = not isinstance(error.reason, ssl.SSLCertVerificationError)
            reason = ('TLS 证书校验失败' if not retry else
                      'DNS 解析失败' if isinstance(error.reason, socket.gaierror) else '网络连接失败')
        except (OSError, http.client.HTTPException):
            reason = '连接中断或超时'
        except (ValueError, UnicodeError):
            reason = '返回的内容不是有效 JSON'
        if not retry or attempt == 2:
            raise RuntimeError(f'{source}：{reason}（已尝试 {attempt + 1} 次）') from None
        time.sleep(delay)
