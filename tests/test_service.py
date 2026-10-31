import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pixelctl.service import install


class ServiceTests(unittest.TestCase):
    def test_plugin_in_support_root_is_not_recursively_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            user_home = Path(folder)
            support = user_home / "Library/Application Support/Pixelctl"
            support.mkdir(parents=True)
            plugin = support / "my_plugin.py"
            plugin.write_text("def register(registry): pass\n")
            config = support / "config.json"
            config.write_text(json.dumps({"plugins": ["./my_plugin.py"]}))

            def command(arguments, **kwargs):
                return subprocess.CompletedProcess(arguments, 1 if "print" in arguments else 0)

            with patch("pixelctl.service.home", return_value=support), patch("pixelctl.service.Path.home", return_value=user_home), patch("pixelctl.service.sys.platform", "darwin"), patch("pixelctl.service.subprocess.run", side_effect=command):
                installed = install(config, support / "state")
            self.assertEqual(json.loads(installed.read_text())["plugins"], [str(plugin.resolve())])
            self.assertFalse((support / "plugins/plugin-0").exists())
