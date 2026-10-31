import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pixelctl.cli import main


class CliTests(unittest.TestCase):
    def test_init_is_generic_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "clock.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--config", str(config), "init", "--device", "192.0.2.10"])
            data = json.loads(config.read_text())
            self.assertEqual(data["providers"], [])
            self.assertEqual(data["device_ip"], "192.0.2.10")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["--config", str(config), "init", "--device", "192.0.2.20"])
            self.assertEqual(json.loads(config.read_text()), data)

    def test_plugin_list_works_without_device_key(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["--config", str(Path(folder) / "empty.json"), "plugins", "list"])
            self.assertIn("github", json.loads(output.getvalue())["providers"])
