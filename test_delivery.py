import io
import json
import os
import plistlib
import smtplib
import ssl
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import http_client
import tibo_tweet_mail as mail


CONFIG = dict(handle='example', to='recipient@example.com', smtp_user='sender@example.com',
              smtp_password='app password', smtp_host='smtp.gmail.com', smtp_port=587)


def response(payload):
    result = MagicMock()
    result.status = 200
    result.read.return_value = json.dumps(payload).encode()
    result.__enter__.return_value = result
    return result


def post(post_id='1', text='Usage reset now'):
    return dict(id=post_id, type='status', text=text, created_timestamp=1,
                author={'screen_name': 'example'})


class HTTPTests(unittest.TestCase):
    @patch('http_client.time.sleep')
    @patch('http_client.urllib.request.urlopen')
    def test_long_retry_after_does_not_retry_early(self, open_url, sleep):
        open_url.side_effect = urllib.error.HTTPError('https://source', 429, '',
                                                      {'Retry-After': '120'}, io.BytesIO(b''))
        with self.assertRaisesRegex(RuntimeError, '稍后重试'):
            http_client.fetch_json('https://source', source='test')
        self.assertEqual(open_url.call_count, 1)
        sleep.assert_not_called()

    def test_retry_after_http_date(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        self.assertGreater(http_client.retry_delay(format_datetime(future), 1), 80)
        self.assertEqual(http_client.retry_delay('invalid', 2), 2)

    @patch('http_client.time.sleep')
    @patch('http_client.urllib.request.urlopen')
    def test_empty_response_retries_without_being_treated_as_empty_timeline(self, open_url, sleep):
        empty = response({})
        empty.status = 204
        open_url.side_effect = [empty, response({'results': [post()]})]
        self.assertEqual(len(mail.fetch_posts('example')), 1)
        self.assertEqual(open_url.call_count, 2)

    @patch('http_client.time.sleep')
    @patch('http_client.urllib.request.urlopen')
    def test_timeline_recovers_after_transient_404(self, open_url, sleep):
        open_url.side_effect = [urllib.error.HTTPError('https://source', 404, '', {}, io.BytesIO(b'')),
                                response({'code': 200, 'results': [post()]})]
        self.assertEqual(mail.fetch_posts('example')[0]['id'], '1')
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch('http_client.time.sleep')
    @patch('http_client.urllib.request.urlopen')
    def test_retry_is_bounded_and_does_not_echo_response(self, open_url, sleep):
        def unavailable(*args, **kwargs):
            raise urllib.error.HTTPError('https://source', 503, 'sensitive', {}, io.BytesIO(b'credentials'))
        open_url.side_effect = unavailable
        with self.assertRaisesRegex(RuntimeError, '503.*3') as caught:
            http_client.fetch_json('https://source', source='test')
        self.assertNotIn('credentials', str(caught.exception))
        self.assertEqual(open_url.call_count, 3)

    @patch('http_client.time.sleep')
    @patch('http_client.urllib.request.urlopen')
    def test_certificate_failure_is_not_retried(self, open_url, sleep):
        open_url.side_effect = urllib.error.URLError(ssl.SSLCertVerificationError('bad certificate'))
        with self.assertRaisesRegex(RuntimeError, '证书'):
            http_client.fetch_json('https://source', source='test')
        sleep.assert_not_called()

    @patch('tibo_tweet_mail.fetch_json')
    def test_missing_id_is_rejected_and_duplicates_are_removed(self, fetch):
        fetch.return_value = {'results': [post(), post()]}
        self.assertEqual(len(mail.fetch_posts('example')), 1)
        fetch.return_value = {'results': [dict(text='Usage reset now')]}
        with self.assertRaises(RuntimeError):
            mail.fetch_posts('example')
        fetch.return_value = {'code': 200}
        with self.assertRaises(RuntimeError):
            mail.fetch_posts('example')


class SMTPTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch('tibo_tweet_mail.smtplib.SMTP')
        self.factory = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.smtp = self.factory.return_value
        self.smtp.send_message.return_value = {}

    def test_tls_checks_certificates_and_message_has_delivery_headers(self):
        mail.deliver(CONFIG, 'Test', 'body')
        context = self.smtp.starttls.call_args.kwargs['context']
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.smtp.login.assert_called_once_with(CONFIG['smtp_user'], 'apppassword')
        message = self.smtp.send_message.call_args.args[0]
        self.assertIsNotNone(message['Date'])
        self.assertIsNotNone(message['Message-ID'])

    @patch('tibo_tweet_mail.time.sleep')
    def test_only_pre_submission_disconnect_is_retried(self, sleep):
        self.factory.side_effect = [smtplib.SMTPServerDisconnected('closed'), self.smtp]
        mail.deliver(CONFIG, 'Test', 'body')
        self.assertEqual(self.factory.call_count, 2)
        self.smtp.send_message.assert_called_once()

    @patch('tibo_tweet_mail.time.sleep')
    def test_authentication_failure_is_redacted_and_not_retried(self, sleep):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'secret user@example.com')
        with self.assertRaisesRegex(RuntimeError, '535') as caught:
            mail.deliver(CONFIG, 'Test', 'body')
        self.assertNotIn('secret', str(caught.exception))
        self.assertNotIn('user@example', str(caught.exception))
        sleep.assert_not_called()
        self.smtp.send_message.assert_not_called()

    def test_quit_failure_does_not_turn_accepted_mail_into_failure(self):
        self.smtp.quit.side_effect = smtplib.SMTPResponseException(451, b'closing failure')
        mail.deliver(CONFIG, 'Test', 'body')
        self.smtp.send_message.assert_called_once()
        self.smtp.close.assert_called_once()

    def test_unknown_submission_outcome_is_not_immediately_retried(self):
        self.smtp.send_message.side_effect = smtplib.SMTPServerDisconnected('closed')
        with self.assertRaisesRegex(RuntimeError, '是否接收未知'):
            mail.deliver(CONFIG, 'Test', 'body')
        self.smtp.send_message.assert_called_once()

    def test_explicit_rejection_is_not_reported_as_unknown_delivery(self):
        self.smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused({'private': (550, b'rejected')})
        with self.assertRaisesRegex(RuntimeError, '收件地址被拒绝') as caught:
            mail.deliver(CONFIG, 'Test', 'body')
        self.assertNotIn('未知', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))

    @patch('tibo_tweet_mail.time.sleep')
    def test_missing_starttls_is_not_retried(self, sleep):
        self.smtp.starttls.side_effect = smtplib.SMTPNotSupportedError('not supported')
        with self.assertRaisesRegex(RuntimeError, '不支持'):
            mail.deliver(CONFIG, 'Test', 'body')
        sleep.assert_not_called()

    @patch('tibo_tweet_mail.smtplib.SMTP_SSL')
    def test_ssl_465_and_non_gmail_password_spaces(self, factory):
        factory.return_value.send_message.return_value = {}
        mail.deliver(dict(CONFIG, smtp_port=465, smtp_host='smtp.example.com'), 'Test', 'body')
        self.factory.assert_not_called()
        self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
        factory.return_value.login.assert_called_once_with(CONFIG['smtp_user'], 'app password')


