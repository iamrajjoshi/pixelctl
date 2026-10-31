import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pixelctl.config import read_json, save_json
from pixelctl.transport import Display


class TransportTests(unittest.TestCase):
    def test_native_wire_format_is_exact_rgb(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "device.key"
            key.write_text("test-key")
            display = Display("192.0.2.10", key)
            with patch.object(display, "request") as request:
                display.frame("clock", Image.new("RGBA", (52,16), (5,9,13,255)))
            path, payload = request.call_args.args
            self.assertEqual(path, "/frame/clock")
            self.assertEqual(payload, bytes([5,9,13]) * 832)

    def test_invalid_frame_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "device.key"
            key.write_text("test-key")
            display = Display("192.0.2.10", key)
            with patch.object(display, "request") as request:
                with self.assertRaises(ValueError):
                    display.frame("clock", Image.new("RGB", (16,52)))
            request.assert_not_called()

    def test_saved_state_is_private_atomic_and_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            save_json(path, {"uptime": {"samples": 12}})
            save_json(path, {"uptime": {"samples": 13}})
            self.assertEqual(read_json(path, {})["uptime"]["samples"], 13)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_suffix(".tmp").exists())
