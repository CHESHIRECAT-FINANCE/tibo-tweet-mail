import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from info_relay.service import Controller, BusyError
from info_relay.server import make_server


def settings():
    return {"version": 1, "timezone": "UTC", "sources": [{"id": "feed", "type": "rss", "url": "https://example.com/feed"}], "email": {"smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_user": "sender@example.com", "smtp_password": "test-secret-only", "to": "recipient@example.com"}}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.controller = Controller(self.tmp.name)
        self.controller.save_config(settings())

    def test_saved_secret_is_never_returned_and_blank_preserves_it(self):
        result = self.controller.read_config()
        self.assertNotIn("test-secret-only", json.dumps(result))
        self.assertTrue(result["has_password"])
        cfg = result["config"]; cfg["sources"][0]["name"] = "Edited"
        self.controller.save_config(cfg)
        self.assertEqual(json.loads(self.controller.config_path.read_text())["email"]["smtp_password"], "test-secret-only")

    def test_blank_password_cannot_follow_a_changed_sender(self):
        cfg = self.controller.read_config()["config"]; cfg["email"]["smtp_user"] = "different@example.com"
        with self.assertRaises(ValueError): self.controller.save_config(cfg)

    def test_preview_and_diagnose_never_deliver_or_write_checkpoints(self):
        from info_relay.sources import item
        with patch("info_relay.service.sources.fetch", return_value=[item("1", "Title", "Body")]), patch("info_relay.service.delivery.diagnose", return_value="ok"), patch("info_relay.service.delivery.make_sender") as send:
            self.assertTrue(self.controller._perform("diagnose")["ok"])
            self.assertEqual(self.controller._perform("preview")["reports"][0]["items"][0]["title"], "Title")
        send.assert_not_called(); self.assertFalse(self.controller.state_path.exists())

    def test_test_email_submits_exactly_one_message(self):
        with patch("info_relay.service.delivery.make_sender") as factory:
            result = self.controller._perform("test-email")
        self.assertEqual(factory.return_value.call_count, 1); self.assertEqual(result["code"], "test_submitted")
        self.assertFalse(self.controller.state_path.exists())

    def test_provider_exceptions_do_not_leak_secrets(self):
        with patch("info_relay.service.delivery.make_sender", side_effect=RuntimeError("test-secret-only")):
            self.controller._perform("test-email")
        self.assertNotIn("test-secret-only", json.dumps(self.controller.status()))

    def test_scheduler_runs_immediately_and_stop_finishes_current_cycle(self):
        started, release = threading.Event(), threading.Event(); calls = []
        def run(*args, **kwargs):
            calls.append(1); started.set(); release.wait(3); return [{"id": "feed", "status": "ok", "sent": 0}]
        self.controller.run_cycle = run
        with patch("info_relay.service.delivery.make_sender"):
            self.controller.start(60)
            self.assertTrue(started.wait(2))
            with self.assertRaises(BusyError): self.controller.start(60)
            with self.assertRaises(BusyError): self.controller.submit("run")
            with self.assertRaises(BusyError): self.controller.save_config(settings())
            self.assertTrue(self.controller.stop()["finishing_current_task"])
            release.set(); self.controller._thread.join(3)
        self.assertEqual(len(calls), 1); self.assertFalse(self.controller.status()["running"])
        self.assertIsNone(self.controller.status()["next_run"])

    def test_simultaneous_manual_operations_are_rejected(self):
        started, release = threading.Event(), threading.Event()
        def run(*args, **kwargs): started.set(); release.wait(3); return []
        self.controller.run_cycle = run; self.controller.submit("run"); self.assertTrue(started.wait(2))
        try:
            with self.assertRaises(BusyError): self.controller.submit("run")
        finally:
            release.set(); self.controller._task_thread.join(3)

    def test_wizard_cannot_overwrite_an_advanced_configuration(self):
        cfg = settings(); cfg["sources"][0]["processors"] = [{"type": "prefix", "options": {"text": "Custom "}}]
        self.controller.config_path.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError): self.controller.save_config(settings())
        self.assertEqual(json.loads(self.controller.config_path.read_text()), cfg)

    def test_invalid_interval_and_empty_active_sources_rejected(self):
        for value in (0, True, 59, 86401):
            with self.assertRaises(ValueError): self.controller.start(value)
        cfg = settings(); cfg["sources"][0]["enabled"] = False; self.controller.save_config(cfg)
        with patch("info_relay.service.delivery.make_sender"), self.assertRaises(ValueError): self.controller.start()


class ConsoleHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.controller = Controller(self.tmp.name)
        self.server = make_server(self.controller, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        _, payload, _ = self.request("GET", "/api/session", headers={"X-InfoRelay-Client": "1"})
        self.token = json.loads(payload)["token"]

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, method, path, data=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps(data) if data is not None else None
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse(); result = response.status, response.read().decode(), dict(response.getheaders()); client.close(); return result

    def post(self, path, data, **headers):
        return self.request("POST", path, data, {"Content-Type": "application/json", "Origin": self.origin, "X-InfoRelay-Token": self.token, **headers})

    def test_only_explicit_assets_served_and_private_paths_rejected(self):
        self.controller.save_config(settings())
        for path in ("/subscriptions.json", "/../.inforelay/subscriptions.json", "/.git/config", "/%2e%2e/config.json"):
            self.assertEqual(self.request("GET", path)[0], 404)
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200); self.assertNotIn("test-secret-only", body)
        self.assertEqual(headers["X-Frame-Options"], "DENY"); self.assertEqual(headers["Cache-Control"], "no-store")

    def test_cross_origin_host_and_missing_session_cannot_access_api(self):
        self.assertEqual(self.request("GET", "/api/config")[0], 403)
        self.assertEqual(self.post("/api/config", settings(), Origin="https://evil.example")[0], 403)
        self.assertEqual(self.post("/api/config", settings(), Host="evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"X-InfoRelay-Client": "1", "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session")[0], 403)

    def test_config_save_reload_redaction_and_status(self):
        self.assertEqual(self.post("/api/config", settings())[0], 200)
        status, body, _ = self.request("GET", "/api/config", headers={"X-InfoRelay-Token": self.token})
        self.assertEqual(status, 200); self.assertNotIn("test-secret-only", body)
        self.assertTrue(json.loads(body)["has_password"])
        self.assertEqual(self.request("GET", "/api/status", headers={"X-InfoRelay-Token": self.token})[0], 200)

    def test_plain_form_and_bad_config_rejected(self):
        self.assertEqual(self.post("/api/config", settings(), **{"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post("/api/config", {"unexpected": "field"})[0], 422)
        self.assertEqual(self.post("/api/task", {"operation": "shell"})[0], 422)

    def test_session_rotates_after_server_restart(self):
        other = make_server(self.controller, 0)
        thread = threading.Thread(target=other.serve_forever, daemon=True); thread.start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", other.server_port, timeout=3)
            client.request("GET", "/api/config", headers={"X-InfoRelay-Token": self.token})
            response = client.getresponse(); self.assertEqual(response.status, 403); response.read(); client.close()
        finally:
            other.shutdown(); other.server_close(); thread.join()


if __name__ == "__main__": unittest.main()
