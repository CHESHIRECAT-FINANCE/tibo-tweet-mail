import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from info_relay import config, sources, state, engine, cloud, net
from info_relay.__main__ import main


def settings(**source):
    return config.validate({"sources": [{"id": "news", "type": "rss", "name": "News", "url": "https://example.com/feed", **source}],
                            "email": {"smtp_user": "sender@example.com", "to": "reader@example.com", "smtp_password": "secret"}})


def entry(identity, title="Release", body="new features"):
    return sources.item(identity, title, body, "https://example.com/update")


NOW = datetime(2026, 10, 30, 9, tzinfo=timezone.utc)


class ConfigTests(unittest.TestCase):
    def test_defaults_are_safe_and_input_is_not_mutated(self):
        raw = {"sources": [{"id": "news", "type": "github", "repository": "cli/cli"}]}
        before = copy.deepcopy(raw)
        parsed = config.validate(raw)
        self.assertEqual(raw, before)
        self.assertEqual(parsed["sources"][0]["initial"], "skip")
        self.assertEqual(parsed["sources"][0]["keywords"], [])

    def test_invalid_or_misspelled_settings_fail_early(self):
        variants = [{"type": []}, {"type": "unknown"}, {"id": "../a"}, {"enabled": "false"},
                    {"keywords": "python"}, {"keywords": [""]}, {"initial": "all"},
                    {"include_quotes": 1}, {"url": "file:///etc/passwd"}, {"keyword": ["misspelled"]}]
        for source in variants:
            with self.subTest(source=source), self.assertRaises(ValueError):
                settings(**source)

    def test_duplicate_ids_and_invalid_timezone_rejected(self):
        cfg = settings()
        cfg["sources"].append(copy.deepcopy(cfg["sources"][0]))
        with self.assertRaises(ValueError): config.validate(cfg)
        cfg = settings(); cfg["timezone"] = "Not/AZone"
        with self.assertRaises(ValueError): config.validate(cfg)

    def test_password_env_fallback_and_header_validation(self):
        cfg = settings(); cfg["email"]["smtp_password"] = ""
        with patch.dict(os.environ, {"INFO_RELAY_SMTP_PASSWORD": "test-password"}):
            self.assertEqual(config.smtp_config(cfg)["smtp_password"], "test-password")
        cfg["email"]["to"] = "reader@example.com\nBcc:other@example.com"
        with self.assertRaises(ValueError): config.smtp_config(cfg)

    def test_private_and_credential_urls_rejected(self):
        for url in ["https://localhost/x", "http://127.0.0.1", "http://[::1]", "http://10.0.0.1", "http://169.254.169.254", "https://user:pass@example.com", "https://example.com:22"]:
            with self.subTest(url=url), self.assertRaises(ValueError): net.public_url(url)

    def test_dns_and_redirects_cannot_target_internal_services(self):
        address = [(2, 1, 6, "", ("10.1.1.1", 443))]
        with patch.object(net.socket, "getaddrinfo", return_value=address):
            with self.assertRaises(ValueError): net.public_url("https://example.com", resolve=True)
            with self.assertRaises(ValueError):
                net.PublicRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


