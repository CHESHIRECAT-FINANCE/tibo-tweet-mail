"""Loopback-only console. Explicit asset allowlist; authenticated same-origin API.

This is a personal desktop companion, not a public/multi-user application server.
"""
from __future__ import annotations

import json
import secrets
import sysconfig
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .service import Controller, BusyError
from .storage import file_lease

ASSETS = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
          "/style.css": ("style.css", "text/css"), "/model.js": ("model.js", "text/javascript"),
          "/app.js": ("app.js", "text/javascript"), "/i18n.js": ("i18n.js", "text/javascript")}
MAX_REQUEST = 100_000


def asset_directory():
    packaged = Path(__file__).parent / "web"
    if packaged.is_dir():
        return packaged
    checkout = Path(__file__).resolve().parent.parent / "web"
    return checkout if checkout.is_dir() else Path(sysconfig.get_path("data")) / "share" / "inforelay" / "web"


def make_server(controller, port=8766, assets=None):
    asset_root = Path(assets) if assets else asset_directory()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No request bodies, query strings, credentials or provider output in logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def _send(self, code, body, kind="application/json"):
            if kind == "application/json":
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self, authenticated=False):
            address = "127.0.0.1:" + str(self.server.server_port)
            origin = self.headers.get("Origin")
            if self.headers.get("Host") != address or (origin and origin != "http://" + address):
                self._send(403, {"error": "origin_rejected"}); return False
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                self._send(403, {"error": "origin_rejected"}); return False
            if authenticated and not secrets.compare_digest(self.headers.get("X-InfoRelay-Token", ""), token):
                self._send(403, {"error": "session_required"}); return False
            return True

        def do_GET(self):
            if not self._allowed():
                return
            path = urlsplit(self.path).path
            if path == "/api/session":
                if self.headers.get("X-InfoRelay-Client") != "1":
                    self._send(403, {"error": "client_required"}); return
                self._send(200, {"token": token, "mode": "local"}); return
            if path.startswith("/api/"):
                if not self._allowed(authenticated=True):
                    return
                try:
                    if path == "/api/status": self._send(200, controller.status())
                    elif path == "/api/config": self._send(200, controller.read_config())
                    else: self._send(404, {"error": "not_found"})
                except Exception:
                    self._send(422, {"error": "config_unreadable"})
                return
            if path not in ASSETS:
                self._send(404, {"error": "not_found"}); return
            name, kind = ASSETS[path]
            try:
                self._send(200, (asset_root / name).read_bytes(), kind)
            except OSError:
                self._send(404, {"error": "asset_missing"})

        def do_POST(self):
            if not self._allowed(authenticated=True):
                return
            if self.headers.get("Origin") != "http://127.0.0.1:" + str(self.server.server_port):
                self._send(403, {"error": "origin_required"}); return
            if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                self._send(415, {"error": "json_required"}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    self._send(413, {"error": "request_too_large"}); return
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                path = urlsplit(self.path).path
                if path == "/api/config": result = controller.save_config(data)
                elif path == "/api/start": result = controller.start(data.get("interval", 600))
                elif path == "/api/stop": result = controller.stop()
                elif path == "/api/task": result = controller.submit(data.get("operation"))
                else:
                    self._send(404, {"error": "not_found"}); return
                self._send(200, result)
            except BusyError:
                self._send(409, {"error": "busy"})
            except (ValueError, TypeError):
                self._send(422, {"error": "invalid_settings"})
            except Exception:
                self._send(500, {"error": "operation_failed"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(directory=".inforelay", port=8766, open_browser=True, registry=None, stop_event=None):
    controller = Controller(directory, registry)
    with file_lease(Path(directory) / "console.lock"):
        server = make_server(controller, port)
        url = f"http://127.0.0.1:{server.server_port}/"
        print("InfoRelay local console / 本地控制台: " + url, flush=True)
        print("Keep this window open. Ctrl+C stops checks. / 请保持此窗口运行，Ctrl+C 停止。", flush=True)
        if open_browser:
            webbrowser.open(url)
        if stop_event is not None:
            def stop_when_requested():
                stop_event.wait()
                server.shutdown()
            threading.Thread(target=stop_when_requested, daemon=True).start()
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        finally:
            controller.stop()
            if controller._thread:
                controller._thread.join()  # Finish persistence before closing, never cancel mid-send.
            if controller._task_thread:
                controller._task_thread.join()
            server.server_close()
