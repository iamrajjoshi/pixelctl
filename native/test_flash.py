"""Guarded NOR writer contracts against disposable regular files, never hardware."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class FlashTest(unittest.TestCase):
    def test_preflight_write_verify_and_idempotent_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = b"A" * 65536 + b"B" * 65536
            new = b"A" * 65536 + b"C" * 65536
            (root / "old").write_bytes(old)
            (root / "new").write_bytes(new)
            partition = root / "partition"
            partition.write_bytes(old + b"\xff" * (8 * 1024 * 1024 - len(old)))
            args = [sys.argv[1], str(root / "old"), str(root / "new"), "--simulate-file", str(partition)]
            before = partition.read_bytes()
            result = subprocess.run(args, capture_output=True, check=True, text=True)
            self.assertTrue(json.loads(result.stdout)["dry_run"])
            self.assertEqual(partition.read_bytes(), before)
            result = subprocess.run(args + ["--write"], capture_output=True, check=True, text=True)
            self.assertTrue(json.loads(result.stdout.splitlines()[-1])["verified"])
            self.assertEqual(partition.read_bytes(), new + before[len(new):])
            result = subprocess.run(args + ["--write"], capture_output=True, check=True, text=True)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1])["blocks_written"], 0)

    def test_unknown_live_bytes_and_non_erased_tail_are_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "old").write_bytes(b"A" * 65536)
            (root / "new").write_bytes(b"B" * 65536)
            partition = root / "partition"
            args = [sys.argv[1], str(root / "old"), str(root / "new"), "--simulate-file", str(partition), "--write"]
            for data in [b"C" * 65536 + b"\xff" * (8 * 1024 * 1024 - 65536), b"A" * 65536 + b"\x00" * (8 * 1024 * 1024 - 65536)]:
                partition.write_bytes(data)
                result = subprocess.run(args, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(partition.read_bytes(), data)

    def test_explicit_recovery_repairs_a_partially_written_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "old").write_bytes(b"A" * 65536)
            (root / "new").write_bytes(b"B" * 65536)
            partition = root / "partition"
            partition.write_bytes(b"C" * 65536 + b"\xff" * (8 * 1024 * 1024 - 65536))
            args = [sys.argv[1], str(root / "old"), str(root / "new"), "--simulate-file", str(partition), "--write", "--recover-partial"]
            subprocess.run(args, capture_output=True, check=True)
            self.assertEqual(partition.read_bytes()[:65536], b"B" * 65536)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