class SourceTests(unittest.TestCase):
    def test_rss_namespace_and_stable_guid(self):
        feed = b'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
          <item><guid>same-id</guid><title>New &amp; useful</title><link>/post</link>
          <content:encoded>&lt;p&gt;Hello&lt;/p&gt;&lt;script&gt;bad()&lt;/script&gt;</content:encoded></item>
          </channel></rss>'''
        with patch.object(sources, "get", return_value=(feed, "utf-8", "https://example.com/feed")):
            row = sources.rss(settings()["sources"][0], NOW)[0]
        self.assertEqual(row["id"], sources.digest("same-id"))
        self.assertEqual(row["title"], "New & useful")
        self.assertEqual(row["body"], "Hello")
        self.assertEqual(row["url"], "https://example.com/post")

    def test_atom_alternate_link_and_unsafe_links(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>a</id><title>Atom</title>
        <link rel="self" href="https://example.com/api"/><link href="/news"/>
        <summary>Text</summary><updated>2026-10-30T00:00:00Z</updated></entry></feed>'''
        with patch.object(sources, "get", return_value=(feed, "utf-8", "https://example.com/feed")):
            row = sources.rss(settings()["sources"][0], NOW)[0]
        self.assertEqual(row["url"], "https://example.com/news")
        self.assertEqual(sources.item("x", "X", "", "javascript:alert(1)")["url"], "")

    def test_xml_entities_html_and_malformed_sources_fail(self):
        for payload in [b'<!DOCTYPE rss [<!ENTITY a "secret">]><rss/>',
                        '<!DOCTYPE rss><rss/>'.encode('utf-16'), b'<html><body>not a feed</body></html>', b'<rss>']:
            with patch.object(sources, "get", return_value=(payload, "utf-8", "https://example.com")):
                with self.assertRaises(ValueError): sources.rss(settings()["sources"][0], NOW)

    def test_filters_casefold_any_all_and_exclude(self):
        source = settings(keywords=["Python", "发布"], exclude=["广告"])["sources"][0]
        self.assertTrue(sources.matches(source, entry("1", "PYTHON")))
        source["match"] = "all"
        self.assertFalse(sources.matches(source, entry("1", "PYTHON")))
        self.assertTrue(sources.matches(source, entry("1", "Python 发布")))
        self.assertFalse(sources.matches(source, entry("1", "Python 发布广告")))

    def test_webpage_ignores_script_style_and_whitespace_changes(self):
        source = settings(type="webpage")["sources"][0]
        results = []
        for html in [b'<p>Hello  world</p><script>clock=1</script><style>blue</style>', b'<p>Hello world</p><script>clock=2</script>']:
            with patch.object(sources, "get", return_value=(html, "utf-8", source["url"])):
                results.append(sources.webpage(source, NOW)[0])
        self.assertEqual(results[0]["id"], results[1]["id"])

    def test_github_skips_drafts_and_prereleases_and_reverses_order(self):
        releases = [{"id": 3, "name": "pre", "prerelease": True}, {"id": 2, "name": "new", "body": "notes"},
                    {"id": 1, "name": "old"}, {"id": 0, "draft": True}]
        source = settings(type="github", repository="cli/cli")["sources"][0]
        with patch.object(sources, "get", return_value=(json.dumps(releases).encode(), "utf-8", "https://api.github.com")):
            self.assertEqual([x["title"] for x in sources.github(source, NOW)], ["old", "new"])

    def test_x_adapter_uses_legacy_reset_filter(self):
        import tibo_tweet_mail as mail
        posts = [{"id": "1", "text": "Hello", "created_timestamp": 1}, {"id": "2", "text": "Usage reset now", "created_timestamp": 2}]
        with patch.object(mail, "selected_posts", return_value=posts):
            rows = sources.x(settings(type="x", handle="example", reset_only=True)["sources"][0], NOW)
        self.assertEqual([r["id"] for r in rows], [sources.digest("2")])

    def test_market_respects_hour_and_render_timezone(self):
        source = settings(type="market", hour=10)["sources"][0]
        self.assertEqual(sources.market(source, NOW), [])
        source["hour"] = 8
        with patch("market_digest.digest_body", return_value="quotes") as fetch:
            rows = sources.market(source, NOW)
        self.assertEqual(rows[0]["id"], sources.digest("2026-10-30"))
        self.assertEqual(str(fetch.call_args.args[0].tzinfo), "Asia/Shanghai")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.cfg = settings()
        self.sent = []

    def run_once(self, rows, cfg=None, sender=None):
        return engine.run(cfg or self.cfg, self.path, now=NOW, fetcher=lambda s, n: rows,
                          sender=sender or (lambda *args: self.sent.append(args)))

    def test_first_run_baselines_then_sends_only_new_entries(self):
        self.run_once([entry("1")]); self.assertEqual(self.sent, [])
        self.run_once([entry("1"), entry("2")]); self.assertEqual(len(self.sent), 1)
        self.run_once([entry("1"), entry("2")]); self.assertEqual(len(self.sent), 1)

    def test_initial_send_is_explicit_and_batches_are_bounded(self):
        cfg = settings(initial="send"); cfg["batch_size"] = 1
        report = self.run_once([entry("1"), entry("2")], cfg)
        self.assertEqual((report[0]["sent"], report[0]["pending"]), (1, 1))
        report = self.run_once([], cfg)
        self.assertEqual((report[0]["sent"], report[0]["pending"]), (1, 0))

    def test_pending_saved_before_smtp_and_survives_source_outage(self):
        cfg = settings(initial="send")
        def fail(*args):
            self.assertEqual(len(state.read(self.path)["sources"]["news"]["pending"]), 1)
            raise RuntimeError("private-provider-response")
        report = self.run_once([entry("1", "private-title")], cfg, fail)
        self.assertNotIn("private", json.dumps(report))
        def offline(*args): raise RuntimeError("private-source-url")
        report = engine.run(cfg, self.path, now=NOW, fetcher=offline, sender=lambda *args: self.sent.append(args))
        self.assertEqual(report[0]["sent"], 1)
        self.assertEqual(report[0]["status"], "error")
        self.assertEqual(state.read(self.path)["sources"]["news"]["pending"], [])

    def test_one_broken_source_does_not_block_other_sources(self):
        cfg = settings(initial="send")
        cfg["sources"].append({**cfg["sources"][0], "id": "second"})
        def fetch(source, now):
            if source["id"] == "news": raise RuntimeError("offline")
            return [entry("new")]
        reports = engine.run(cfg, self.path, now=NOW, fetcher=fetch, sender=lambda *args: self.sent.append(args))
        self.assertEqual([r["status"] for r in reports], ["error", "ok"])
        self.assertEqual(len(self.sent), 1)

    def test_webpage_returning_to_previous_content_is_a_new_change(self):
        cfg = settings(type="webpage")
        for identity in ("A", "B", "A", "A"):
            self.run_once([entry(identity)], cfg)
        self.assertEqual(len(self.sent), 2)

    def test_filtered_entries_do_not_reappear_when_repolled(self):
        cfg = settings(keywords=["wanted"], initial="send")
        self.run_once([entry("1", "ignored")], cfg)
        self.run_once([entry("1", "now wanted")], cfg)
        self.assertEqual(self.sent, [])

    def test_empty_source_notifies_its_first_future_item(self):
        for kind in ("rss", "github", "x"):
            with self.subTest(kind=kind):
                cfg = settings(type=kind, repository="cli/cli", handle="example")
                before = len(self.sent)
                self.run_once([], cfg)
                self.assertTrue(state.read(self.path)["sources"]["news"]["seeded"])
                self.run_once([entry("first-ever-update")], cfg)
                self.assertEqual(len(self.sent), before + 1)

    def test_initial_source_failure_does_not_create_a_baseline(self):
        def fail(*args): raise RuntimeError("offline")
        engine.run(self.cfg, self.path, now=NOW, fetcher=fail, sender=lambda *args: self.sent.append(args))
        self.assertFalse(state.read(self.path)["sources"]["news"]["seeded"])

    def test_market_before_due_hour_does_not_create_a_baseline(self):
        self.run_once([], settings(type="market"))
        self.assertFalse(state.read(self.path)["sources"]["news"]["seeded"])

    def test_config_change_does_not_silently_discard_pending(self):
        cfg = settings(initial="send")
        def fail(*args): raise RuntimeError()
        self.run_once([entry("1")], cfg, fail)
        cfg["sources"][0]["url"] = "https://example.org/feed"
        result = self.run_once([], cfg)
        self.assertEqual(result[0]["status"], "error")
        self.assertEqual(len(state.read(self.path)["sources"]["news"]["pending"]), 1)

    def test_disabled_source_pauses_pending_without_erasing_it(self):
        cfg = settings(initial="send")
        def fail(*args): raise RuntimeError()
        self.run_once([entry("1")], cfg, fail)
        cfg["sources"][0]["enabled"] = False
        self.assertEqual(self.run_once([], cfg), [])
        self.assertEqual(len(state.read(self.path)["sources"]["news"]["pending"]), 1)

    def test_market_is_fetched_and_sent_once_per_local_day(self):
        cfg = settings(type="market")
        with patch.object(sources, "market", return_value=[]):
            rows = [entry("2026-10-30")]
            self.run_once(rows, cfg)
            def no_fetch(*args): raise AssertionError("already sent")
            result = engine.run(cfg, self.path, now=NOW, fetcher=no_fetch, sender=lambda *args: self.sent.append(args))
        self.assertEqual(result[0]["status"], "ok")
        self.assertEqual(len(self.sent), 1)

    def test_corrupt_checkpoint_stops_before_network_or_smtp(self):
        self.path.write_text('{"secret":"bad"}')
        def forbidden(*args): self.fail("unexpected call")
        with self.assertRaises(ValueError): engine.run(self.cfg, self.path, fetcher=forbidden, sender=forbidden)

    def test_failed_state_write_cannot_submit_email(self):
        with patch.object(state, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.run_once([entry("1")], settings(initial="send"))
        self.assertEqual(self.sent, [])

    def test_cloud_snapshots_are_saved_before_and_after_submission(self):
        events = []
        def checkpoint(path):
            pending = state.read(path)["sources"]["news"]["pending"]
            events.append(("save", len(pending)))
        def send(*args):
            self.assertEqual(events[-1], ("save", 1))
            events.append(("send", 1))
        engine.run(settings(initial="send"), self.path, now=NOW, fetcher=lambda *args: [entry("1")],
                   sender=send, checkpoint=checkpoint)
        self.assertEqual(events, [("save", 0), ("save", 1), ("send", 1), ("save", 0)])

    def test_cloud_failure_before_smtp_stops_delivery(self):
        def checkpoint(path):
            if state.read(path)["sources"]["news"]["pending"]:
                raise RuntimeError("push unavailable")
        with self.assertRaises(RuntimeError):
            engine.run(settings(initial="send"), self.path, now=NOW, fetcher=lambda *args: [entry("1")],
                       sender=lambda *args: self.sent.append(args), checkpoint=checkpoint)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(state.read(self.path)["sources"]["news"]["pending"]), 1)

    def test_global_capacity_failure_still_drains_existing_queue(self):
        cfg = settings(initial="send")
        def fail(*args): raise RuntimeError("offline")
        self.run_once([entry("1")], cfg, fail)
        original_write = state.write
        def limited(path, data):
            if len(data["sources"]["news"]["pending"]) > 1:
                raise ValueError("capacity")
            return original_write(path, data)
        with patch.object(state, "write", side_effect=limited):
            report = self.run_once([entry("2")], cfg)
        self.assertEqual(report[0]["sent"], 1)
        self.assertNotIn(sources.digest("2"), state.read(self.path)["sources"]["news"]["seen"])

    def test_duplicate_batch_and_queue_capacity(self):
        cfg = settings(initial="send")
        self.run_once([entry("1"), entry("1")], cfg)
        self.assertEqual(self.sent[0][1], "[InfoRelay] News · 1 条更新")
        source = cfg["sources"][0]
        entry_state = engine.fresh(engine.fingerprint(source, cfg["timezone"]))
        with patch.object(state, "MAX_PENDING", 1):
            with self.assertRaises(ValueError): engine.collect(entry_state, source, [entry("a"), entry("b")])
        self.assertEqual(entry_state["pending"], [])


