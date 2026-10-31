"""Prepare generic receiver settings and invoke the guarded TC002 installer."""
from __future__ import annotations

import importlib.resources
import os
import secrets
import subprocess
import sys
from pathlib import Path


def _color(theme, key, fallback):
    value = theme.get(key, fallback)
    return value.lstrip("#") if isinstance(value, str) else "".join(f"{int(component):02X}" for component in value)


def prepare(config, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    key = Path(config["token_file"])
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_hex(32))
    pages = folder / "pages"
    pages.write_text("\n".join(config["pages"]) + "\n")
    theme = config["theme"]
    arguments = [
        "--pages-file", "/data/pixelctl/pages",
        "--timezone", config["timezone_posix"],
        "--rotate", str(config["rotate_seconds"]),
        "--brightness", str(config["brightness"]),
        "--clock-mark-width", "14",
        "--clock-color", _color(theme, "foreground", "E8E7DC"),
        "--date-color", _color(theme, "secondary", "E8E7DC"),
        "--accent-color", _color(theme, "accent", "AACCEE"),
    ]
    if "identity" in config["pages"]:
        arguments += ["--static-page", "identity"]
    path = folder / "receiver.args"
    path.write_text("\n".join(arguments) + "\n")
    return key, pages, path


def install(config, args, state_dir: Path):
    resources = Path(str(importlib.resources.files("pixelctl") / "device_files"))
    installer = resources / "install.py"
    if not installer.exists():
        raise ValueError("This CLI build has no TC002 receiver resources; build/package native/ first")
    key, pages, arguments = prepare(config, state_dir / "device")
    serial = args.serial or f"{config['device_ip']}:5555"
    command = [
        sys.executable, str(installer), "--serial", serial, "--adb", args.adb,
        "--backup-dir", str(args.backup_dir or state_dir / "recovery"),
        "--resources", str(resources), "--host", config["device_ip"],
        "--key-file", str(key), "--pages-file", str(pages), "--args-file", str(arguments),
    ]
    if args.action == "restore":
        command += ["--restore"]
    elif args.flash:
        command += ["--flash"]
    else:
        command += ["--trial"]
    subprocess.run(command, check=True)
