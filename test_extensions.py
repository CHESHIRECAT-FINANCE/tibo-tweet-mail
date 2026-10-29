import copy
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from info_relay import config, engine, sources, state
from info_relay.builtins import register
from info_relay.plugins import Registry, SourcePlugin, ProcessorPlugin, ChannelPlugin, process_item
from info_relay.storage import JsonStore, SQLiteStore, file_lease

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def registry():
    result = Registry(); register(result); return result


def settings():
    return {"version": 1, "timezone": "UTC", "sources": [{"id": "demo", "type": "rss", "url": "https://example.com/feed", "initial": "send"}], "delivery": {"type": "file", "options": {"directory": "unused"}}}


class ExtensionTests(unittest.TestCase):
    def test_custom_source_processor_and_channel_work_without_core_edits(self):
        r = registry(); received = []
        r.source("demo", SourcePlugin(lambda src, now: [sources.item("1", "Release", "keep"), sources.item("2", "Skip", "drop")], lambda src: None))
        r.processor("keep", ProcessorPlugin(lambda row, opts: row if row["body"] == "keep" else None, lambda opts: None))
        r.channel("record", ChannelPlugin(lambda opts, subject, body, identity: received.append((body, identity)), lambda opts: None))
        cfg = settings(); cfg["sources"][0].update(type="demo", processors=[{"type": "keep"}]); cfg["delivery"] = {"type": "record"}
        cfg = config.validate(cfg, r)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = engine.run(cfg, path, now=NOW, registry=r)
            self.assertEqual(result[0]["sent"], 1)
            self.assertIn("keep", received[0][0]); self.assertNotIn("drop", received[0][0])
            self.assertEqual(len(state.read(path)["sources"]["demo"]["seen"]), 2)
            engine.run(cfg, path, now=NOW, registry=r)
        self.assertEqual(len(received), 1)

    def test_custom_snapshot_reports_a_b_a_transitions(self):
        r = registry(); content = ["A"]
        r.source("snapshot", SourcePlugin(lambda src, now: [sources.item(content[0], "Page", content[0])], lambda src: None, "snapshot"))
        sent = []; r.channel("record", ChannelPlugin(lambda *args: sent.append(args), lambda opts: None))
        cfg = settings(); cfg["sources"][0].update(type="snapshot", initial="skip"); cfg["delivery"] = {"type": "record"}; cfg = config.validate(cfg, r)
        with tempfile.TemporaryDirectory() as directory:
            for value in ("A", "B", "B", "A"):
                content[0] = value; engine.run(cfg, Path(directory) / "state.json", registry=r, now=NOW)
        self.assertEqual(len(sent), 2)
        self.assertNotEqual(sent[0][-1], sent[1][-1])

    def test_failed_custom_channel_retains_stable_retry_identity(self):
        r = registry(); ids = []
        def send(opts, subject, body, identity):
            ids.append(identity)
            if len(ids) == 1: raise OSError("offline")
        r.channel("flaky", ChannelPlugin(send, lambda opts: None))
        cfg = settings(); cfg["delivery"] = {"type": "flaky"}; cfg = config.validate(cfg, r)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            first = engine.run(cfg, path, registry=r, now=NOW, fetcher=lambda *a: [sources.item("one", "Title", "body")])
            self.assertEqual(first[0]["pending"], 1)
            second = engine.run(cfg, path, registry=r, now=NOW, fetcher=lambda *a: [])
            self.assertEqual(second[0]["pending"], 0)
        self.assertEqual(ids[0], ids[1])

    def test_processor_failure_keeps_original_baseline(self):
        r = registry()
        def fail(row, opts): raise RuntimeError("broken")
        r.processor("broken", ProcessorPlugin(fail, lambda opts: None))
        cfg = settings(); cfg["sources"][0]["processors"] = [{"type": "broken"}]; cfg = config.validate(cfg, r)
        with tempfile.TemporaryDirectory() as directory:
            cfg["delivery"]["options"]["directory"] = str(Path(directory) / "out")
            path = Path(directory) / "state.json"
            report = engine.run(cfg, path, registry=r, now=NOW, fetcher=lambda *a: [sources.item("one", "Title", "body")])
            self.assertEqual(report[0]["status"], "error")
            self.assertFalse(state.read(path)["sources"]["demo"]["seeded"])

    def test_processor_cannot_change_identity_or_return_unbounded_content(self):
        for change in ({"id": "wrong"}, {"body": "x" * 10001}):
            r = registry(); r.processor("bad", ProcessorPlugin(lambda row, opts: dict(row, **change), lambda opts: None))
            with self.assertRaises(ValueError): process_item(sources.item("x", "X", ""), [{"type": "bad"}], r)

    def test_unknown_plugins_not_loaded_from_config(self):
        for mutation in ({"plugins": ["os"]}, {"delivery": {"type": "unknown"}}, {"delivery": {"type": "file", "options": {}}}):
            cfg = settings(); cfg.update(mutation)
            with self.assertRaises(ValueError): config.validate(cfg)
        cfg = settings(); cfg["sources"][0]["processors"] = [{"type": "unknown"}]
        with self.assertRaises(ValueError): config.validate(cfg)

    def test_duplicate_and_invalid_registration_rejected(self):
        r = registry()
        with self.assertRaises(ValueError): r.source("rss", r.sources["rss"])
        with self.assertRaises(ValueError): r.source("../code", r.sources["rss"])
        with self.assertRaises(ValueError): r.source("wrong", SourcePlugin(lambda *a: [], lambda *a: None, "invalid"))

    def test_plugin_loading_is_explicit_versioned_and_idempotent(self):
        r = registry(); self.assertNotIn("json_feed", r.sources)
        r.load("examples.custom_plugin"); r.load("examples.custom_plugin")
        self.assertIn("json_feed", r.sources); self.assertIn("min_words", r.processors); self.assertIn("markdown", r.channels)
        with self.assertRaises(ValueError): r.load("os")
        with self.assertRaises(ValueError): r.load("./evil.py")

    def test_failed_registration_is_transactional(self):
        r = registry()
        class Broken:
            INFO_RELAY_API = 1
            @staticmethod
            def register(target):
                target.source("partial", target.sources["rss"])
                raise RuntimeError("fail")
        with patch("info_relay.plugins.importlib.import_module", return_value=Broken), self.assertRaises(RuntimeError): r.load("broken_plugin")
        self.assertNotIn("partial", r.sources)
        self.assertNotIn("broken_plugin", r.loaded_modules)

    def test_file_channel_retries_overwrite_same_batch(self):
        from info_relay.builtins import file_delivery
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2): file_delivery({"directory": directory}, "Subject", "Body", "a" * 64)
            files = list(Path(directory).glob("*.json")); self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())["body"], "Body")

    def test_builtin_config_fingerprint_stays_compatible_with_v01(self):
        # Frozen v0.1 settings should not acquire processor/options defaults during upgrade.
        cfg = config.validate({"sources": [{"id": "feed", "type": "rss", "url": "https://example.com/feed"}]})
        source = cfg["sources"][0]
        self.assertNotIn("processors", source); self.assertNotIn("options", source)
        original = json.dumps([{k: v for k, v in source.items() if k not in ("name", "enabled")}, "Asia/Shanghai"], sort_keys=True, ensure_ascii=False)
        self.assertEqual(engine.fingerprint(source, "Asia/Shanghai"), sources.digest(original))


