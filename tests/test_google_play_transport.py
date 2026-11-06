"""A stalled Play response must not hold the bot's shared check lock forever."""
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import app
from google_play_scraper.utils import request as play_request


class GooglePlayTransportTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        release = self.release

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/slow-headers":
                    release.wait(2)
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    if self.path == "/slow-body":
                        release.wait(2)
                    self.wfile.write(b"{}")
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_stalled_headers_raise_instead_of_waiting_without_a_timeout(self):
        with patch.object(app, "GOOGLE_PLAY_METADATA_TIMEOUT", 0.1, create=True):
            with self.assertRaises(TimeoutError):
                play_request.get(f"{self.base_url}/slow-headers")

    def test_stalled_body_also_times_out(self):
        with patch.object(app, "GOOGLE_PLAY_METADATA_TIMEOUT", 0.1, create=True):
            with self.assertRaises(TimeoutError):
                play_request.get(f"{self.base_url}/slow-body")

    def test_fast_response_still_works_without_changing_global_socket_defaults(self):
        default_timeout = socket.getdefaulttimeout()
        with patch.object(app, "GOOGLE_PLAY_METADATA_TIMEOUT", 0.1, create=True):
            self.assertEqual(play_request.get(f"{self.base_url}/ok"), "{}")
        self.assertEqual(socket.getdefaulttimeout(), default_timeout)


if __name__ == "__main__":
    unittest.main()
