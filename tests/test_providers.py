import io
import unittest
import urllib.error
from unittest.mock import patch

from pixelctl import providers


CONFIG = {
    "github_login": "test-user", "github_org": "example-org",
    "vercel_scope": "example-team", "vercel_project": "example-app",
    "production_alias": "app.example.com", "health_url": "https://api.example.com/health",
}


def pr(number):
    return {
        "number": number, "title": f"PR {number}",
        "url": f"https://github.com/example-org/example-repo/pull/{number}",
        "isDraft": False, "reviewDecision": "REVIEW_REQUIRED",
        "updatedAt": "2026-10-31T22:00:00Z",
        "repository": {"nameWithOwner": "example-org/example-repo"},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]},
    }


class Response(io.BytesIO):
    status = 200


class ProviderTests(unittest.TestCase):
    @patch("pixelctl.providers._graphql")
    def test_prs_follow_all_pages_and_keep_failed_checks(self, graphql):
        graphql.side_effect = [
            {"search": {"issueCount": 2, "nodes": [pr(1)], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}},
            {"search": {"issueCount": 2, "nodes": [pr(2)], "pageInfo": {"hasNextPage": False, "endCursor": "end"}}},
        ]
        result = providers.collect_prs(CONFIG)
        self.assertEqual([item["number"] for item in result["items"]], [1, 2])
        self.assertEqual(result["items"][0]["status"], "FAILURE")
        self.assertTrue(result["complete"])
        self.assertEqual(graphql.call_args_list[1].args[1]["after"], "next")

    @patch("pixelctl.providers._graphql")
    def test_search_limit_is_explicitly_incomplete(self, graphql):
        graphql.return_value = {"search": {"issueCount": 1001, "nodes": [pr(1)], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}
        result = providers.collect_prs(CONFIG)
        self.assertFalse(result["complete"])
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["total"], 1001)

    @patch("pixelctl.providers._run_json")
    def test_failed_production_attempt_does_not_hide_behind_serving_success(self, run_json):
        run_json.side_effect = [
            {"deployments": [
                {"url": "failed.example", "target": "production", "state": "ERROR", "createdAt": 2000, "meta": {"githubCommitSha": "failed"}},
                {"url": "live.example", "target": "production", "state": "READY", "createdAt": 1000, "ready": 1500, "meta": {"githubCommitSha": "live"}},
                {"url": "preview.example", "target": "preview", "state": "BUILDING", "createdAt": 3000},
            ]},
            {"url": "live.example", "target": "production", "readyState": "READY", "aliases": ["app.example.com"]},
        ]
        result = providers.collect_deploy(CONFIG)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["sha"], "failed")
        self.assertIsNone(result["deployed_at"])
        self.assertEqual(result["serving"]["state"], "successful")
        self.assertEqual(result["serving"]["sha"], "live")

    @patch("pixelctl.providers._run_json")
    def test_alias_must_be_verified_as_production(self, run_json):
        run_json.side_effect = [
            {"deployments": [{"url": "live.example", "target": "production", "state": "READY", "createdAt": 1000}]},
            {"url": "live.example", "target": "preview", "readyState": "READY", "aliases": ["app.example.com"]},
        ]
        result = providers.collect_deploy(CONFIG)
        self.assertEqual(result["serving"]["state"], "unavailable")

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_health_requires_expected_json_not_any_200(self, urlopen):
        urlopen.return_value = Response(b"<html>Login</html>")
        result = providers.probe_health(CONFIG)
        self.assertEqual(result["health"]["state"], "unknown")
        self.assertIsNone(result["uptime"]["availability_pct"])
        self.assertEqual(result["uptime"]["samples"], 0)

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_network_timeout_is_unknown_and_not_counted_as_outage(self, urlopen):
        urlopen.return_value = Response(b'{"status":"ok"}')
        previous = providers.probe_health(CONFIG)
        urlopen.side_effect = TimeoutError()
        result = providers.probe_health(CONFIG, previous)
        self.assertEqual(result["health"]["state"], "unknown")
        self.assertEqual(result["uptime"]["availability_pct"], 100)
        self.assertEqual(result["uptime"]["samples"], 1)
        self.assertEqual(result["uptime"]["failures"], 0)
        self.assertEqual(result["uptime"]["unknown"], 1)
        self.assertIsNone(result["health"]["history"][-1])

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_server_error_counts_only_observed_failure(self, urlopen):
        urlopen.return_value = Response(b'{"status":"ok"}')
        previous = providers.probe_health(CONFIG)
        urlopen.side_effect = urllib.error.HTTPError("https://api.example.com/health", 503, "unavailable", {}, None)
        result = providers.probe_health(CONFIG, previous)
        self.assertEqual(result["health"]["state"], "down")
        self.assertEqual(result["uptime"]["availability_pct"], 50)
        self.assertEqual(result["uptime"]["failures"], 1)
        self.assertEqual(result["uptime"]["samples"], 2)

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_access_denial_does_not_assert_production_outage(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError("https://api.example.com/health", 403, "forbidden", {}, None)
        result = providers.probe_health(CONFIG)
        self.assertEqual(result["health"]["state"], "unknown")
        self.assertIsNone(result["uptime"]["availability_pct"])

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_different_endpoint_starts_new_measurement_window(self, urlopen):
        urlopen.return_value = Response(b'{"status":"ok"}')
        previous = providers.probe_health(CONFIG)
        urlopen.return_value = Response(b'{"status":"ok"}')
        result = providers.probe_health(CONFIG | {"health_url": "https://other.example/health"}, previous)
        self.assertEqual(result["uptime"]["samples"], 1)

    @patch("pixelctl.providers.collect_deploy", side_effect=RuntimeError("Vercel unavailable"))
    @patch("pixelctl.providers.collect_garden", return_value={"total": 10, "error": None})
    @patch("pixelctl.providers.collect_prs", return_value={"total": 2, "error": None})
    def test_one_source_failure_does_not_erase_other_sources(self, *_):
        with patch("pixelctl.providers.probe_health", return_value={}):
            # The registry resolves the original functions; only suppress network.
            with patch.dict(providers.provider_functions, {"http-health": lambda c, p: {}}):
                result = providers.collect_sources(CONFIG)
        self.assertEqual(result["prs"]["total"], 2)
        self.assertEqual(result["garden"]["total"], 10)
        self.assertEqual(result["deploy"]["state"], "unavailable")


class ConfigurationTests(unittest.TestCase):
    @patch("pixelctl.providers._run_json")
    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_absent_providers_do_no_io_and_return_no_sections(self, urlopen, run_json):
        self.assertEqual(providers.collect_sources({}), {})
        self.assertEqual(providers.probe_health({}), {})
        urlopen.assert_not_called()
        run_json.assert_not_called()

    @patch("pixelctl.providers._graphql")
    def test_github_org_is_optional_for_personal_prs(self, graphql):
        graphql.return_value = {"search": {"issueCount": 0, "nodes": [], "pageInfo": {"hasNextPage": False}}}
        providers.collect_prs({"github_login": "test-user"})
        self.assertNotIn("org:", graphql.call_args.args[1]["search"])
        self.assertIn("author:test-user", graphql.call_args.args[1]["search"])

    def test_partial_vercel_config_does_not_select_an_account_implicitly(self):
        result = providers.vercel_provider({"vercel_project": "example-app"})
        self.assertEqual(result["deploy"]["state"], "unavailable")
        self.assertIn("required", result["deploy"]["error"])

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_configured_predicate_is_strict(self, urlopen):
        urlopen.return_value = Response(b'{"healthy":true,"status":"ready"}')
        result = providers.probe_health(CONFIG | {"health_expected": {"healthy": True, "status": "ready"}})
        self.assertEqual(result["health"]["state"], "ok")
        urlopen.return_value = Response(b'{"healthy":true,"status":"ready"}')
        result = providers.probe_health(CONFIG)
        self.assertEqual(result["health"]["state"], "unknown")

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_health_uses_https_and_nonempty_predicate(self, urlopen):
        with self.assertRaises(ValueError):
            providers.probe_health({"health_url": "http://example.com/health"})
        with self.assertRaises(ValueError):
            providers.probe_health(CONFIG | {"health_expected": {}})
        urlopen.assert_not_called()

    @patch("pixelctl.providers.urllib.request.urlopen")
    def test_changed_predicate_starts_new_availability_window(self, urlopen):
        urlopen.return_value = Response(b'{"status":"ok"}')
        previous = providers.probe_health(CONFIG)
        urlopen.return_value = Response(b'{"healthy":true}')
        result = providers.probe_health(CONFIG | {"health_expected": {"healthy": True}}, previous)
        self.assertEqual(result["uptime"]["samples"], 1)
        self.assertEqual(result["uptime"]["availability_pct"], 100)

    @patch("pixelctl.providers._run_json")
    def test_without_alias_latest_ready_is_not_claimed_live(self, run_json):
        run_json.return_value = {"deployments": [{"url": "ready.example", "target": "production", "state": "READY", "createdAt": 1000}]}
        config = {key: value for key, value in CONFIG.items() if key != "production_alias"}
        result = providers.collect_deploy(config)
        self.assertEqual(result["state"], "successful")
        self.assertEqual(result["serving"]["state"], "unavailable")
        self.assertEqual(run_json.call_count, 1)


if __name__ == "__main__":
    unittest.main()
