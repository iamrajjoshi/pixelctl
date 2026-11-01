"""Export and deploy the compact on-device provider/renderer runtime."""
from __future__ import annotations

import importlib.resources
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import home, render_data, save_json
from .credentials import credential_path
from .device import prepare
from .rendering import PAGES, render


def _native_color(value):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in value):
        return "#" + "".join(f"{channel:02x}" for channel in value)
    raise ValueError("Native theme colors must be #RRGGBB strings or three RGB integers")


def prepare_bundle(config: dict, folder: Path):
    unsupported = set(config["pages"]) - set(PAGES)
    if unsupported:
        raise ValueError("Standalone mode supports native built-in pages; custom Python pages need host mode")
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    enabled = set(config["providers"])
    if enabled - {"github", "vercel", "http-health"}:
        raise ValueError("A configured provider has no native implementation")
    payload = {
        "pages_file": "/data/pixelctl/pages", "key_file": "/data/pixelctl.key",
        "https_helper": "/data/pixelctl/https-get", "ca_file": "/data/pixelctl/cacert.pem",
        "timezone": config["timezone_posix"], "refresh_seconds": config.get("refresh_seconds", 60),
        "brand_frame": "/data/pixelctl/brand.rgb", "identity_frame": "/data/pixelctl/identity.rgb",
        "identity_label": config["theme"].get("wordmark", "PIXELCTL"),
        "theme": {key: _native_color(config["theme"][key]) for key in ("foreground", "accent", "secondary") if key in config["theme"]},
        "settings": config.get("settings", {}).copy(),
    }
    target = payload["settings"].get("countdown_target")
    if isinstance(target, str):
        date = datetime.fromisoformat(target.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=ZoneInfo(config["timezone"]))
        payload["settings"]["countdown_target"] = int(date.timestamp())
    credentials = {}
    for name, fields in {
        "github": ("github_login", "github_org"),
        "vercel": ("vercel_scope", "vercel_project", "production_alias"),
    }.items():
        if name not in enabled:
            continue
        path = credential_path(name)
        if not path.exists():
            raise ValueError(f"Missing {name} token; run pixelctl credentials set {name}")
        if path.stat().st_mode & 0o077:
            raise ValueError(f"{name} token must have private file permissions")
        if name == "github" and not config.get("github_login"):
            raise ValueError("Configure github_login before enabling the native GitHub provider")
        if name == "vercel" and (not config.get("vercel_scope") or not (config.get("vercel_project_id") or config.get("vercel_project"))):
            raise ValueError("Configure vercel_scope and vercel_project before enabling the native Vercel provider")
        credentials[name] = path
        for field in fields:
            if field in config:
                payload[field] = config[field]
        if name == "vercel" and config.get("vercel_project_id"):
            payload["vercel_project"] = config["vercel_project_id"]
        payload[f"{name}_token_file"] = f"/data/pixelctl/credentials/{name}.token"
    if "http-health" in enabled:
        payload["health_url"] = config.get("health_url", "")
        if not payload["health_url"].startswith("https://"):
            raise ValueError("Configure an HTTPS health_url before enabling the native health provider")
        payload["health_expected"] = config.get("health_expected", {"status": "ok"})
    save_json(folder / "standalone.json", payload)
    now = datetime.now(timezone.utc)
    data = render_data(config, {})
    for name, page in (("brand.rgb", "clock"), ("identity.rgb", "identity")):
        (folder / name).write_bytes(render(page, data, now, 0).tobytes())
    key, pages, arguments = prepare(config, folder)
    return {"config": folder / "standalone.json", "brand": folder / "brand.rgb", "identity": folder / "identity.rgb", "key": key, "pages": pages, "arguments": arguments, "credentials": credentials}


def _adb(config, args):
    return [args.adb, "-s", args.serial or f"{config['device_ip']}:5555"]


