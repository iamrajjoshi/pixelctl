"""Exercise the real native HTTP receiver in desktop simulation mode."""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


class ReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.key = root / "key"
        cls.key.write_text("test-only-key")
        cls.cache = root / "frames"
        cls.cache.mkdir()
        cls.pages = root / "pages"
        cls.pages.write_text("clock\nprs\ndeploy\ngarden\nhealth\nuptime\nidentity\n")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.process = subprocess.Popen(
            [sys.argv[1], "--simulate", "--port", str(cls.port), "--token-file", str(cls.key), "--brightness", "100", "--cache", str(cls.cache), "--pages-file", str(cls.pages), "--static-page", "identity"],
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                cls.request("GET", "/status")
                break
            except urllib.error.URLError:
                time.sleep(0.01)
        else:
            raise RuntimeError("Native receiver did not start")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=3)
        cls.temporary.cleanup()

    @classmethod
    def request(cls, method, path, body=None, authorized=True):
        headers = {"X-Pixelctl-Key": "test-only-key"} if authorized else {}
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.read()

    def test_rejects_unauthenticated_reads_and_writes(self):
        for method, path, body in [("GET", "/status", None), ("GET", "/display", None), ("POST", "/pause", b"")]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request(method, path, body, authorized=False)
            self.assertEqual(raised.exception.code, 403)

    def test_raw_frame_roundtrip_and_page_selection(self):
        payload = bytes(range(256)) * 9 + bytes(range(192))
        self.assertEqual(len(payload), 2496)
        self.request("POST", "/frame/identity", payload)
        self.assertEqual(self.request("GET", "/frame/identity"), payload)
        self.request("POST", "/page/identity", b"")
        status = json.loads(self.request("GET", "/status"))
        self.assertEqual(status["page"], "identity")
        self.assertEqual(self.request("GET", "/display"), payload)

    def test_malformed_frame_cannot_replace_existing_frame(self):
        before = self.request("GET", "/frame/identity")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request("POST", "/frame/identity", b"bad")
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(self.request("GET", "/frame/identity"), before)

    def test_offline_clock_and_stale_data_marker(self):
        self.request("POST", "/page/clock", b"")
        clock = self.request("GET", "/display")
        self.assertEqual(len(clock), 2496)
        self.assertGreater(sum(clock), 0)
        self.request("POST", "/page/health", b"")
        health = self.request("GET", "/display")
        self.assertEqual(health[-156:], bytes([235, 184, 93]) * 52)

    def test_pause_is_reversible(self):
        original = json.loads(self.request("GET", "/status"))["paused"]
        toggled = json.loads(self.request("POST", "/pause", b""))["paused"]
        restored = json.loads(self.request("POST", "/pause", b""))["paused"]
        self.assertNotEqual(original, toggled)
        self.assertEqual(original, restored)

    def test_wrong_method_does_not_pause(self):
        original = json.loads(self.request("GET", "/status"))["paused"]
        with self.assertRaises(urllib.error.HTTPError):
            self.request("GET", "/pause")
        self.assertEqual(json.loads(self.request("GET", "/status"))["paused"], original)

    def test_oversized_authenticated_header_cannot_overflow_body_buffer(self):
        request = b"POST /frame/identity HTTP/1.1\r\nX-Pixelctl-Key: test-only-key\r\nContent-Length: 2496\r\nX-Pad: " + b"A" * 9000 + b"\r\n\r\n" + b"B" * 2496
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
            client.sendall(request)
            response = client.recv(512)
        self.assertIn(b"400", response.split(b"\r\n")[0])
        self.assertTrue(json.loads(self.request("GET", "/status"))["native"])

    def test_slow_drip_request_has_absolute_deadline(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
            def drip():
                for _ in range(15):
                    try:
                        client.sendall(b"G")
                    except OSError:
                        return
                    time.sleep(0.15)
            thread = threading.Thread(target=drip)
            thread.start()
            time.sleep(0.2)
            started = time.monotonic()
            self.assertTrue(json.loads(self.request("GET", "/status"))["native"])
            self.assertLess(time.monotonic() - started, 1.5)
            thread.join(timeout=3)

    def test_cache_explicitly_persists_raw_pages(self):
        frame = bytes([20, 180, 70]) * 832
        self.request("POST", "/frame/garden", frame)
        self.request("POST", "/persist", b"")
        self.assertEqual((self.cache / "garden.rgb").read_bytes(), frame)

    def test_page_config_rejects_unsafe_and_duplicate_ids(self):
        for contents in ["../escape\n", "clock\nclock\n", "", "a" * 33 + "\n"]:
            path = Path(self.temporary.name) / "bad-pages"
            path.write_text(contents)
            result = subprocess.run([sys.argv[1], "--simulate", "--pages-file", str(path)], capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)

    def test_status_exposes_configured_page_order(self):
        status = json.loads(self.request("GET", "/status"))
        self.assertEqual(status["pages"], self.pages.read_text().splitlines())

    def test_auto_rotation_advances_one_page(self):
        if json.loads(self.request("GET", "/status"))["paused"]:
            self.request("POST", "/pause", b"")
        self.request("POST", "/page/clock", b"")
        time.sleep(8.2)
        self.assertEqual(json.loads(self.request("GET", "/status"))["page"], "prs")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
