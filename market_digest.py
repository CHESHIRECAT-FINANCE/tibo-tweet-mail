"""Daily market email using Yahoo Finance daily bars (possibly delayed)."""
from __future__ import annotations

import math
import urllib.parse
import urllib.request
import json
from datetime import datetime

INSTRUMENTS = (
    ('CNY=X', '人民币兑美元', '美元/人民币'),
    ('CL=F', 'WTI 原油期货', '美元/桶'),
    ('BZ=F', '布伦特原油期货', '美元/桶'),
    ('GC=F', 'COMEX 黄金期货', '美元/金衡盎司'),
)


def parse_quote(payload, symbol):
    chart = payload['chart']
    if chart.get('error'):
        raise ValueError(f'{symbol}: {chart["error"]}')
    result = chart['result'][0]
    if result['meta']['symbol'] != symbol:
        raise ValueError('报价代码不匹配')
    rows = []
    for stamp, close in zip(result['timestamp'], result['indicators']['quote'][0]['close']):
        if close is not None and math.isfinite(close) and close > 0:
            rows.append((stamp, float(close)))
    rows.sort()
    if len(rows) < 2:
        raise ValueError(f'{symbol}: 缺少上一交易日报价')
    stamp, current = rows[-1]
    previous_stamp, previous = rows[-2]
    if symbol == 'CNY=X':
        current, previous = 1 / current, 1 / previous
    return current, previous, stamp, previous_stamp


def fetch_quote(symbol):
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/'
           + urllib.parse.quote(symbol, safe='') + '?interval=1d&range=1mo')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return parse_quote(json.load(response), symbol)


def digest_body(now, fetch=fetch_quote):
    lines = [f'每日行情｜{now:%Y-%m-%d %H:%M} 北京时间', '']
    for symbol, name, unit in INSTRUMENTS:
        current, previous, stamp, previous_stamp = fetch(symbol)
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
    now = now.astimezone(mail.SHANGHAI)
    hour = int(config.get('market_digest_hour', 8))
    if not 0 <= hour < 12:
        raise ValueError('market_digest_hour 必须为 0 到 11')
    if not hour <= now.hour < 12:
        return 0
    state_path = mail.STATE_PATH.with_name('market-state.json')
    lock = mail.acquire_lock(state_path.with_suffix('.lock'))
    if lock is None:
        return 0
    try:
        state = mail.load_json(state_path, {})
        today = now.date().isoformat()
        if state.get('sent_date') == today:
            return 0
        body = digest_body(now)
        mail.deliver(config, f'[每日行情] {today} 人民币汇率 · 原油 · 黄金', body)
        mail.save_json(state_path, {'sent_date': today})
        mail.log('每日行情邮件已发送')
        return 0
    finally:
        lock.close()
