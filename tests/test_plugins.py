import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pixelctl.config import load, save_json, render_data
from pixelctl.plugins import builtin_registry


class PluginTests(unittest.TestCase):
    def test_minimal_default_needs_no_company_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            config, registry = load(Path(folder) / "missing.json")
            self.assertEqual(config["providers"], [])
            self.assertEqual(config["timezone"], "UTC")
            image = registry.pages["identity"](render_data(config, {}), datetime.now(timezone.utc), 4)
            self.assertEqual(image.size, (52, 16))

    def test_external_plugin_registers_page_theme_and_preset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "plugin.py").write_text('''from PIL import Image
def register(registry):
    registry.register_theme("separate", {"wordmark": "HELLO"})
    registry.register_page("custom", lambda data, now, phase: Image.new("RGB", (52,16), (1,2,3)))
    registry.register_preset("mine", {"pages":["custom"], "theme":"separate"})
''')
            save_json(root / "config.json", {"plugins": ["./plugin.py"], "extends": "mine"})
            config, registry = load(root / "config.json")
            self.assertEqual(config["pages"], ["custom"])
            self.assertEqual(config["theme"]["wordmark"], "HELLO")
            self.assertEqual(registry.pages["custom"]({}, None, 0).getpixel((0,0)), (1,2,3))

    def test_unknown_page_and_duplicate_ids_rejected_before_device_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for pages in (["missing"], ["clock", "clock"], []):
                save_json(path, {"pages": pages})
                with self.assertRaises(ValueError):
                    load(path)

    def test_plugin_cannot_silently_replace_builtin_page(self):
        registry = builtin_registry()
        with self.assertRaises(ValueError):
            registry.register_page("clock", lambda *args: None)

    def test_plugin_with_annotated_dataclass_can_be_imported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "plugin.py").write_text('''from __future__ import annotations
from dataclasses import dataclass
@dataclass
class Options:
    name: str
def register(registry):
    registry.register_theme("typed", {"wordmark": Options("TYPED").name})
''')
            save_json(root / "config.json", {"plugins": ["plugin.py"], "theme": "typed"})
            config, _ = load(root / "config.json")
            self.assertEqual(config["theme"]["wordmark"], "TYPED")
