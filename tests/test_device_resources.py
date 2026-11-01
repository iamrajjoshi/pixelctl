"""Validate the resources users install, including native license obligations."""
import hashlib
from importlib.resources import files
import json
import unittest


class DeviceResourcesTest(unittest.TestCase):
    def test_packaged_resources_match_release_manifest(self):
        root = files("pixelctl") / "device_files"
        manifest = json.loads((root / "manifest.json").read_text())
        required = {
            "receiver-armv7", "supervisor-armv7", "standalone-armv7",
            "https-get-armv7", "res-flash-armv7", "libpixelctl.so", "install.py",
            "cacert.pem", "MBEDTLS-LICENSE", "MBEDTLS-NOTICES",
            "TLS-PROVENANCE.json", "MOZILLA-MPL-2.0.txt",
            "JSMN-LICENSE", "JSMN-PROVENANCE.json",
        }
        self.assertEqual(set(manifest), required)
        self.assertEqual({item.name for item in root.iterdir() if item.is_file()}, required | {"manifest.json"})
        for name, expected in manifest.items():
            with self.subTest(resource=name):
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected)

    def test_release_executables_target_arm_and_include_licenses(self):
        root = files("pixelctl") / "device_files"
        for name in ("receiver-armv7", "supervisor-armv7", "standalone-armv7", "https-get-armv7", "res-flash-armv7", "libpixelctl.so"):
            with self.subTest(resource=name):
                header = (root / name).read_bytes()[:20]
                self.assertEqual(header[:6], b"\x7fELF\x01\x01")  # 32-bit, little-endian.
                self.assertEqual(int.from_bytes(header[18:20], "little"), 40)  # EM_ARM.
        self.assertIn("Apache License", (root / "MBEDTLS-LICENSE").read_text())
        self.assertIn("Serge Zaitsev", (root / "JSMN-LICENSE").read_text())
        self.assertIn("Mozilla Public License", (root / "MOZILLA-MPL-2.0.txt").read_text())


if __name__ == "__main__":
    unittest.main()
