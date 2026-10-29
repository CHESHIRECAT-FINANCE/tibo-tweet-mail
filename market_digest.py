"""Daily market email using Yahoo Finance daily bars (possibly delayed)."""
from __future__ import annotations

import math
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from http_client import fetch_json

INSTRUMENTS = (
    ('CNY=X', '人民币兑美元', '美元/人民币'),
    ('CL=F', 'WTI 原油期货', '美元/桶'),
    ('BZ=F', '布伦特原油期货', '美元/桶'),
    ('GC=F', 'COMEX 黄金期货', '美元/金衡盎司'),
)


def parse_quote(payload, symbol):
    if not isinstance(payload, dict) or not isinstance(payload.get('chart'), dict):
        raise ValueError(f'{symbol}: 行情响应格式异常')
    chart = payload['chart']
    if chart.get('error'):
        raise ValueError(f'{symbol}: 数据源返回错误')
    try:
        result = chart['result'][0]
        if result['meta']['symbol'] != symbol:
            raise ValueError('报价代码不匹配')
        stamps = result['timestamp']
        closes = result['indicators']['quote'][0]['close']
        if not isinstance(stamps, list) or not isinstance(closes, list) or len(stamps) != len(closes):
            raise ValueError('报价时间与价格数量不匹配')
    except (KeyError, IndexError, TypeError):
        raise ValueError(f'{symbol}: 行情字段缺失') from None
    rows = {}
    for stamp, close in zip(stamps, closes):
        if (type(stamp) in (int, float) and math.isfinite(stamp) and 0 < stamp < 253402300800
                and type(close) in (int, float) and math.isfinite(close) and close > 0):
            rows[stamp] = float(close)
    rows = sorted(rows.items())
    if len(rows) < 2:
        raise ValueError(f'{symbol}: 缺少上一交易日报价')
    stamp, current = rows[-1]
    previous_stamp, previous = rows[-2]
    if symbol == 'CNY=X':
        current, previous = 1 / current, 1 / previous
    if not all(math.isfinite(value) and value > 0 for value in (current, previous)):
        raise ValueError(f'{symbol}: 报价超出有效范围')
    if not math.isfinite((current / previous - 1) * 100):
        raise ValueError(f'{symbol}: 涨跌幅超出有效范围')
    return current, previous, stamp, previous_stamp


def fetch_quote(symbol):
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/'
           + urllib.parse.quote(symbol, safe='') + '?interval=1d&range=1mo')
    return parse_quote(fetch_json(url, source=f'行情 {symbol}', user_agent='Mozilla/5.0'), symbol)


def digest_body(now, fetch=None):
    fetch = fetch or fetch_quote
    # Independent quotes run concurrently, keeping a failing source within the job timeout.
    with ThreadPoolExecutor(max_workers=len(INSTRUMENTS)) as pool:
        quotes = list(pool.map(fetch, [item[0] for item in INSTRUMENTS]))
    lines = [f'每日行情｜{now:%Y-%m-%d %H:%M} 北京时间', '']
    for (symbol, name, unit), quote in zip(INSTRUMENTS, quotes):
        current, previous, stamp, previous_stamp = quote
        when = datetime.fromtimestamp(stamp, now.tzinfo)
        previous_when = datetime.fromtimestamp(previous_stamp, now.tzinfo)
        change = (current / previous - 1) * 100
        price = f'{current:.6f}' if symbol == 'CNY=X' else f'{current:,.2f}'
        lines.append(f'{name}：{price} {unit}（{change:+.2f}%）')
        if symbol == 'CNY=X':
            lines.append(f'  1 人民币 = {current:.6f} 美元；1 美元 = {1/current:.4f} 人民币')
            lines.append('  百分比按人民币兑美元计算，正值表示人民币升值。')
        suffix = '（非今日报价，休市或数据尚未更新）' if when.date() != now.date() else ''
        lines.append(f'  日线时间：{when:%Y-%m-%d %H:%M} 北京时间{suffix}')
        lines.append(f'  对比基准：{previous_when:%Y-%m-%d} 上一有效交易日收盘')
        lines.append('  https://finance.yahoo.com/quote/' + urllib.parse.quote(symbol, safe='') + '/')
        lines.append('')
    lines.extend(['涨跌幅 =（最新日线价格 ÷ 上一有效交易日收盘价 − 1）× 100%。',
                  '来源：Yahoo Finance；行情可能延迟，盘中日线价格仍会变化。',
                  '休市沿用最近交易日行情；原油与黄金为期货报价，换月可能影响涨跌幅，非零售金价。'])
    return '\n'.join(lines) + '\n'


def run_daily(config, *, now=None):
    # Separate state and lock ensure timeline failures cannot block this digest.
    import tibo_tweet_mail as mail

    if not config.get('market_digest_enabled', True):
        return 0
    now = now or datetime.now(mail.SHANGHAI)
    if now.tzinfo is None:
        raise ValueError('行情调度时间必须包含时区')
    now = now.astimezone(mail.SHANGHAI)
    hour = int(config.get('market_digest_hour', 8))
    end_hour = int(config.get('market_digest_end_hour', 24))
    if not 0 <= hour < end_hour <= 24:
        raise ValueError('行情发送时段必须满足 0 <= 开始小时 < 截止小时 <= 24')
    if not hour <= now.hour < end_hour:
        mail.log(f'行情未到发送时段（{hour}:00–{end_hour}:00 北京时间）')
        return 0
    state_path = mail.STATE_PATH.with_name('market-state.json')
    lock = mail.acquire_lock(state_path.with_suffix('.lock'))
    if lock is None:
        return 0
    try:
        state = mail.checked_state('market-state.json', mail.load_json(state_path, {}))
        today = now.date().isoformat()
        if state.get('sent_date') == today:
            mail.log('今日行情已发送，跳过')
            return 0
        if state.get('sent_date', '') > today:
            raise ValueError('行情检查点日期晚于当前日期，请检查系统时间或状态文件')
        body = digest_body(now)
        mail.deliver(config, f'[每日行情] {today} 人民币汇率 · 原油 · 黄金', body)
        mail.save_json(state_path, {'sent_date': today})
        mail.log('每日行情邮件已发送')
        return 0
    finally:
        lock.close()
