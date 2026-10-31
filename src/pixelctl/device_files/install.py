#!/usr/bin/env python3
"""Install the TC002 native receiver with a verified, private recovery backup.

The default is a reversible RAM trial. ``--flash`` persists the tested loader in
the res partition; no kernel, rootfs, MCU, Wi-Fi or recovery partition is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request


class Installer:
    def __init__(self, args):
        self.args = args
        self.backup = Path(args.backup_dir).expanduser().resolve()
        self.backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backup.chmod(0o700)
        self.resources = Path(args.resources).resolve()
        self.adb = [args.adb, "-s", args.serial]

    def command(self, command, *, check=True):
        result = subprocess.run(command, text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(f"Command failed: {command[0]}\n{result.stderr.strip()}\n{result.stdout.strip()}")
        return result

    def shell(self, command, *, check=True):
        return self.command(self.adb + ["shell", command], check=check).stdout.strip()

    def push(self, source, destination):
        self.command(self.adb + ["push", str(source), destination])

    def pull(self, source, destination):
        self.command(self.adb + ["pull", source, str(destination)])

    def check_device(self):
        if ":" in self.args.serial:
            self.command([self.args.adb, "connect", self.args.serial])
        mtd = self.shell("cat /proc/mtd")
        firmware = self.shell("getprop ro.firmware")
        if 'mtd3: 00800000 00010000 "res"' not in mtd or "ulanzi_I008" not in firmware:
            raise RuntimeError("Refused: expected the verified TC002/I008 NOR partition layout")
        mac = self.shell("cat /sys/class/net/wlan0/address").lower()
        identity_path = self.backup / "device.json"
        if identity_path.exists() and json.loads(identity_path.read_text())["mac"] != mac:
            raise RuntimeError("Backup directory belongs to a different clock")
        identity_path.write_text(json.dumps({"mac": mac, "firmware": firmware, "mtd": mtd}, indent=2) + "\n")

    def backup_res(self):
        current = self.backup / "res-before.bin"
        self.pull("/dev/mtd/mtd3ro", current)
        content = current.read_bytes()
        if len(content) != 8 * 1024 * 1024 or content[:4] != b"hsqs":
            raise RuntimeError("Refused: res backup has unexpected size or filesystem")
        digest = hashlib.sha256(content).hexdigest()
        (self.backup / "res-before.sha256").write_text(digest + "\n")
        original = self.backup / "res-original.bin"
        if not original.exists():
            # The original backup is immutable across later upgrades.
            original.write_bytes(content)
            original.chmod(0o600)
            (self.backup / "res-original.sha256").write_text(digest + "\n")
        else:
            original_digest = hashlib.sha256(original.read_bytes()).hexdigest()
            digest_path = self.backup / "res-original.sha256"
            if digest_path.exists() and digest_path.read_text().strip() != original_digest:
                raise RuntimeError("Original recovery backup checksum mismatch")
            if not digest_path.exists():
                digest_path.write_text(original_digest + "\n")
        return current

    def wait_native(self, timeout=75):
        key = Path(self.args.key_file).read_text().strip()
        host = self.args.serial.split(":", 1)[0]
        if not self.args.host and ":" not in self.args.serial:
            raise RuntimeError("USB serial needs --host CLOCK_IP for live verification")
        host = self.args.host or host
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                request = urllib.request.Request(f"http://{host}:9000/status", headers={"X-Pixelctl-Key": key})
                with urllib.request.urlopen(request, timeout=2) as response:
                    result = json.load(response)
                if result.get("native"):
                    return result
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Native receiver did not become reachable; keep the clock powered and inspect /tmp/pixelctl-native.log")

    def stage(self):
        self.shell("mkdir -p /data/pixelctl/frames; chmod 700 /data/pixelctl")
        for source, destination in [("receiver-armv7", "/data/pixelctl/receiver"), ("supervisor-armv7", "/data/pixelctl/supervisor")]:
            self.push(self.resources / source, destination + ".new")
            self.shell(f"chmod 700 {destination}.new; mv {destination}.new {destination}")
        self.push(self.args.key_file, "/data/pixelctl.key")
        self.push(self.args.pages_file, "/data/pixelctl/pages")
        self.push(self.args.args_file, "/data/pixelctl/receiver.args")
        self.push(self.resources / "libpixelctl.so", "/tmp/libpixelctl.so")
        self.push(self.resources / "res-flash-armv7", "/tmp/pixelctl-res-flash")
        self.shell("chmod 600 /data/pixelctl.key; chmod 700 /tmp/pixelctl-res-flash")
        if (self.resources / "https-get-armv7").exists():
            self.push(self.resources / "https-get-armv7", "/data/pixelctl/https-get.new")
            self.shell("chmod 700 /data/pixelctl/https-get.new; mv /data/pixelctl/https-get.new /data/pixelctl/https-get")
        if (self.resources / "cacert.pem").exists():
            self.push(self.resources / "cacert.pem", "/data/pixelctl/cacert.pem")
        for name in ["MBEDTLS-LICENSE", "MBEDTLS-NOTICES", "MOZILLA-MPL-2.0.txt", "TLS-PROVENANCE.json"]:
            if (self.resources / name).exists():
                self.push(self.resources / name, "/data/pixelctl/" + name)
        # The embedded kernel has no hardware RNG; seed its CSPRNG from the
        # installer's OS CSPRNG, uniquely for this device. Never publish this file.
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory) / "entropy.seed"
            seed.write_bytes(os.urandom(32)); seed.chmod(0o600)
            self.push(seed, "/data/pixelctl/entropy.seed")
        self.shell("chmod 600 /data/pixelctl/entropy.seed")

    def trial(self):
        # This targets receiver executable paths, never arbitrary processes.
        for line in self.shell("ps").splitlines():
            if "/data/pixelctl/receiver" in line:
                pid = line.split()[0]
                if pid.isdigit():
                    self.shell(f"kill -TERM {pid}", check=False)
        time.sleep(0.3)
        config = json.loads(self.shell("cat /res/etc/EasyUI.cfg"))
        config["startupLibPath"] = "/tmp/libpixelctl.so"
        trial_config = self.backup / "EasyUI-trial.cfg"
        trial_config.write_text(json.dumps(config, indent=2) + "\n")
        self.push(trial_config, "/tmp/pixelctl-EasyUI.cfg")
        self.shell("mount -o bind /tmp/pixelctl-EasyUI.cfg /res/etc/EasyUI.cfg; setprop ctl.restart zkswe")
        return self.wait_native()

    def build_image(self, current):
        def tool(name):
            return shutil.which(name) or str(Path("/opt/homebrew/bin") / name)
        with tempfile.TemporaryDirectory(dir=self.backup) as temporary:
            tree = Path(temporary) / "res"
            self.command([tool("unsquashfs"), "-d", str(tree), str(current)])
            before = {str(p.relative_to(tree)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777) for p in tree.rglob("*") if p.is_file()}
            config = json.loads((tree / "etc/EasyUI.cfg").read_text())
            config["startupLibPath"] = "/res/lib/libpixelctl.so"
            (tree / "etc/EasyUI.cfg").write_text(json.dumps(config, indent=2) + "\n")
            shutil.copy2(self.resources / "libpixelctl.so", tree / "lib/libpixelctl.so")
            image = self.backup / "res-pixelctl.squashfs"
            self.command([tool("mksquashfs"), str(tree), str(image), "-noappend", "-comp", "xz", "-b", "131072", "-all-root", "-no-xattrs", "-no-progress", "-processors", "2"])
            verify = Path(temporary) / "verified"
            self.command([tool("unsquashfs"), "-d", str(verify), str(image)])
            after = {str(p.relative_to(verify)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777) for p in verify.rglob("*") if p.is_file()}
            changed = {name for name in before if before[name] != after.get(name)}
            added = set(after) - set(before)
            if changed - {"etc/EasyUI.cfg", "lib/libpixelctl.so"} or added - {"lib/libpixelctl.so"}:
                raise RuntimeError("Refused: unexpected vendor file modifications")
        return image

    def prefixes(self, current, image):
        old, new = current.read_bytes(), image.read_bytes()
        used = struct.unpack_from("<Q", old, 40)[0]
        size = (max(used, len(new)) + 65535) // 65536 * 65536
        if size > len(old) or any(byte != 255 for byte in old[size:]):
            raise RuntimeError("Refused: res has an unexpected occupied tail")
        oldpath, newpath = self.backup / "res-old.prefix", self.backup / "res-new.prefix"
        oldpath.write_bytes(old[:size]); newpath.write_bytes(new + b"\xff" * (size - len(new)))
        self.push(oldpath, "/tmp/pixelctl-res-old.prefix")
        self.push(newpath, "/tmp/pixelctl-res-new.prefix")
        return oldpath, newpath

    def flash(self, current, image):
        self.prefixes(current, image)
        command = "/tmp/pixelctl-res-flash /tmp/pixelctl-res-old.prefix /tmp/pixelctl-res-new.prefix"
        self.shell(command)  # Complete dry run before stopping the GUI.
        self.shell("setprop ctl.stop zkswe")
        result = self.command(self.adb + ["shell", command + " --write"], check=False)
        if result.returncode or '"verified":true' not in result.stdout:
            if '"preflight":true' in result.stdout:
                recovery = command.replace("old.prefix /tmp/pixelctl-res-new.prefix", "new.prefix /tmp/pixelctl-res-old.prefix")
                self.shell(recovery + " --write --recover-partial")
                self.shell("setprop ctl.start zkswe")
            raise RuntimeError("Flash failed; original res restored when possible. Keep power on and inspect the installer output.")
        self.shell("reboot", check=False)
        return self.wait_native(timeout=90)

    def restore(self):
        original = self.backup / "res-original.bin"
        if not original.exists():
            raise RuntimeError("Original recovery backup not found")
        expected = (self.backup / "res-original.sha256").read_text().strip()
        if hashlib.sha256(original.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Original backup checksum mismatch")
        current = self.backup_res()
        # A complete original image is reduced to its used filesystem prefix.
        original_bytes = original.read_bytes()
        used = struct.unpack_from("<Q", original_bytes, 40)[0]
        prefix_size = (used + 65535) // 65536 * 65536
        if any(byte != 255 for byte in original_bytes[prefix_size:]):
            raise RuntimeError("Original recovery image has an unexpected occupied tail")
        image = self.backup / "res-restore.squashfs"
        image.write_bytes(original_bytes[:prefix_size])
        self.push(self.resources / "res-flash-armv7", "/tmp/pixelctl-res-flash")
        self.shell("chmod 700 /tmp/pixelctl-res-flash")
        self.prefixes(current, image)
        self.shell("setprop ctl.stop zkswe")
        self.shell("/tmp/pixelctl-res-flash /tmp/pixelctl-res-old.prefix /tmp/pixelctl-res-new.prefix --write")
        self.shell("reboot", check=False)
        return {"restored": True, "original_res_sha256": expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--host")
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--resources", default=str(Path(__file__).parent))
    parser.add_argument("--key-file")
    parser.add_argument("--pages-file")
    parser.add_argument("--args-file")
    parser.add_argument("--trial", action="store_true")
    parser.add_argument("--flash", action="store_true")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if not args.restore and not all([args.key_file, args.pages_file, args.args_file]):
        parser.error("--key-file, --pages-file and --args-file are required")
    installer = Installer(args)
    installer.check_device()
    if args.restore:
        print(json.dumps(installer.restore()))
        return
    current = installer.backup_res()
    installer.stage()
    status = installer.trial()
    if args.flash:
        status = installer.flash(current, installer.build_image(current))
    print(json.dumps({"installed": True, "persistent": args.flash, "backup_dir": str(installer.backup), "status": status}))


if __name__ == "__main__":
    main()
