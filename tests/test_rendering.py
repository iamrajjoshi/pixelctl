import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from pixelctl.rendering import AMBER, GREEN, PAGES, RED, render


NOW = datetime(2026, 10, 31, 22, 0, 30, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
SNAPSHOT = {
    "prs": {"updated_at": STAMP, "items": [{"number": 123, "title": "Improve widget alignment", "status": "success", "review": "approved", "draft": False}]},
    "deploy": {"updated_at": STAMP, "deployed_at": STAMP, "state": "successful", "url": "https://deploy.example.invalid", "serving": {"state": "successful", "deployment_url": "https://deploy.example.invalid"}},
    "garden": {"updated_at": STAMP, "days": [{"date": "2026-10-31", "count": 8}]},
    "health": {"updated_at": STAMP, "state": "ok", "latency_ms": 174, "history": [12, 45, None, 76, 174]},
    "uptime": {"updated_at": STAMP, "availability_pct": 99.98, "window_start": STAMP, "checks": 5000},
}


def colors(image):
    return {rgb for count, rgb in image.getcolors(10000)}


class RenderingTests(unittest.TestCase):
    def test_every_builtin_renders_exact_panel_size(self):
        for page in PAGES:
            for phase in (0, 4, 9):
                image = render(page, SNAPSHOT, NOW, phase)
                self.assertEqual(image.size, (52, 16), (page, phase))
                self.assertEqual(image.mode, "RGB")
                self.assertIsNotNone(image.getbbox(), (page, phase))

    def test_unknown_data_does_not_imply_success(self):
        for page in ("prs", "deploy", "garden", "health", "uptime"):
            image = render(page, {}, NOW)
            self.assertIn(AMBER, colors(image))
            self.assertNotIn(GREEN, colors(image))

    def test_unknown_page_and_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            render("bogus", {}, NOW)
        with self.assertRaises(ValueError):
            render("clock", {}, NOW.replace(tzinfo=None))

    def test_default_clock_is_utc_and_timezone_is_configurable(self):
        utc = render("clock", {}, NOW)
        self.assertEqual(utc.tobytes(), render("clock", {"_timezone": "UTC"}, NOW).tobytes())
        self.assertNotEqual(utc.tobytes(), render("clock", {"_timezone": "America/Los_Angeles"}, NOW).tobytes())

    def test_pacific_fall_back_repeats_the_local_hour(self):
        data = {"_timezone": "America/Los_Angeles"}
        before = datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
        after = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)
        self.assertEqual(render("clock", data, before).tobytes(), render("clock", data, after).tobytes())

    def test_pr_cycle_reaches_every_item(self):
        data = {"prs": {"updated_at": STAMP, "items": [{"number": n} for n in range(17)]}}
        self.assertEqual(len({render("prs", data, NOW, n*7).tobytes() for n in range(17)}), 17)

    def test_stale_empty_prs_do_not_show_all_clear(self):
        stale = {"prs": {"updated_at": "2020-01-01T00:00:00Z", "items": []}}
        self.assertIn(AMBER, colors(render("prs", stale, NOW)))
        self.assertNotIn(GREEN, colors(render("prs", stale, NOW)))

    def test_current_http_failure_is_red_down_not_stale(self):
        data = {"health": {"state": "down", "updated_at": STAMP, "error": "HTTP 503", "latency_ms": 31}}
        self.assertIn(RED, colors(render("health", data, NOW)))

    def test_missing_probe_history_is_unknown_not_failure(self):
        data = {"health": {"state": "ok", "updated_at": STAMP, "history": [10, None, 30], "latency_ms": 30}}
        image = render("health", data, NOW)
        self.assertEqual(image.getpixel((32, 14)), AMBER)
        self.assertNotIn(RED, colors(image))

    def test_uptime_current_unknown_does_not_display_cached_percentage(self):
        data = {**SNAPSHOT, "health": {"state": "unknown", "updated_at": STAMP}}
        image = render("uptime", data, NOW)
        self.assertIn(AMBER, colors(image))
        self.assertNotIn(GREEN, colors(image))

    def test_uptime_stale_current_health_hides_cached_percentage(self):
        data = {**SNAPSHOT, "health": {"state": "ok", "updated_at": "2020-01-01T00:00:00Z"}}
        self.assertNotIn(GREEN, colors(render("uptime", data, NOW)))

    def test_live_deploy_requires_matching_successful_serving_alias(self):
        live = render("deploy", SNAPSHOT, NOW)
        for serving in ({}, {"state": "failed", "deployment_url": "https://deploy.example.invalid"}, {"state": "successful", "deployment_url": "https://other.example.invalid"}):
            data = {"deploy": {**SNAPSHOT["deploy"], "serving": serving}}
            self.assertNotEqual(live.tobytes(), render("deploy", data, NOW).tobytes())
        absent = {"deploy": {"state": "successful", "updated_at": STAMP}}
        self.assertNotEqual(live.tobytes(), render("deploy", absent, NOW).tobytes())

    def test_theme_colors_and_identity_are_injected(self):
        theme = {"name": "EXAMPLE", "accent": "#7f8f9f", "foreground": [240, 241, 242], "secondary": "#708090"}
        image = render("clock", {"_theme": theme}, NOW)
        self.assertIn((127, 143, 159), colors(image))
        self.assertIn((240, 241, 242), colors(image))
        self.assertNotEqual(render("identity", {}, NOW, 4).tobytes(), render("identity", {"_theme": theme}, NOW, 4).tobytes())

    def test_theme_supports_external_mark_without_bundled_brand_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mark.png"
            Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)
            themed = render("identity", {"_theme": {"mark_path": str(path)}}, NOW, 0)
            default = render("identity", {}, NOW, 0)
            self.assertNotEqual(themed.tobytes(), default.tobytes())


if __name__ == "__main__":
    unittest.main()
