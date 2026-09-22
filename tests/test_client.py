"""Unit tests for the Python SDK against an in-process HTTP server.

Standard library only — no pip installs, runs anywhere python3 runs.
The local server records requests so assertions cover the WIRE (headers,
method, path, body), not just return values.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from agentisend import AgentiSend, AgentiSendError, AgentiSendTransportError, idempotency_key


class FakeApiHandler(BaseHTTPRequestHandler):
    """Programmable responses + request journal."""

    server_version = "FakeAgentiSend/1.0"

    def log_message(self, *args: Any) -> None:  # silence
        return

    def _respond(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        record: Dict[str, Any] = {
            "method": self.command,
            "path": self.path,
            "authorization": self.headers.get("authorization"),
            "idempotency_key": self.headers.get("idempotency-key"),
            "user_agent": self.headers.get("user-agent"),
            "body": json.loads(raw_body.decode("utf-8")) if raw_body else None,
        }
        journal.append(record)

        status, payload, extra_headers = routes.pop(0) if routes else (200, {"ok": True}, {})
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("x-request-id", "py-test-req-1")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond
    do_PATCH = _respond
    do_DELETE = _respond


journal: List[Dict[str, Any]] = []
routes: List[Tuple[int, Any, Optional[Dict[str, str]]]] = []


def quiet_http_server(handler: type) -> ThreadingHTTPServer:
    """ThreadingHTTPServer without the getfqdn() in server_bind.

    stock HTTPServer resolves the FQDN on bind, and on macOS that can stall
    for seconds-to-forever on .local names. The SDK never sees this; only the
    test harness cares."""
    import socketserver

    class Quiet(ThreadingHTTPServer):
        def server_bind(self) -> None:
            socketserver.TCPServer.server_bind(self)
            host, port = self.server_address[:2]
            self.server_name = str(host)
            self.server_port = int(port)

    return Quiet(("127.0.0.1", 0), handler)


class ClientTest(unittest.TestCase):
    httpd: ThreadingHTTPServer
    base_url: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = quiet_http_server(FakeApiHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self) -> None:
        journal.clear()
        routes.clear()
        self.client = AgentiSend(api_key="as_" + "x" * 64, base_url=self.base_url)

    def test_send_email_posts_json_with_bearer_and_idempotency(self) -> None:
        routes.append((201, {"id": "em_1"}, None))
        out = self.client.send_email(
            {"from": "a@b.test", "to": ["c@d.test"], "subject": "hi"},
            idempotency_key("welcome-email", "user_123"),
        )
        self.assertEqual(out, {"id": "em_1"})
        req = journal[0]
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["path"], "/emails")
        self.assertEqual(req["authorization"], f"Bearer as_{'x' * 64}")
        self.assertEqual(req["idempotency_key"], "welcome-email/user_123")
        self.assertEqual(req["body"]["subject"], "hi")

    def test_error_envelope_surfaces_code_message_fix_and_retry_after(self) -> None:
        routes.append(
            (
                429,
                {
                    "error": {
                        "code": "rate_ceiling_exceeded",
                        "message": "Per-minute rate ceiling for this key is exhausted.",
                        "fix": "Slow down, or raise rate_ceiling_per_minute via PATCH /limits/keys/:id.",
                        "docs_url": "https://docs.agentisend.dev/errors#rate_ceiling_exceeded",
                    }
                },
                {"retry-after": "17"},
            )
        )
        with self.assertRaises(AgentiSendError) as ctx:
            self.client.list_emails({"limit": 10})
        err = ctx.exception
        self.assertEqual(err.code, "rate_ceiling_exceeded")
        self.assertIn("PATCH /limits/keys", err.fix)
        self.assertEqual(err.status, 429)
        self.assertEqual(err.retry_after_seconds, 17)
        self.assertEqual(err.request_id, "py-test-req-1")

    def test_non_json_error_becomes_transport_error(self) -> None:
        # A 500 with a non-envelope body.
        class BrokenHandler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                body = b"<html>boom</html>"
                self.send_response(500)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        broken = quiet_http_server(BrokenHandler)
        broken_thread = threading.Thread(target=broken.serve_forever, daemon=True)
        broken_thread.start()
        try:
            bad_client = AgentiSend(api_key="as_" + "y" * 64, base_url=f"http://127.0.0.1:{broken.server_address[1]}")
            with self.assertRaises(AgentiSendTransportError):
                bad_client.list_emails()
        finally:
            broken.shutdown()
            broken.server_close()

    def test_batch_returns_per_item_results_verbatim(self) -> None:
        results = [
            {"index": 0, "status": "accepted", "id": "em_a"},
            {"index": 1, "status": "rejected", "error": {"code": "invalid_from_address", "message": "m", "fix": "f"}},
        ]
        routes.append((200, {"data": results}, None))
        out = self.client.send_email_batch([
            {"from": "a@b.test", "to": "c@d.test", "subject": "one"},
            {"from": "not-an-address", "to": "c@d.test", "subject": "two"},
        ])
        self.assertEqual(out["data"][0]["status"], "accepted")
        self.assertEqual(out["data"][1]["status"], "rejected")
        self.assertEqual(journal[0]["path"], "/emails/batch")

    def test_snake_case_survives_round_trip_untouched(self) -> None:
        routes.append((201, {"id": "em_2"}, None))
        self.client.send_email(
            {"from": "a@b.test", "to": "c@d.test", "subject": "s", "reply_to": "r@b.test", "scheduled_at": None}
        )
        sent = json.dumps(journal[0]["body"])
        self.assertNotIn("replyTo", sent)
        self.assertNotIn("scheduledAt", sent)
        self.assertIn("reply_to", sent)

    def test_query_params_render_and_skip_nones(self) -> None:
        routes.append((200, {"data": [], "has_more": False}, None))
        self.client.list_dead_letters("wh_1", limit=5, cursor=None)
        self.assertEqual(journal[0]["path"], "/webhooks/wh_1/dead-letters?limit=5")

    def test_missing_api_key_raises_immediately(self) -> None:
        saved = dict(__import__("os").environ)
        __import__("os").environ.pop("AGENTISEND_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                AgentiSend(base_url=self.base_url)
        finally:
            __import__("os").environ.update(saved)


if __name__ == "__main__":
    unittest.main()
