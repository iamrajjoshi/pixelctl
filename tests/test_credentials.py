import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pixelctl.credentials import credential_path, set_interactive, status, store, validate


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"PIXELCTL_HOME": self.directory.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_token_is_private_and_status_never_contains_it(self):
        token = "private-test-value"
        path = store("github", token, metadata={"validated": True})
        self.assertEqual(path.read_text(), token)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(token, json.dumps(status()))

    def test_malformed_token_does_not_create_file(self):
        for token in ("", "a b", "one\ntwo", "x" * 4096):
            with self.assertRaises(ValueError):
                store("github", token)
        self.assertFalse(credential_path("github").exists())

    def test_service_name_cannot_escape_credential_directory(self):
        with self.assertRaises(ValueError):
            store("../../unexpected", "test-value")

    def test_hidden_prompt_validation_precedes_storage(self):
        output = io.StringIO()
        with patch("pixelctl.credentials.getpass.getpass", return_value="test-value"), patch("pixelctl.credentials.validate", side_effect=ValueError("denied")), contextlib.redirect_stdout(output):
            with self.assertRaises(ValueError):
                set_interactive("github", {})
        self.assertFalse(credential_path("github").exists())
        self.assertNotIn("test-value", output.getvalue())

    def test_invalid_header_token_is_rejected_without_exposing_it(self):
        with patch("pixelctl.credentials.urllib.request.build_opener") as opener:
            for token in ("private\nsecret", "private\rsecret", "private secret", "private\u2603secret"):
                with self.assertRaises(ValueError) as raised:
                    validate("github", token, {})
                self.assertNotIn(token, str(raised.exception))
            opener.assert_not_called()

    def test_vercel_validation_uses_explicit_team_context(self):
        for scope, key in (("example-team", "slug"), ("team_example", "teamId")):
            with patch("pixelctl.credentials.urllib.request.build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"id":"prj_example","name":"example-project"}'
                validate("vercel", "test-only-value", {"vercel_project_id": "prj_example", "vercel_scope": scope})
                request = opener.return_value.open.call_args.args[0]
                self.assertIn(f"{key}={scope}", request.full_url)

    def test_github_validation_identifies_scope_of_its_proof(self):
        with patch("pixelctl.credentials.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"login":"example-user"}'
            result = validate("github", "test-only-value", {"github_login": "example-user"})
            self.assertEqual(result["validation"], "identity")

    def test_vercel_response_must_match_configured_project_name(self):
        with patch("pixelctl.credentials.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"id":"prj_other","name":"other-project"}'
            with self.assertRaisesRegex(ValueError, "different configured project"):
                validate("vercel", "test-only-value", {"vercel_project": "example-project"})
