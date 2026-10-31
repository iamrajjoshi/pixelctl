"""Authenticated RGB transport for the native TC002 receiver."""
import json
import urllib.request
from pathlib import Path


class Display:
    def __init__(self, address: str, token_path: str | Path, port=9000):
        self.base = f"http://{address}:{port}"
        self.token = Path(token_path).read_text().strip()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body=None, *, raw=False):
        request = urllib.request.Request(
            self.base + path, data=body, method="GET" if body is None else "POST",
            headers={"Content-Type": "application/octet-stream", "X-Pixelctl-Key": self.token},
        )
        with self.opener.open(request, timeout=2) as response:
            payload = response.read(65536)
        return payload if raw else json.loads(payload) if payload else {}

    def frame(self, page, image):
        if image.size != (52, 16):
            raise ValueError("TC002 frames must be exactly 52 by 16 pixels")
        return self.request(f"/frame/{page}", image.convert("RGB").tobytes())
