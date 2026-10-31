"""Install a per-user macOS collector using the current Python environment."""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import home, save_json

LABEL = "io.pixelctl.collector"


def control(action):
    if sys.platform != "darwin":
        raise ValueError("Automatic service management currently supports macOS; run pixelctl run elsewhere")
    domain = f"gui/{os.getuid()}"
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    if action == "stop":
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=True)
    elif action == "start":
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    elif action == "restart":
        subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=True)
    else:
        subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], check=True)


def install(config_path: Path, state_dir: Path):
    if sys.platform != "darwin":
        raise ValueError("Automatic service management currently supports macOS; run pixelctl run elsewhere")
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    raw = json.loads(config_path.read_text())
    references = []
    for index, reference in enumerate(raw.get("plugins", [])):
        source = Path(reference).expanduser()
        if not source.is_absolute():
            source = config_path.parent / source
        if source.is_file():
            # A plugin's sibling assets remain together, outside the public core.
            target = root / "plugins" / f"plugin-{index}"
            if target.resolve().is_relative_to(source.parent.resolve()):
                # Already in the durable support tree (or an ancestor). Copying
                # that tree into itself would recursively include the target.
                references.append(str(source.resolve()))
                continue
            if source.parent.resolve() != target.resolve():
                shutil.copytree(source.parent, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".git", ".venv"))
            references.append(str(target / source.name))
        else:
            references.append(reference)
    raw["plugins"] = references
    key = Path(raw.get("token_file", root / "device.key")).expanduser()
    if not key.is_absolute():
        key = config_path.parent / key
    raw["token_file"] = str(key.resolve())
    installed_config = root / "config.json"
    save_json(installed_config, raw)
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    agent_dir = Path.home() / "Library/LaunchAgents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    plist = agent_dir / f"{LABEL}.plist"
    data = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "pixelctl", "--config", str(installed_config), "--state-dir", str(state_dir), "run"],
        "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 30, "ExitTimeOut": 10,
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PATH": ":".join([str(Path.home() / "Library/pnpm"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]),
        },
        "StandardOutPath": str(root / "service.log"),
        "StandardErrorPath": str(root / "service.log"),
    }
    plist.write_bytes(plistlib.dumps(data))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    deadline = time.monotonic() + 15
    while subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True).returncode == 0:
        if time.monotonic() >= deadline:
            raise ValueError("Previous collector is still stopping; retry service install shortly")
        time.sleep(0.2)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    return installed_config