class CommandTests(unittest.TestCase):
    def test_preview_has_no_mail_or_state_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "state.json"
            with patch.dict(os.environ, {"INFO_RELAY_CONFIG": json.dumps(settings())}), \
                 patch.object(sources, "fetch", return_value=[entry("1")]), \
                 patch.object(engine, "run") as run, redirect_stdout(io.StringIO()):
                self.assertEqual(main(["preview", "--state", str(checkpoint)]), 0)
                run.assert_not_called()
            self.assertFalse(checkpoint.exists())

    def test_init_never_overwrites_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            target = Path(directory) / "config.json"
            main(["init", "--config", str(target)])
            original = target.read_bytes()
            with self.assertRaises(FileExistsError): main(["init", "--config", str(target)])
            self.assertEqual(target.read_bytes(), original)


class EncryptedCloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"; self.remote = self.root / "remote.git"
        for args in [["init", "--bare", str(self.remote)], ["init", str(self.repo)]]:
            subprocess.run(["git", *args], check=True, capture_output=True)
        self.old = Path.cwd(); os.chdir(self.repo); self.addCleanup(os.chdir, self.old)
        cloud.git("config", "user.name", "Test"); cloud.git("config", "user.email", "test@example.com")
        (self.repo / "source.txt").write_text("source")
        cloud.git("add", "source.txt"); cloud.git("commit", "-m", "initial")
        cloud.git("remote", "add", "origin", str(self.remote))
        self.key = Fernet.generate_key().decode()
        self.env = patch.dict(os.environ, {"INFO_RELAY_STATE_KEY": self.key}); self.env.start(); self.addCleanup(self.env.stop)

    def test_two_runners_restore_pending_without_publishing_plaintext(self):
        remote_state = self.root / "encrypted-1"; local = self.root / "local-1.json"
        cloud.restore(remote_state, local)
        cfg = settings(initial="send"); source = cfg["sources"][0]
        data = state.empty(); data["sources"]["news"] = engine.fresh(engine.fingerprint(source, cfg["timezone"]))
        data["sources"]["news"], _ = engine.collect(data["sources"]["news"], source, [entry("private-id", "private-title", "private-content")])
        state.write(local, data); cloud.save(remote_state, local)
        blob = (remote_state / cloud.BLOB).read_bytes()
        self.assertNotIn(b"private", blob)
        self.assertEqual(cloud.git("ls-tree", "--name-only", "HEAD", cwd=remote_state).stdout.strip(), cloud.BLOB)
        second = self.root / "local-2.json"
        cloud.restore(self.root / "encrypted-2", second)
        self.assertEqual(state.read(second), data)
        before = cloud.git("rev-parse", "HEAD", cwd=remote_state).stdout
        cloud.save(remote_state, local)
        self.assertEqual(cloud.git("rev-parse", "HEAD", cwd=remote_state).stdout, before)

    def test_wrong_key_tampering_and_missing_file_fail_closed(self):
        directory = self.root / "encrypted"; local = self.root / "local.json"
        cloud.restore(directory, local)
        blob = (directory / cloud.BLOB).read_bytes()
        with patch.dict(os.environ, {"INFO_RELAY_STATE_KEY": Fernet.generate_key().decode()}):
            with self.assertRaises(ValueError): cloud.decode(blob)
        with self.assertRaises(ValueError): cloud.decode(blob[:-10] + b"tampered")
        local.unlink()
        with self.assertRaises(ValueError): cloud.save(directory, local)

    def test_unexpected_staged_file_is_not_published(self):
        directory = self.root / "encrypted"; local = self.root / "local.json"
        cloud.restore(directory, local)
        (directory / "config.json").write_text("sensitive")
        cloud.git("add", "config.json", cwd=directory)
        with self.assertRaises(ValueError): cloud.save(directory, local)

    def test_unreachable_remote_never_bootstraps_empty_state(self):
        cloud.git("remote", "set-url", "origin", str(self.root / "missing.git"))
        with self.assertRaises(subprocess.CalledProcessError): cloud.restore(self.root / "encrypted", self.root / "local.json")


if __name__ == "__main__":
    unittest.main()
