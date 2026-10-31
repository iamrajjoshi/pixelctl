"""User configuration stays outside the source checkout by default."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from .plugins import builtin_registry, load_plugin


def home() -> Path:
    if os.environ.get("PIXELCTL_HOME"):
        return Path(os.environ["PIXELCTL_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Pixelctl"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "pixelctl"


def read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default.copy()


def save_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(value, file, indent=2)
        file.write("\n")
    os.replace(temporary, path)


def load(path: Path):
    raw = read_json(path, {})
    registry = builtin_registry()
    for reference in raw.get("plugins", []):
        load_plugin(registry, reference, path.parent)
    preset_name = raw.get("extends")
    if preset_name and preset_name not in registry.presets:
        raise ValueError(f"Unknown preset: {preset_name}")
    defaults = {
        "pages": ["clock", "identity"], "providers": [], "theme": "default",
        "timezone": "UTC", "timezone_posix": "UTC0", "rotate_seconds": 8,
        "brightness": 40, "token_file": str(home() / "device.key"),
    }
    config = defaults | registry.presets.get(preset_name, {}) | raw
    ZoneInfo(config["timezone"])
    if not config["pages"] or len(config["pages"]) > 32 or len(set(config["pages"])) != len(config["pages"]):
        raise ValueError("Choose 1-32 distinct pages")
    for page in config["pages"]:
        if page not in registry.pages:
            raise ValueError(f"Unknown page: {page}")
    for provider in config["providers"]:
        if provider not in registry.providers:
            raise ValueError(f"Unknown provider: {provider}")
    if isinstance(config["theme"], str):
        if config["theme"] not in registry.themes:
            raise ValueError(f"Unknown theme: {config['theme']}")
        config["theme"] = registry.themes[config["theme"]].copy()
    key = Path(config["token_file"]).expanduser()
    config["token_file"] = str(key if key.is_absolute() else path.parent / key)
    return config, registry


def render_data(config: dict, state: dict) -> dict:
    return state | {"_theme": config["theme"], "_timezone": config["timezone"]}