class ConfigTests(unittest.TestCase):
    def test_existing_launch_agent_label_is_reused(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
             patch.object(mail, 'PLIST_PATH', Path(directory) / 'com.tibo-tweet-mail.plist'):
            legacy = Path(directory) / 'com.legacy.tibo-tweet-mail.plist'
            legacy.write_text(mail.plist_text(legacy.stem, 'run', 300))
            self.assertEqual(mail.agent_settings(), (legacy.stem, legacy))

    def test_plist_escapes_paths_and_detects_duplicate_installations(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
             patch.object(mail, 'PLIST_PATH', Path(directory) / 'com.tibo-tweet-mail.plist'), \
             patch.object(mail, 'LOG_PATH', Path('/tmp/a & b.log')):
            xml = mail.plist_text('com.tibo-tweet-mail', 'run', 300)
            self.assertEqual(plistlib.loads(xml.encode())['StandardOutPath'], '/tmp/a & b.log')
            for name in ('com.tibo-tweet-mail', 'com.legacy.tibo-tweet-mail'):
                (Path(directory) / (name + '.plist')).write_text(mail.plist_text(name, 'run', 300))
            with self.assertRaisesRegex(ValueError, '多个'):
                mail.agent_settings()

    def test_bad_values_fail_without_echoing_secrets(self):
        for changes in ({'smtp_port': True}, {'smtp_port': 0}, {'include_reposts': 'false'},
                        {'handle': '../bad'}, {'smtp_user': 'bad\nSecret'}, {'to': 'a@b.com,c@d.com'},
                        {'market_digest_hour': 12, 'market_digest_end_hour': 8}):
            with self.subTest(changes=changes), self.assertRaises(ValueError) as caught:
                mail.validate_config(dict(CONFIG, **changes))
            self.assertNotIn('Secret', str(caught.exception))

    def test_diagnose_does_not_send_or_change_state(self):
        with patch.object(mail, 'smtp_session'), patch.object(mail, 'fetch_posts', return_value=[post()]), \
             patch('market_digest.digest_body'), patch.object(mail, 'load_json', return_value={}), \
             patch.object(mail, 'save_json') as save, patch.object(mail, 'deliver') as send:
            self.assertEqual(mail.diagnose(CONFIG), 0)
        send.assert_not_called()
        save.assert_not_called()


class TimelineStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        patcher = patch.object(mail, 'STATE_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch('tibo_tweet_mail.selected_posts', return_value=[post()])
    @patch('tibo_tweet_mail.send_email')
    def test_first_run_seeds_without_sending(self, send, posts):
        self.assertEqual(mail.run_once(CONFIG), 0)
        send.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text())['seen'], ['1'])

    @patch('tibo_tweet_mail.selected_posts', return_value=[])
    def test_empty_initial_result_does_not_create_seed(self, posts):
        mail.run_once(CONFIG)
        self.assertFalse(self.path.exists())

    @patch('tibo_tweet_mail.selected_posts', return_value=[post('2'), post('3')])
    @patch('tibo_tweet_mail.send_email')
    def test_partial_delivery_is_saved_and_only_unsent_retries(self, send, posts):
        mail.save_json(self.path, {'seeded': True, 'seen': ['1']})
        send.side_effect = [None, RuntimeError('SMTP unavailable')]
        with self.assertRaises(RuntimeError):
            mail.run_once(CONFIG)
        self.assertEqual(json.loads(self.path.read_text())['seen'], ['1', '2'])
        send.reset_mock(side_effect=True)
        mail.run_once(CONFIG)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1]['id'], '3')

    @patch('tibo_tweet_mail.send_email')
    def test_failed_notice_retries_after_disappearing_from_source(self, send):
        mail.save_json(self.path, {'seeded': True, 'seen': ['1']})
        send.side_effect = RuntimeError('SMTP unavailable')
        with patch.object(mail, 'selected_posts', return_value=[post('2')]):
            with self.assertRaises(RuntimeError):
                mail.run_once(CONFIG)
        checkpoint = json.loads(self.path.read_text())
        self.assertEqual(checkpoint['pending'][0]['id'], '2')
        send.reset_mock(side_effect=True)
        with patch.object(mail, 'selected_posts', return_value=[post('20', 'ordinary post')]):
            mail.run_once(CONFIG)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1]['id'], '2')
        self.assertFalse(json.loads(self.path.read_text()).get('pending'))

    @patch('tibo_tweet_mail.send_email')
    def test_pending_notice_delivered_even_when_source_fails(self, send):
        mail.save_json(self.path, {'seeded': True, 'seen': ['1'], 'pending': [mail.checked_post(post('2'))]})
        with patch.object(mail, 'selected_posts', side_effect=RuntimeError('HTTP 404')):
            with self.assertRaisesRegex(RuntimeError, '404'):
                mail.run_once(CONFIG)
        send.assert_called_once()
        self.assertIn('2', json.loads(self.path.read_text())['seen'])
        self.assertFalse(json.loads(self.path.read_text()).get('pending'))

    @patch('tibo_tweet_mail.send_email')
    def test_queue_persistence_failure_stops_before_smtp(self, send):
        mail.save_json(self.path, {'seeded': True, 'seen': ['1']})
        with patch.object(mail, 'selected_posts', return_value=[post('2')]), \
             patch.object(mail, 'save_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                mail.run_once(CONFIG)
        send.assert_not_called()

    @patch('tibo_tweet_mail.send_email')
    def test_duplicate_ids_in_one_batch_are_sent_once(self, send):
        mail.save_json(self.path, {'seeded': True, 'seen': ['1']})
        with patch.object(mail, 'selected_posts', return_value=[post('2'), post('2')]):
            mail.run_once(CONFIG)
        send.assert_called_once()

    def test_post_timestamp_formats_and_deterministic_fallback(self):
        expected = datetime(2026, 10, 29, tzinfo=timezone.utc)
        for item in ({'created_at': '2026-10-29T00:00:00Z'},
                     {'created_timestamp': expected.timestamp() * 1000},
                     {'created_timestamp': float('nan'), 'created_at': 'Thu, 29 Oct 2026 00:00:00 GMT'}):
            self.assertEqual(mail.created_at(item), expected)
        snowflake = str((int(expected.timestamp() * 1000) - 1288834974657) << 22)
        self.assertEqual(mail.created_at({'id': snowflake}), expected)
        with self.assertRaises(ValueError):
            mail.created_at({'id': '1', 'created_at': 'invalid'})

    @patch('tibo_tweet_mail.selected_posts', return_value=[post()])
    @patch('tibo_tweet_mail.send_email')
    def test_corrupt_state_fails_closed(self, send, posts):
        self.path.write_text('{"seen":"bad"}')
        with self.assertRaises(ValueError):
            mail.run_once(CONFIG)
        send.assert_not_called()

    def test_reposted_quotes_and_replies_respect_flags(self):
        item = post()
        item.update(author={'screen_name': 'other'}, quote={'id': '2'})
        self.assertFalse(mail.is_forwardable(item, dict(CONFIG, include_reposts=True)))
        item.pop('quote')
        item['replying_to'] = {'screen_name': 'other'}
        self.assertFalse(mail.is_forwardable(item, dict(CONFIG, include_reposts=True)))

    @patch('tibo_tweet_mail.load_config', return_value=CONFIG)
    @patch('market_digest.run_daily', side_effect=RuntimeError('market unavailable'))
    @patch('tibo_tweet_mail.run_once', return_value=0)
    def test_market_failure_does_not_block_notices(self, notices, market, config):
        self.assertEqual(mail.main(['run']), 1)
        notices.assert_called_once()


if __name__ == '__main__':
    unittest.main()
