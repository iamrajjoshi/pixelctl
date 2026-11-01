"""Exercise the real engine with missing cloud credentials and every local page."""
import json
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

PAGES = ["clock", "prs", "deploy", "garden", "health", "uptime", "identity", "date", "dayprogress", "weekprogress", "yearprogress", "worldclock", "text", "countdown", "system", "network", "binaryclock"]


class EngineTest(unittest.TestCase):
    def setup_files(self, root):
        (root / "pages").write_text("\n".join(PAGES) + "\n")
        (root / "key").write_text("engine-test-key")
        config = {"pages_file": str(root / "pages"), "key_file": str(root / "key"), "uptime_file": str(root / "uptime.state"), "status_file": str(root / "status.json"), "timezone": "UTC0", "text": "HELLO", "identity_label": "PIXELCTL", "github_login": "example"}
        (root / "config.json").write_text(json.dumps(config))
        return root / "config.json"

    def test_all_builtin_pages_render_without_cloud_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.setup_files(root)
            output = root / "frames"; output.mkdir()
            subprocess.run([sys.argv[1], "--config", str(config), "--once", "--render-dir", str(output)], capture_output=True, check=True, timeout=10)
            self.assertEqual({path.stem for path in output.glob("*.rgb")}, set(PAGES))
            self.assertTrue(all(path.stat().st_size == 2496 for path in output.glob("*.rgb")))
            status = json.loads((root / "status.json").read_text())
            self.assertEqual(status["mode"], "standalone")
            self.assertFalse(status["prs"]["complete"])
            self.assertIn("token", status["prs"]["error"].lower())
            self.assertEqual(status["deploy"]["serving_error"], "")
            self.assertEqual(status["deploy"]["serving_verified_at"], 0)
            self.assertEqual(status["uptime"]["samples"], 0)
            self.assertEqual(status["uptime"]["availability_pct"], -1)

    def test_slow_cloud_collection_does_not_block_health_or_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.setup_files(root)
            helper = root / "https-helper"
            helper.write_text("#!/usr/bin/env python3\nimport json,sys,time\nif 'api.github.com' in sys.argv[1]: time.sleep(5)\nprint(json.dumps({'status':200,'tls_verified':True,'hostname_verified':True,'body':json.dumps({'status':'ok'})}))\n")
            helper.chmod(0o700); (root / "token").write_text("fixture-token")
            data = json.loads(config.read_text()); data.update({"https_helper": str(helper), "github_token_file": str(root / "token"), "health_url": "https://example.test/health"})
            config.write_text(json.dumps(data)); output = root / "frames"; output.mkdir()
            process = subprocess.Popen([sys.argv[1], "--config", str(config), "--render-dir", str(output)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                deadline = time.monotonic() + 3
                status = {}
                while time.monotonic() < deadline:
                    try:
                        status = json.loads((root / "status.json").read_text())
                        if status.get("health_cycles", 0): break
                    except (OSError, ValueError): pass
                    time.sleep(.05)
                self.assertEqual(status["health"]["state"], 1)
                self.assertEqual(status["uptime"]["samples"], 1)
                self.assertEqual(status["provider_cycles"], 0)
                self.assertGreaterEqual(status["frames_sent"], len(PAGES))
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=1)

    def test_engine_sends_every_page_to_authenticated_receiver(self):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", 9000))
            except OSError:
                self.skipTest("Local receiver port 9000 is in use")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.setup_files(root)
            receiver = subprocess.Popen([sys.argv[2], "--simulate", "--port", "9000", "--token-file", str(root / "key"), "--pages-file", str(root / "pages")], stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9000/status", headers={"X-Pixelctl-Key": "engine-test-key"}), timeout=1) as response:
                            json.load(response)
                        break
                    except urllib.error.URLError:
                        time.sleep(.01)
                subprocess.run([sys.argv[1], "--config", str(config), "--once"], capture_output=True, check=True, timeout=10)
                with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9000/status", headers={"X-Pixelctl-Key": "engine-test-key"}), timeout=1) as response:
                    status = json.load(response)
                self.assertEqual(status["frames_received"], len(PAGES))
                self.assertTrue(all(value > 0 for value in status["updated"]))
            finally:
                receiver.terminate(); receiver.wait(timeout=3)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
