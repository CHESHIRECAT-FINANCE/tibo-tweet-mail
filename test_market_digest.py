import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import market_digest as market
import tibo_tweet_mail as mail


class MarketTests(unittest.TestCase):
    def test_inverse_rate_percentage(self):
        payload = {'chart': {'result': [{'meta': {'symbol': 'CNY=X'},
            'timestamp': [1, 2, 3], 'indicators': {'quote': [{'close': [8, None, 4]}]}}]}}
        current, previous, *_ = market.parse_quote(payload, 'CNY=X')
        self.assertEqual(current, .25)
        self.assertEqual((current / previous - 1) * 100, 100)

    def test_missing_previous_rejected(self):
        payload = {'chart': {'result': [{'meta': {'symbol': 'GC=F'},
            'timestamp': [1, 2], 'indicators': {'quote': [{'close': [None, 4]}]}}]}}
        with self.assertRaises(ValueError):
            market.parse_quote(payload, 'GC=F')

    def test_schedule_deduplication_and_retry(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(mail, 'STATE_PATH', Path(directory) / 'state.json'), \
             patch.object(market, 'digest_body', return_value='行情'), \
             patch.object(mail, 'deliver') as deliver:
            def run(hour, day=26):
                return market.run_daily({}, now=datetime(2026, 10, day, hour, tzinfo=mail.SHANGHAI))
            run(7)
            run(12)
            deliver.assert_not_called()
            deliver.side_effect = RuntimeError('SMTP failed')
            with self.assertRaises(RuntimeError):
                run(8)
            self.assertFalse((Path(directory) / 'market-state.json').exists())
            deliver.side_effect = None
            run(8)
            run(9)
            self.assertEqual(deliver.call_count, 2)
            run(8, 27)
            self.assertEqual(deliver.call_count, 3)

    def test_stale_quotes_label_and_units(self):
        now = datetime(2026, 10, 26, 8, tzinfo=mail.SHANGHAI)
        old = datetime(2026, 10, 23, 8, tzinfo=mail.SHANGHAI).timestamp()
        body = market.digest_body(now, fetch=lambda _: (10, 8, old, old - 86400))
        self.assertIn('+25.00%', body)
        self.assertIn('非今日报价', body)
        self.assertIn('美元/桶', body)
        self.assertIn('美元/金衡盎司', body)


if __name__ == '__main__':
    unittest.main()
