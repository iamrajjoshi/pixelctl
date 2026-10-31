"""Explicitly loaded plugins extend pages, providers, themes, and presets."""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .providers import provider_functions
from .rendering import PAGES, render


@dataclass
class Provider:
    collect: Callable
    interval: float
    sections: tuple[str, ...]


class Registry:
    def __init__(self):
        self.pages = {}
        self.providers = {}
        self.themes = {"default": {"name": "Pixelctl", "wordmark": "PIXELCTL"}}
        self.presets = {}

    def register_page(self, name: str, renderer: Callable):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", name):
            raise ValueError("Page IDs must contain 1-32 letters, digits, hyphens, or underscores")
        if name in self.pages:
            raise ValueError(f"Page already registered: {name}")
        self.pages[name] = renderer

    def register_provider(self, name: str, collector: Callable, *, interval=120, sections=()):
        if name in self.providers:
            raise ValueError(f"Provider already registered: {name}")
        if interval < 10:
            raise ValueError("Provider intervals must be at least ten seconds")
        self.providers[name] = Provider(collector, float(interval), tuple(sections))

    def register_theme(self, name: str, theme: dict):
        if name in self.themes:
            raise ValueError(f"Theme already registered: {name}")
        self.themes[name] = theme.copy()

    def register_preset(self, name: str, config: dict):
        if name in self.presets:
            raise ValueError(f"Preset already registered: {name}")
        self.presets[name] = config.copy()


def builtin_registry() -> Registry:
    registry = Registry()
    for page in PAGES:
        registry.register_page(page, lambda data, now, phase=0, page=page: render(page, data, now, phase))
    sections = {"github": ("prs", "garden"), "vercel": ("deploy",), "http-health": ("health", "uptime")}
    for name, function in provider_functions.items():
        registry.register_provider(name, function, interval=60 if name == "http-health" else 120, sections=sections[name])
    return registry


def load_plugin(registry: Registry, reference: str, base: Path):
    candidate = Path(reference).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    if candidate.is_file():
        name = "pixelctl_plugin_" + hashlib.sha256(str(candidate.resolve()).encode()).hexdigest()[:16]
        spec = importlib.util.spec_from_file_location(name, candidate)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load plugin: {candidate}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            module.register(registry)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return
    matches = list(importlib.metadata.entry_points(group="pixelctl.plugins", name=reference))
    if len(matches) != 1:
        raise ValueError(f"Plugin not found or ambiguous: {reference}")
    matches[0].load()(registry)