class StorageTests(unittest.TestCase):
    def test_json_and_sqlite_complete_the_same_delivery_cycle(self):
        for backend in (JsonStore, SQLiteStore):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); cfg = settings(); cfg["delivery"]["options"]["directory"] = str(root / "out")
                cfg = config.validate(cfg); store = backend(root / "checkpoint")
                result = engine.run(cfg, store=store, now=NOW, fetcher=lambda *a: [sources.item("one", "Item", "body")])
                self.assertEqual(result[0]["sent"], 1)
                self.assertEqual(len(store.load()["sources"]["demo"]["seen"]), 1)
                self.assertEqual(len(list((root / "out").glob("*.json"))), 1)

    def test_sqlite_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.db"; path.write_bytes(b"not sqlite")
            with self.assertRaises(ValueError): SQLiteStore(path).load()

    def test_sqlite_failure_does_not_erase_previous_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "state.db"); store.save(state.empty())
            with self.assertRaises(ValueError): store.save({"invalid": True})
            self.assertEqual(store.load(), state.empty())

    def test_overlapping_runs_cannot_take_same_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.lock"
            with file_lease(path):
                with self.assertRaises((RuntimeError, OSError)):
                    with file_lease(path): pass
            with file_lease(path): pass

    def test_custom_store_and_cloud_callback_cannot_be_combined(self):
        with self.assertRaises(ValueError): engine.run(config.validate(settings()), store=object(), checkpoint=lambda p: None)

    def test_store_failure_happens_before_any_delivery(self):
        class BrokenStore:
            def load(self): return state.empty()
            def save(self, data): raise OSError("disk full")
            @contextmanager
            def lease(self): yield
        r = registry(); calls = []
        r.channel("record", ChannelPlugin(lambda *args: calls.append(args), lambda opts: None))
        cfg = settings(); cfg["delivery"] = {"type": "record"}; cfg = config.validate(cfg, r)
        with self.assertRaises(OSError): engine.run(cfg, registry=r, store=BrokenStore())
        self.assertEqual(calls, [])


if __name__ == "__main__": unittest.main()
