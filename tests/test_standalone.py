import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pixelctl.config import load
from pixelctl.credentials import store
from pixelctl.standalone import prepare_bundle, validation_errors


class StandaloneBundleTests(unittest.TestCase):
    def test_public_health_bundle_contains_no_private_provider_fields(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PIXELCTL_HOME": folder}):
            root = Path(folder)
            config, _ = load(root / "missing.json")
            config.update({"providers": ["http-health"], "health_url": "https://api.example.com/health", "github_login": "should-not-export", "settings": {"countdown_target": "2027-01-01T00:00:00Z"}})
            bundle = prepare_bundle(config, root / "bundle")
            output = json.loads(bundle["config"].read_text())
            self.assertNotIn("github_login", output)
            self.assertNotIn("github_token_file", output)
            self.assertEqual(output["timezone"], "UTC0")
            self.assertIsInstance(output["settings"]["countdown_target"], int)
            self.assertEqual(bundle["brand"].stat().st_size, 2496)
            self.assertEqual(bundle["identity"].stat().st_size, 2496)

    def test_missing_required_credential_fails_before_device_operations(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PIXELCTL_HOME": folder}):
            root = Path(folder)
            config, _ = load(root / "missing.json")
            config["providers"] = ["github"]
            with self.assertRaisesRegex(ValueError, "credentials set github"):
                prepare_bundle(config, root / "bundle")

    def test_credentials_are_referenced_not_embedded_in_export(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PIXELCTL_HOME": folder}):
            root = Path(folder)
            store("github", "fake-secret-value")
            config, _ = load(root / "missing.json")
            config.update({"providers": ["github"], "github_login": "example"})
            bundle = prepare_bundle(config, root / "bundle")
            content = bundle["config"].read_text()
            self.assertNotIn("fake-secret-value", content)
            self.assertEqual(json.loads(content)["github_token_file"], "/data/pixelctl/credentials/github.token")

    def test_tuple_and_list_theme_colors_export_as_native_hex(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PIXELCTL_HOME": folder}):
            root = Path(folder)
            config, _ = load(root / "missing.json")
            config["theme"].update({"foreground": (10, 20, 30), "accent": [110, 120, 130], "secondary": "#aabbcc"})
            bundle = prepare_bundle(config, root / "bundle")
            self.assertEqual(json.loads(bundle["config"].read_text())["theme"], {"foreground": "#0a141e", "accent": "#6e7882", "secondary": "#aabbcc"})

    def test_enabled_provider_requires_its_resource_config_before_device_writes(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PIXELCTL_HOME": folder}):
            root = Path(folder)
            store("github", "fake-secret-value")
            config, _ = load(root / "missing.json")
            config["providers"] = ["github"]
            with self.assertRaisesRegex(ValueError, "github_login"):
                prepare_bundle(config, root / "bundle")
            config["providers"] = ["http-health"]
            with self.assertRaisesRegex(ValueError, "HTTPS health_url"):
                prepare_bundle(config, root / "bundle")


class StandaloneValidationTests(unittest.TestCase):
    def status(self):
        return {"started_at": 101, "checked_at": 110, "receiver_ok": True, "provider_cycles": 1, "health_cycles": 1,
                "prs": {"updated_at": 105, "complete": True, "error": ""},
                "garden": {"updated_at": 105, "days": 365, "error": ""},
                "deploy": {"updated_at": 105, "state": 1, "error": ""},
                "health": {"updated_at": 105, "state": 1, "error": ""}}

    def test_waits_for_new_runtime_and_all_configured_workers(self):
        status = self.status()
        self.assertIsNone(validation_errors(status | {"started_at": 90}, {"providers": []}, 100))
        self.assertIsNone(validation_errors(status | {"health_cycles": 0}, {"providers": ["http-health"]}, 100))
        self.assertIsNone(validation_errors(status | {"provider_cycles": 0}, {"providers": ["github"]}, 100))

    def test_auth_errors_are_explicit_even_without_updated_timestamp(self):
        status = self.status()
        status["prs"] = {"updated_at": 0, "error": "GitHub permission denied"}
        errors = validation_errors(status, {"providers": ["github"]}, 100)
        self.assertIn("permission denied", errors[0])

    def test_known_negative_results_are_successful_observations(self):
        status = self.status()
        status["deploy"]["state"] = 3
        status["health"].update({"state": 3, "error": "Observed HTTP server error"})
        self.assertEqual(validation_errors(status, {"providers": ["vercel", "http-health"]}, 100), [])

    def test_complete_metadata_fallback_is_valid_with_unknown_ci(self):
        status = self.status()
        status["prs"]["checks_available"] = False
        self.assertEqual(validation_errors(status, {"providers": ["github"]}, 100), [])

    def test_unknown_probe_and_incomplete_pr_list_fail_validation(self):
        status = self.status()
        status["health"]["state"] = 0
        self.assertTrue(validation_errors(status, {"providers": ["http-health"]}, 100))
        status["prs"]["complete"] = False
        self.assertTrue(validation_errors(status, {"providers": ["github"]}, 100))

    def test_stale_values_cannot_validate_success(self):
        status = self.status()
        status["garden"]["updated_at"] = 99
        self.assertIsNone(validation_errors(status, {"providers": ["github"]}, 100))

    def test_configured_alias_must_be_verified_by_device(self):
        status = self.status()
        config = {"providers": ["vercel"], "production_alias": "app.example.com"}
        self.assertTrue(validation_errors(status, config, 100))
        status["deploy"].update({"serving_verified_at": 105, "serving_error": ""})
        self.assertEqual(validation_errors(status, config, 100), [])
        status["deploy"]["serving_error"] = "Production alias verification unavailable"
        self.assertIn("alias", validation_errors(status, config, 100)[0])
