"""Generic 52 × 16 LED pages with optional plugin-provided theme assets.

``data`` is the collector snapshot; ``phase`` is elapsed seconds on this page.
The PR page walks every item for seven seconds before repeating. Runtime should
use :func:`page_duration` so even a large PR inbox is fully traversable.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

WIDTH, HEIGHT = 52, 16
PAGES = ["clock", "prs", "deploy", "garden", "health", "uptime", "identity"]
BACKGROUND = (0, 0, 0)
WHITE = (232, 231, 220)
ACCENT = (183, 189, 194)
SECONDARY = (137, 161, 162)
GREEN = (72, 211, 112)
AMBER = (235, 184, 93)
RED = (246, 105, 105)
DIM = (65, 70, 72)

# A three-column alphabet keeps labels readable without scrolling at 52 pixels.
FONT = {
    "A": ["010", "101", "111", "101", "101"], "B": ["110", "101", "110", "101", "110"],
    "C": ["011", "100", "100", "100", "011"], "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "110", "100", "111"], "F": ["111", "100", "110", "100", "100"],
    "G": ["011", "100", "101", "101", "011"], "H": ["101", "101", "111", "101", "101"],
    "I": ["111", "010", "010", "010", "111"], "J": ["001", "001", "001", "101", "010"],
    "K": ["101", "101", "110", "101", "101"], "L": ["100", "100", "100", "100", "111"],
    "M": ["101", "111", "111", "101", "101"], "N": ["101", "111", "111", "111", "101"],
    "O": ["010", "101", "101", "101", "010"], "P": ["110", "101", "110", "100", "100"],
    "Q": ["010", "101", "101", "111", "011"], "R": ["110", "101", "110", "101", "101"],
    "S": ["011", "100", "010", "001", "110"], "T": ["111", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "111"], "V": ["101", "101", "101", "101", "010"],
    "W": ["101", "101", "111", "111", "101"], "X": ["101", "101", "010", "101", "101"],
    "Y": ["101", "101", "010", "010", "010"], "Z": ["111", "001", "010", "100", "111"],
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["110", "001", "010", "100", "111"], "3": ["110", "001", "010", "001", "110"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "110", "001", "110"],
    "6": ["011", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "110"],
    "#": ["101", "111", "101", "111", "101"], ":": ["0", "1", "0", "1", "0"],
    ".": ["0", "0", "0", "0", "1"], "/": ["001", "001", "010", "100", "100"],
    "-": ["000", "000", "111", "000", "000"], "+": ["000", "010", "111", "010", "000"],
    "%": ["101", "001", "010", "100", "101"], "?": ["110", "001", "010", "000", "010"],
    "!": ["1", "1", "1", "0", "1"], " ": ["0", "0", "0", "0", "0"],
    "<": ["001", "010", "100", "010", "001"], ">": ["100", "010", "001", "010", "100"],
    "_": ["000", "000", "000", "000", "111"], "=": ["000", "111", "000", "111", "000"],
}
BIG = {
    "0": ["01110", "11011", "11011", "11011", "11011", "11011", "01110"],
    "1": ["00110", "01110", "00110", "00110", "00110", "00110", "01111"],
    "2": ["01110", "11011", "00011", "00110", "01100", "11000", "11111"],
    "3": ["11110", "00011", "00011", "01110", "00011", "00011", "11110"],
    "4": ["00011", "00111", "01011", "11011", "11111", "00011", "00011"],
    "5": ["11111", "11000", "11110", "00011", "00011", "11011", "01110"],
    "6": ["01110", "11000", "11110", "11011", "11011", "11011", "01110"],
    "7": ["11111", "00011", "00110", "00110", "01100", "01100", "01100"],
    "8": ["01110", "11011", "11011", "01110", "11011", "11011", "01110"],
    "9": ["01110", "11011", "11011", "01111", "00011", "00011", "01110"],
    ":": ["00", "11", "11", "00", "11", "11", "00"],
}


def text_width(text: str, big: bool = False) -> int:
    font = BIG if big else FONT
    return max(0, sum(len(font.get(c, FONT["?"])[0]) + 1 for c in str(text).upper()) - 1)


def draw_text(image: Image.Image, text: str, x: int, y: int, color=WHITE, *, big=False) -> None:
    draw = ImageDraw.Draw(image)
    font = BIG if big else FONT
    for c in str(text).upper():
        glyph = font.get(c, FONT["?"])
        for row, bits in enumerate(glyph):
            for col, value in enumerate(bits):
                if value == "1":
                    draw.point((int(x + col), y + row), color)
        x += len(glyph[0]) + 1


def label(image: Image.Image, text: str, y: int = 0, color=ACCENT, center=False) -> None:
    draw_text(image, text, max(0, (WIDTH - text_width(text)) // 2) if center else 1, y, color)


def ticker(image: Image.Image, text: str, y: int, phase: float, color=WHITE) -> None:
    width = text_width(text)
    if width <= 50:
        draw_text(image, text, 1, y, color)
        return
    # Pause at both ends, then return to the beginning on the next item.
    distance = width - 50
    offset = min(distance, max(0, int((phase - 1.0) * 12)))
    draw_text(image, text, 1 - offset, y, color)


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _age(value, now: datetime) -> str:
    dt = _parse_time(value)
    if dt is None:
        return "--"
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}S"
    if seconds < 3600:
        return f"{seconds // 60}M"
    if seconds < 86400:
        return f"{seconds // 3600}H"
    return f"{seconds // 86400}D"


def _section(data, key, now):
    section = data.get(key) or {}
    updated = _parse_time(section.get("updated_at") or section.get("checked_at"))
    # Preserve readable cached values, but never present them as current.
    ttl = 180 if key in ("health", "uptime") else 900
    # An observed HTTP 5xx is a current failure, rather than a stale refresh.
    refresh_error = section.get("error") and not (key == "health" and section.get("state") == "down")
    stale = bool(section.get("stale") or refresh_error or (updated and (now - updated).total_seconds() > ttl))
    return section, stale


def _unknown(image, title, reason="NO DATA"):
    label(image, title)
    label(image, reason, 9, AMBER)


def _mark(image, data, x, y, size=13, color=ACCENT):
    path = (data.get("_theme") or {}).get("mark_path")
    if path:
        with Image.open(path) as source:
            rgba = source.convert("RGBA")
            mask = rgba.getchannel("A").resize((size, size), Image.Resampling.LANCZOS)
        mask = mask.point(lambda value: 255 if value >= 105 else 0)
        image.paste(Image.new("RGB", (size, size), color), (x, y), mask)
    else:
        draw = ImageDraw.Draw(image)
        center = (size - 1) // 2
        draw.polygon([(x + center, y), (x + size - 1, y + center), (x + center, y + size - 1), (x, y + center)], outline=color)
        draw.point((x + center, y + center), color)


def _clock(image, data, now, phase):
    local = now.astimezone(ZoneInfo(data.get("_timezone", "UTC")))
    _mark(image, data, 0, 1, 13)
    digits = local.strftime("%H:%M")
    draw_text(image, digits, 17, 1, WHITE, big=True)
    draw_text(image, local.strftime("%a %m/%d"), 17, 10, SECONDARY)
    draw = ImageDraw.Draw(image)
    # The second hand is a quiet single-pixel sweep under the brand mark.
    draw.point((local.second % 13, 15), ACCENT)


def _pr_status(item):
    if item.get("draft"):
        return "DRAFT", DIM
    status = str(item.get("status", "")).lower()
    review = str(item.get("review", "")).lower()
    if any(s in status for s in ("fail", "error", "cancel", "timed_out")):
        return "CI FAIL", RED
    if "changes" in review:
        return "CHANGES", AMBER
    if any(s in status for s in ("pending", "progress", "queue", "expect")):
        return "CI RUN", AMBER
    if "approved" in review:
        return "APPROVED", GREEN
    return "REVIEW", ACCENT


def _prs(image, data, now, phase):
    section, stale = _section(data, "prs", now)
    if section.get("error") and not section.get("items"):
        return _unknown(image, "MY PRS", "UNAVAILABLE")
    items = section.get("items", [])
    if not items:
        if stale:
            _unknown(image, "MY PRS", "STALE")
        elif section.get("updated_at"):
            label(image, "MY PRS", color=ACCENT)
            label(image, "ALL CLEAR", 9, GREEN)
        else:
            _unknown(image, "MY PRS")
        return
    idx = int(max(0, phase) // 7) % len(items)
    item = items[idx]
    status, color = _pr_status(item)
    if phase % 7 < 3:
        label(image, f"PR {idx+1}/{len(items)}", color=ACCENT)
        draw_text(image, f"#{item['number']}", 1, 9, WHITE)
        # Status dot stays visible even for large PR numbers.
        ImageDraw.Draw(image).rectangle((48, 10, 50, 12), color)
    else:
        label(image, "STALE" if stale else status, color=AMBER if stale else color)
        title = str(item.get("title", "UNTITLED"))
        title = re.sub(r"^\s*(?::[a-z_]+:|[^\x00-\x7f])+\s*", "", title)
        title = re.sub(r"^(?:feat|fix|ref|refactor|docs|chore|nit|test|perf|style)(?:\[[^\]]+\]|\([^)]+\))?:\s*", "", title, flags=re.IGNORECASE)
        ticker(image, title, 9, phase % 7 - 3, WHITE)


def _deploy(image, data, now, phase):
    section, stale = _section(data, "deploy", now)
    state = str(section.get("state", "unavailable")).lower()
    if state == "unavailable":
        return _unknown(image, "DEPLOY", "UNAVAILABLE")
    colors = {"successful": GREEN, "success": GREEN, "ready": GREEN, "building": AMBER, "pending": AMBER, "failed": RED, "error": RED, "canceled": AMBER, "cancelled": AMBER}
    color = colors.get(state, AMBER)
    name = {"successful": "LIVE", "success": "LIVE", "ready": "LIVE", "building": "BUILD", "pending": "BUILD", "failed": "FAILED", "error": "FAILED", "canceled": "CANCEL", "cancelled": "CANCEL"}.get(state, state[:8].upper())
    if state in ("successful", "success", "ready"):
        serving = section.get("serving") or {}
        serving_url = serving.get("deployment_url")
        serving_success = str(serving.get("state", "")).lower() in ("success", "successful", "ready")
        verified_live = bool(serving_url) and not serving.get("error") and serving_success and serving_url == section.get("url")
        name = "LIVE" if verified_live else "READY"
    label(image, "STALE" if stale else "WEB DEPLOY", color=AMBER if stale else ACCENT)
    bottom = f"{name} {_age(section.get('deployed_at') or section.get('updated_at'), now)}"
    draw_text(image, bottom, 1, 9, color)
    if state in ("building", "pending"):
        x = int(phase * 7) % WIDTH
        ImageDraw.Draw(image).line((x, 15, min(51, x + 6), 15), AMBER)


def _garden(image, data, now, phase):
    section, stale = _section(data, "garden", now)
    days = section.get("days", [])
    if not days:
        return _unknown(image, "GARDEN")
    today = now.astimezone(ZoneInfo(data.get("_timezone", "UTC"))).date()
    # Nineteen weeks × seven days, Sunday first, like GitHub's contribution map.
    # Missing dates are gray; zero activity is the darkest green, not missing.
    values = {str(d.get("date")): d.get("count") for d in days}
    first = today - timedelta(days=(today.weekday() + 1) % 7 + 18 * 7)
    palette = [(8, 24, 13), (17, 80, 40), (28, 133, 57), (49, 181, 79), (93, 231, 124)]
    draw = ImageDraw.Draw(image)
    draw_text(image, "GH", 1, 1, ACCENT)
    draw_text(image, "!" if stale else "+", 3, 9, AMBER if stale else GREEN)
    for i in range(19 * 7):
        day = first + timedelta(days=i)
        if day > today:
            continue
        value = values.get(str(day))
        level = 0 if value == 0 else 1 if value is not None and value < 3 else 2 if value is not None and value < 7 else 3 if value is not None and value < 15 else 4
        fill = DIM if value is None else palette[level]
        col, row = divmod(i, 7)
        x, y = 13 + col * 2, 1 + row * 2
        draw.point((x, y), fill)


def _health(image, data, now, phase):
    section, stale = _section(data, "health", now)
    state = section.get("state", "unknown")
    if stale:
        return _unknown(image, "API HEALTH", "STALE")
    if state == "unknown":
        return _unknown(image, "API HEALTH", "UNAVAILABLE")
    color = GREEN if state == "ok" else RED
    label(image, "API OK" if state == "ok" else "API DOWN", color=color)
    latency = section.get("latency_ms")
    if latency is not None:
        label(image, f"{round(latency)}MS", 9, WHITE)
    else:
        label(image, "NO RESPONSE", 9, RED)
    history = section.get("history", [])[-20:]
    if len(history) > 1:
        valid = [v for v in history if isinstance(v, (int, float))]
        ceiling = max(100, max(valid, default=100))
        draw = ImageDraw.Draw(image)
        previous = None
        for i, value in enumerate(history):
            x = 31 + i
            if value is None:
                draw.point((x, 14), AMBER)
                previous = None
                continue
            y = 14 - min(5, round(float(value) / ceiling * 5))
            if previous:
                draw.line((*previous, x, y), SECONDARY)
            else:
                draw.point((x, y), SECONDARY)
            previous = (x, y)


def _uptime(image, data, now, phase):
    section, stale = _section(data, "uptime", now)
    value = section.get("availability_pct")
    checks = section.get("checks", section.get("samples", 0))
    health, health_stale = _section(data, "health", now)
    if health.get("state", "unknown") == "unknown" or health_stale:
        return _unknown(image, "UPTIME", "NO SIGNAL")
    if value is None or not checks:
        return _unknown(image, "API UPTIME", "COLLECTING")
    if stale:
        return _unknown(image, "API UPTIME", "STALE")
    if phase % 8 < 5:
        label(image, "API UPTIME")
        text = f"{value:.2f}%"
        label(image, text, 9, GREEN if value >= 99 else AMBER if value >= 95 else RED)
    else:
        label(image, "OBSERVED API")
        label(image, f"SINCE {_age(section.get('window_start'), now)}", 9, SECONDARY)


def _identity(image, data, now, phase):
    t = phase % 10
    if t < 3:
        _mark(image, data, 18, 0, 16, WHITE)
        # A quiet particle follows a rising path beside the configured mark.
        draw = ImageDraw.Draw(image)
        p = int(t * 10) % 14
        draw.point((2 + p, 14 - p), ACCENT)
        if p > 0:
            draw.point((1 + p, 15 - p), DIM)
    else:
        # Keep the configured identity readable using the shared pixel alphabet.
        # The preceding phase displays its mark at full panel height.
        theme = data.get("_theme") or {}
        wordmark_path = theme.get("wordmark_path")
        if wordmark_path:
            with Image.open(wordmark_path) as source:
                source = source.convert("RGBA")
                source.thumbnail((52, 11), Image.Resampling.LANCZOS)
                image.paste(source, ((52-source.width)//2, (14-source.height)//2), source)
        else:
            name = str(theme.get("wordmark") or theme.get("name") or "PIXELCTL")
            if text_width(name) > 50:
                ticker(image, name, 5, t - 3, WHITE)
            else:
                label(image, name, 5, WHITE, center=True)
        draw = ImageDraw.Draw(image)
        progress = min(51, int((t - 3) / 7 * 52))
        draw.line((0, 15, progress, 15), ACCENT)


_RENDERERS = {"clock": _clock, "prs": _prs, "deploy": _deploy, "garden": _garden, "health": _health, "uptime": _uptime, "identity": _identity}


def page_duration(page: str, data: dict) -> float:
    """Seconds to traverse a page without skipping PRs."""
    if page == "prs":
        return max(10, len((data.get("prs") or {}).get("items", [])) * 7)
    return 10


def render(page: str, data: dict, now: datetime, phase: float = 0) -> Image.Image:
    if page not in _RENDERERS:
        raise ValueError(f"Unknown page: {page}")
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    _RENDERERS[page](image, data, now, max(0, phase))
    # Cached collector errors remain visible even when old values are retained.
    section = data.get(page) or {}
    if page not in ("clock", "identity") and section.get("error"):
        ImageDraw.Draw(image).point((51, 0), AMBER)
    return _apply_theme(image, data)


def _rgb(value, fallback):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))
    if isinstance(value, (tuple, list)) and len(value) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in value):
        return tuple(value)
    return fallback


def _apply_theme(image, data):
    theme = data.get("_theme") or {}
    palette = {
        WHITE: _rgb(theme.get("foreground"), WHITE),
        ACCENT: _rgb(theme.get("accent"), ACCENT),
        SECONDARY: _rgb(theme.get("secondary"), SECONDARY),
    }
    if all(source == target for source, target in palette.items()):
        return image
    pixels = image.load()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            pixels[x, y] = palette.get(pixels[x, y], pixels[x, y])
    return image
