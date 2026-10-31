"""Run configured page and provider plugins without a companion UI."""
from __future__ import annotations

import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from .config import read_json, render_data, save_json
from .transport import Display

LOG = logging.getLogger("pixelctl")


def collect(config, registry, state):
    result = state.copy()
    with ThreadPoolExecutor(max_workers=max(1, len(config["providers"]))) as pool:
        jobs = [pool.submit(registry.providers[name].collect, config, state.copy()) for name in config["providers"]]
        for job in jobs:
            result.update(job.result())
    return result


def preview(config, registry, state, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    pages = config["pages"]
    sheet = Image.new("RGB", (580, len(pages) * 200 + 20), (19, 20, 24))
    draw = ImageDraw.Draw(sheet)
    gif = []
    data = render_data(config, state)
    for index, page in enumerate(pages):
        y = index * 200 + 12
        draw.text((30, y), page.upper(), fill=(200, 198, 211))
        frame = registry.pages[page](data, now, 0)
        frame.save(destination / f"{page}.png")
        sheet.paste(frame.resize((520, 160), Image.Resampling.NEAREST), (30, y + 23))
        for phase in range(7):
            gif.append(registry.pages[page](data, now, phase).resize((624, 192), Image.Resampling.NEAREST))
    sheet.save(destination / "preview.png")
    gif[0].save(destination / "rotation.gif", save_all=True, append_images=gif[1:], duration=1000, loop=0)


def run(config, registry, state_dir: Path):
    display = Display(config["device_ip"], config["token_file"])
    state_path = state_dir / "state.json"
    state = read_json(state_path, {})
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    pages = config["pages"]
    due = {name: 0 for name in config["providers"]}
    phases = {name: 0.0 for name in pages}
    started = previous_tick = time.monotonic()
    next_persist, next_status = started + 30, 0
    last_error = -60
    jobs = {}
    LOG.info("Starting %d pages and %d providers", len(pages), len(due))
    with ThreadPoolExecutor(max_workers=max(1, len(due))) as pool:
        while not stopped:
            tick = time.monotonic()
            for name, job in list(jobs.items()):
                if not job.done():
                    continue
                try:
                    state.update(job.result())
                    save_json(state_path, state)
                    LOG.info("Updated provider %s", name)
                except Exception:
                    # Plugin exceptions must not terminate the local clock.
                    # Omit exception text: third-party plugins may include secrets.
                    LOG.error("Provider %s failed; retained data will expire", name)
                del jobs[name]
            for name in due:
                provider = registry.providers[name]
                if tick >= due[name] and name not in jobs:
                    jobs[name] = pool.submit(provider.collect, config, state.copy())
                    due[name] = tick + provider.interval
            try:
                status = display.request("/status")
                active = status.get("page")
                if active in phases:
                    phases[active] += min(2, tick - previous_tick)
                data = render_data(config, state)
                now = datetime.now(timezone.utc)
                for page in pages:
                    phase = phases[page] if page == "prs" else tick - started
                    display.frame(page, registry.pages[page](data, now, phase))
                if tick >= next_persist:
                    display.request("/persist", b"")
                    next_persist = tick + 3600
                if tick >= next_status:
                    save_json(state_dir / "display-status.json", status)
                    next_status = tick + 15
            except (OSError, ValueError) as exc:
                if tick - last_error >= 60:
                    LOG.warning("Clock unavailable (%s); local cache remains active", type(exc).__name__)
                    last_error = tick
            previous_tick = tick
            time.sleep(max(0.02, 1 - (time.monotonic() - tick)))
    LOG.info("Stopped")
