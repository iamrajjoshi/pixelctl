"""A small independently reusable theme and page plugin."""
from PIL import Image


def heartbeat(data, now, phase):
    image = Image.new("RGB", (52, 16))
    x = int(phase * 4) % 52
    image.putpixel((x, 8), (120, 220, 180))
    return image


def register(registry):
    registry.register_theme("mint", {"name": "Mint", "wordmark": "HELLO", "accent": "#78DCB4"})
    registry.register_page("heartbeat", heartbeat)
