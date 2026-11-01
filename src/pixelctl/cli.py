"""A CLI for pixel pages, plugins, and the TC002 native display."""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import home, load, read_json, save_json
from .runtime import collect, preview, run
from .transport import Display


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--config", type=Path, default=home() / "config.json")
    root.add_argument("--state-dir", type=Path, default=home() / "state")
    commands = root.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="Create a neutral clock configuration")
    initialize.add_argument("--device", required=True)
    for name in ("run", "collect", "status", "pause"):
        commands.add_parser(name)
    page = commands.add_parser("page")
    page.add_argument("name")
    render = commands.add_parser("render", help="Render configured pages from cached data")
    render.add_argument("--output", type=Path, default=Path("preview"))
    plugins = commands.add_parser("plugins")
    plugins.add_argument("action", choices=["list"])
    service = commands.add_parser("service")
    service.add_argument("action", choices=["install", "start", "stop", "restart", "status"])
    device = commands.add_parser("device", help="Guarded TC002 trial, installation, or recovery")
    device.add_argument("action", choices=["install", "restore"])
    device.add_argument("--serial")
    device.add_argument("--adb", default="adb")
    device.add_argument("--backup-dir", type=Path)
    device.add_argument("--flash", action="store_true", help="Persist only after a verified reversible trial")
    credentials = commands.add_parser("credentials", help="Set provider tokens through hidden input")
    credentials.add_argument("action", choices=["set", "status"])
    credentials.add_argument("name", nargs="?", choices=["github", "vercel"])
    standalone = commands.add_parser("standalone", help="Run built-in providers and pages on the clock")
    standalone.add_argument("action", choices=["deploy", "status"])
    standalone.add_argument("--serial")
    standalone.add_argument("--adb", default="adb")
    standalone.add_argument("--backup-dir", type=Path)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "init":
            if args.config.exists():
                raise ValueError("Configuration already exists; edit it directly")
            save_json(args.config, {"device_ip": args.device, "pages": ["clock", "identity"], "providers": [], "timezone": "UTC", "timezone_posix": "UTC0", "theme": "default"})
            print(f"Created {args.config}")
            return
        config, registry = load(args.config)
        if args.command == "plugins":
            print(json.dumps({"pages": list(registry.pages), "providers": list(registry.providers), "themes": list(registry.themes), "presets": list(registry.presets)}, indent=2))
        elif args.command == "credentials":
            from .credentials import set_interactive, status
            if args.action == "set":
                if not args.name:
                    raise ValueError("Choose a service: credentials set github or credentials set vercel")
                set_interactive(args.name, config)
            else:
                print(json.dumps(status(), indent=2))
        elif args.command == "standalone":
            from .standalone import deploy, device_status
            if args.action == "deploy":
                deploy(config, args, args.state_dir)
            else:
                print(json.dumps(device_status(config, args), indent=2))
        elif args.command == "run":
            run(config, registry, args.state_dir)
        elif args.command == "collect":
            state = collect(config, registry, read_json(args.state_dir / "state.json", {}))
            save_json(args.state_dir / "state.json", state)
            print(json.dumps({key: {"error": value.get("error"), "updated_at": value.get("updated_at")} for key, value in state.items()}, indent=2))
        elif args.command == "render":
            preview(config, registry, read_json(args.state_dir / "state.json", {}), args.output)
            print(f"Rendered {len(config['pages'])} pages in {args.output}")
        elif args.command == "service":
            from .service import control, install
            if args.action == "install":
                print(f"Installed collector: {install(args.config, args.state_dir)}")
            else:
                control(args.action)
        elif args.command == "device":
            from .device import install
            install(config, args, args.state_dir)
        else:
            display = Display(config["device_ip"], config["token_file"])
            if args.command == "page":
                if args.name not in config["pages"]:
                    raise ValueError(f"Page is not configured: {args.name}")
                result = display.request(f"/page/{args.name}", b"")
            else:
                result = display.request(f"/{args.command}", None if args.command == "status" else b"")
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"pixelctl: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
