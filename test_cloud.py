import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_state
import tibo_tweet_mail as mail


class CloudConfigTests(unittest.TestCase):
    def test_secret_config_without_local_file(self):
        with patch.dict(os.environ, {"TIBO_MAIL_CONFIG": '{"handle":"example","to":"you@example.com"}'}), \
             patch.object(mail, 'CONFIG_PATH', Path('/missing/config.json')):
            self.assertEqual(mail.load_config()['handle'], 'example')

    def test_bad_secret_does_not_fall_back_or_echo_its_contents(self):
        with patch.dict(os.environ, {"TIBO_MAIL_CONFIG": 'sensitive-example'}):
            with self.assertRaises(SystemExit) as caught:
                mail.load_config()
            self.assertNotIn('sensitive-example', str(caught.exception))

    def test_checkpoint_whitelist(self):
        self.assertEqual(cloud_state.checked_state('state.json', {
            'seeded': True, 'seen': ['123'], 'smtp_password': 'do-not-publish',
        }), {'seeded': True, 'seen': ['123']})
        with self.assertRaises(ValueError):
            cloud_state.checked_state('state.json', {'seen': ['not-a-post-id']})
        with self.assertRaises(ValueError) as caught:
            cloud_state.checked_state('market-state.json', {'sent_date': 'private-value'})
        self.assertNotIn('private-value', str(caught.exception))

    def test_pending_notice_whitelist_and_consistency(self):
        item = {'id': '2', 'text': 'Usage reset now', 'created_timestamp': 1,
                'author': {'screen_name': 'example', 'private': 'not-public'},
                'smtp_password': 'private', 'url': 'https://example.com/private-token'}
        cleaned = cloud_state.checked_state('state.json', {'seeded': True, 'seen': ['1'], 'pending': [item]})
        text = json.dumps(cleaned)
        self.assertNotIn('private', text)
        self.assertEqual(cleaned['pending'][0]['url'], 'https://x.com/i/status/2')
        for state in ({'seeded': True, 'seen': ['2'], 'pending': [item]},
                      {'seeded': False, 'seen': [], 'pending': [item]},
                      {'seeded': True, 'seen': [], 'pending': [item, item]}):
            with self.assertRaises(ValueError):
                cloud_state.checked_state('state.json', state)


class CloudPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.repo = self.root / 'source'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        subprocess.run(['git', 'init', str(self.repo)], check=True, capture_output=True)
        self.old = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.old)
        cloud_state.git('config', 'user.name', 'Test')
        cloud_state.git('config', 'user.email', 'test@example.com')
        (self.repo / 'source.txt').write_text('source only')
        cloud_state.git('add', 'source.txt')
        cloud_state.git('commit', '-m', 'Initial')
        cloud_state.git('remote', 'add', 'origin', str(self.remote))

    def test_two_fresh_runners_preserve_state_without_publishing_secrets(self):
        first = self.root / 'first-run'
        initial = {'state.json': {'seeded': True, 'seen': ['123']},
                   'market-state.json': {'sent_date': '2026-10-28'}}
        with patch.dict(os.environ, {'TIBO_INITIAL_STATE': json.dumps(initial)}):
            cloud_state.restore(first)
        self.assertFalse((first / 'source.txt').exists())
        (first / 'state.json').write_text(json.dumps({'seeded': True, 'seen': ['123', '456']}))
        (first / 'config.json').write_text('{"smtp_password":"must-stay-local"}')
        (first / 'state.lock').write_text('')
        cloud_state.save(first)
        cloud_state.git('worktree', 'remove', '--force', str(first))
        second = self.root / 'second-run'
        # Even an old bootstrap secret must not replace the persisted state.
        with patch.dict(os.environ, {'TIBO_INITIAL_STATE': json.dumps(initial)}):
            cloud_state.restore(second)
        self.assertEqual(json.loads((second / 'state.json').read_text())['seen'], ['123', '456'])
        self.assertEqual(json.loads((second / 'market-state.json').read_text())['sent_date'], '2026-10-28')
        files = cloud_state.git('ls-tree', '--name-only', 'HEAD', cwd=second).stdout.splitlines()
        self.assertEqual(sorted(files), ['market-state.json', 'state.json'])

    def test_pending_queue_survives_fresh_cloud_runner(self):
        first = self.root / 'first-run'
        cloud_state.restore(first)
        pending = {'id': '123', 'text': 'Usage reset now', 'created_timestamp': 1,
                   'author': {'screen_name': 'example'}}
        cloud_state.write_state(first, 'state.json', {'seeded': True, 'seen': [], 'pending': [pending]})
        cloud_state.save(first)
        cloud_state.restore(self.root / 'second-run')
        restored = json.loads((self.root / 'second-run' / 'state.json').read_text())
        self.assertEqual(restored['pending'][0]['id'], '123')

    def test_unreachable_remote_is_not_treated_as_first_run(self):
        cloud_state.git('remote', 'set-url', 'origin', str(self.root / 'missing.git'))
        with self.assertRaises(subprocess.CalledProcessError):
            cloud_state.restore(self.root / 'state')

    def test_missing_persisted_file_stops_sending(self):
        first = self.root / 'first-run'
        cloud_state.restore(first)
        cloud_state.git('rm', 'market-state.json', cwd=first)
        cloud_state.git('commit', '-m', 'Simulate damaged checkpoint', cwd=first)
        cloud_state.git('push', 'origin', 'HEAD:refs/heads/mail-state', cwd=first)
        with self.assertRaises(RuntimeError):
            cloud_state.restore(self.root / 'second-run')

    def test_pre_staged_non_checkpoint_file_is_never_published(self):
        directory = self.root / 'state'
        cloud_state.restore(directory)
        original = cloud_state.git('rev-parse', 'HEAD', cwd=directory).stdout
        (directory / 'config.json').write_text('{"smtp_password":"private"}')
        cloud_state.git('add', 'config.json', cwd=directory)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected tracked'):
            cloud_state.save(directory)
        self.assertEqual(cloud_state.git('rev-parse', 'HEAD', cwd=directory).stdout, original)

    def test_missing_local_checkpoint_cannot_silently_save(self):
        directory = self.root / 'state'
        cloud_state.restore(directory)
        (directory / 'market-state.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Missing checkpoint'):
            cloud_state.save(directory)

    def test_push_timeout_retries_same_checkpoint(self):
        directory = self.root / 'state'
        cloud_state.restore(directory)
        (directory / 'market-state.json').write_text('{"sent_date":"2026-10-29"}')
        original_git = cloud_state.git
        pushes = []

        def unreliable_git(*args, **kwargs):
            if args[0] == 'push':
                pushes.append(args)
                if len(pushes) == 1:
                    raise subprocess.TimeoutExpired('git', 60)
            return original_git(*args, **kwargs)

        with patch.object(cloud_state, 'git', side_effect=unreliable_git), \
             patch('cloud_state.time.sleep'):
            cloud_state.save(directory)
        self.assertEqual(pushes[0], pushes[1])
        self.assertEqual(json.loads(cloud_state.git('show', 'refs/heads/mail-state:market-state.json',
                                                   cwd=self.remote).stdout)['sent_date'], '2026-10-29')


if __name__ == '__main__':
    unittest.main()