def device_status(config, args):
    result = subprocess.run(_adb(config, args) + ["shell", "cat /tmp/pixelctl-standalone.status.json"], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError("Standalone status is unavailable; check the device connection and runtime")
    return json.loads(result.stdout)


def disable_host():
    if sys.platform != "darwin":
        return
    from .service import LABEL
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    if plist.exists():
        destination = home() / "host-collector.plist.disabled"
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(plist, destination)


def deploy(config, args, state_dir):
    bundle = prepare_bundle(config, state_dir / "standalone")
    resources = Path(str(importlib.resources.files("pixelctl") / "device_files"))
    if not (resources / "standalone-armv7").exists():
        raise ValueError("This CLI build does not contain the standalone runtime")
    serial = args.serial or f"{config['device_ip']}:5555"
    if ":" in serial:
        subprocess.run([args.adb, "connect", serial], check=True, capture_output=True)
    adb = _adb(config, args)
    subprocess.run(adb + ["shell", "mkdir -p /data/pixelctl/credentials; chmod 700 /data/pixelctl/credentials"], check=True, capture_output=True)
    for name, source in bundle["credentials"].items():
        target = f"/data/pixelctl/credentials/{name}.token"
        subprocess.run(adb + ["push", str(source), target + ".new"], check=True, capture_output=True)
        subprocess.run(adb + ["shell", f"chmod 600 {target}.new && mv {target}.new {target}"], check=True, capture_output=True)
    # Two renderers must not compete, including after the next Mac login.
    disable_host()
    command = [
        sys.executable, str(resources / "install.py"), "--resources", str(resources),
        "--serial", serial, "--adb", args.adb, "--host", config["device_ip"],
        "--backup-dir", str(args.backup_dir or state_dir / "recovery"),
        "--key-file", str(bundle["key"]), "--pages-file", str(bundle["pages"]),
        "--args-file", str(bundle["arguments"]), "--standalone-config", str(bundle["config"]),
        "--brand-frame", str(bundle["brand"]), "--identity-frame", str(bundle["identity"]), "--trial",
    ]
    # Compare device timestamps against its own clock, allowing ordinary NTP drift.
    clock = subprocess.run(adb + ["shell", "date +%s"], check=True, capture_output=True, text=True, timeout=10)
    started = int(clock.stdout.strip())
    subprocess.run(command, check=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            status = device_status(config, args)
        except (OSError, ValueError):
            time.sleep(1)
            continue
        errors = validation_errors(status, config, started)
        if errors:
            raise ValueError("Standalone runtime started, but provider validation failed: " + "; ".join(errors))
        if errors == []:
            print(json.dumps(status, indent=2))
            return status
        time.sleep(1)
    raise ValueError("The native receiver is running but standalone collection has not completed; inspect standalone status")


def validation_errors(status: dict, config: dict, started: int):
    """None means pending; a list is the completed provider validation result.

    A failed build and an observed HTTP 5xx are valid negative observations.
    Missing credentials, incomplete PR lists and unknown probes are not proof
    that the configured sources work.
    """
    if not isinstance(status, dict):
        return None
    if status.get("started_at", 0) < started or status.get("checked_at", 0) < started or not status.get("receiver_ok"):
        return None
    enabled = set(config["providers"])
    if enabled & {"github", "vercel"} and status.get("provider_cycles", 0) < 1:
        return None
    if "http-health" in enabled and status.get("health_cycles", 0) < 1:
        return None
    required = []
    if "github" in enabled:
        required.extend(("prs", "garden"))
    if "vercel" in enabled:
        required.append("deploy")
    if "http-health" in enabled:
        required.append("health")
    errors = []
    pending = False
    for name in required:
        section = status.get(name, {})
        observed_http_failure = name == "health" and section.get("state") == 3
        if section.get("error") and not observed_http_failure:
            errors.append(f"{name}: {section['error']}")
            continue
        if section.get("updated_at", 0) < started:
            pending = True
            continue
        if name == "prs" and not section.get("complete"):
            errors.append("prs: list is incomplete")
        elif name == "garden" and not section.get("days", 0):
            errors.append("garden: contribution calendar is unavailable")
        elif name == "deploy" and section.get("state") not in (1, 2, 3, 4):
            errors.append("deploy: production deployment state is unknown")
        elif name == "deploy" and config.get("production_alias") and section.get("serving_error"):
            errors.append(f"deploy alias: {section['serving_error']}")
        elif name == "deploy" and config.get("production_alias") and section.get("serving_verified_at", 0) < started:
            errors.append("deploy alias: serving deployment has not been verified")
        elif name == "health" and section.get("state") not in (1, 3):
            errors.append("health: endpoint state is unknown")
    return errors if errors else None if pending else []
