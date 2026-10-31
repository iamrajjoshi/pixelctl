import tempfile
import unittest
from pathlib import Path

from pixelctl.device import prepare


class DeviceSettingsTests(unittest.TestCase):
    def test_generic_pages_timezone_and_theme_are_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {
                "token_file": str(root / "pair.key"), "pages": ["clock", "custom_page"],
                "timezone_posix": "UTC0", "rotate_seconds": 12, "brightness": 35,
                "theme": {"foreground": "#FFEECC", "accent": [10, 20, 30]},
            }
            key, pages, args = prepare(config, root / "settings")
            self.assertEqual(pages.read_text().splitlines(), ["clock", "custom_page"])
            values = args.read_text().splitlines()
            self.assertEqual(values[values.index("--timezone") + 1], "UTC0")
            self.assertEqual(values[values.index("--clock-color") + 1], "FFEECC")
            self.assertEqual(values[values.index("--accent-color") + 1], "0A141E")
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(key.read_text()), 64)

    def test_reconfiguration_preserves_pairing_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {
                "token_file": str(root / "pair.key"), "pages": ["clock", "identity"],
                "timezone_posix": "UTC0", "rotate_seconds": 8, "brightness": 40, "theme": {},
            }
            key, _pages, args = prepare(config, root / "settings")
            original = key.read_bytes()
            prepare(config, root / "settings")
            self.assertEqual(key.read_bytes(), original)
            self.assertIn("--static-page\nidentity\n", args.read_text())
