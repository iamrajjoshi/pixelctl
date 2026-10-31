"""Optional read-only providers for pixelctl.

No accounts, organizations, projects or health endpoints are enabled by default.
Credentials remain in existing CLI stores. Availability counts observed probes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_json(command: list[str], payload: dict | None = None) -> dict:
    """Never include CLI output in an exception: it can contain private data."""
    search_path = os.pathsep.join([
        os.environ.get("PATH", ""), str(Path.home() / "Library/pnpm"),
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
    ])
    executable = shutil.which(command[0], path=search_path)
    if not executable:
        raise RuntimeError(f"{command[0]} CLI unavailable")
    result = subprocess.run(
        [executable, *command[1:]],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PATH": search_path, "NO_COLOR": "1", "GH_PROMPT_DISABLED": "1"},
    )
    if result.returncode:
        raise RuntimeError(f"{command[0]} command failed (exit {result.returncode})")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{command[0]} returned invalid JSON") from exc


def _graphql(query: str, variables: dict | None = None) -> dict:
    response = _run_json(
        ["gh", "api", "graphql", "--input", "-"],
        {"query": query, "variables": variables or {}},
    )
    if response.get("errors") or not isinstance(response.get("data"), dict):
        raise RuntimeError("GitHub query unavailable")
    return response["data"]


PRS_QUERY = """
query($search: String!, $after: String) {
  search(query: $search, type: ISSUE, first: 100, after: $after) {
    issueCount pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      number title url isDraft reviewDecision updatedAt
      repository { nameWithOwner }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    } }
  }
}
"""

GARDEN_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def collect_prs(config: dict) -> dict:
    options = config
    scope = f"org:{options['github_org']} " if options.get("github_org") else ""
    search = f"{scope}is:pr is:open author:{options['github_login']} sort:updated-desc"
    cursor = None
    items = []
    total = 0
    for _ in range(10):
        page = _graphql(PRS_QUERY, {"search": search, "after": cursor})["search"]
        total = page["issueCount"]
        for node in page["nodes"]:
            if not node:
                continue
            commits = node["commits"]["nodes"]
            rollup = commits[-1]["commit"].get("statusCheckRollup") if commits else None
            items.append({
                "number": node["number"],
                "title": node["title"],
                "url": node["url"],
                "repo": node["repository"]["nameWithOwner"],
                "draft": node["isDraft"],
                "review": node.get("reviewDecision") or "UNKNOWN",
                "status": rollup["state"] if rollup else "UNKNOWN",
                "updated_at": node["updatedAt"],
            })
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    complete = len(items) == total
    return {
        "items": items,
        "total": total,
        "complete": complete,
        "scope": search,
        "source": "GitHub authored open PRs",
        "updated_at": _now(),
        "error": None if complete else "GitHub search limit reached; list incomplete",
    }


def collect_garden(config: dict) -> dict:
    options = config
    data = _graphql(GARDEN_QUERY, {"login": options["github_login"]})
    if not data.get("user"):
        raise RuntimeError("GitHub user unavailable")
    calendar = data["user"]["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": day["date"], "count": day["contributionCount"]}
        for week in calendar["weeks"]
        for day in week["contributionDays"]
    ][-364:]
    return {
        "days": days,
        "total": sum(day["count"] for day in days),
        "metric_kind": "github_contributions",
        "scope": "Visible GitHub contributions, including commits, PRs, issues and reviews",
        "source": "GitHub contribution calendar",
        "updated_at": _now(),
        "error": None,
    }


def _milliseconds(value: int | float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def _deploy_state(value: str | None) -> str:
    return {
        "READY": "successful",
        "BUILDING": "building",
        "QUEUED": "building",
        "INITIALIZING": "building",
        "ERROR": "failed",
        "CANCELED": "cancelled",
        "CANCELLED": "cancelled",
    }.get(value or "", "unavailable")


def collect_deploy(config: dict) -> dict:
    """Latest production attempt plus the separately verified serving alias."""
    options = config
    result = _run_json([
        "vercel", "list", options["vercel_project"],
        "--environment", "production", "--json", "--yes",
        "--scope", options["vercel_scope"], "--limit", "20",
    ])
    deployments = sorted(
        (item for item in result.get("deployments", []) if item.get("target") == "production"),
        key=lambda item: item.get("createdAt", 0), reverse=True,
    )
    if not deployments:
        raise RuntimeError("No production deployments found")
    latest = deployments[0]
    checked_at = _now()
    output = {
        "state": _deploy_state(latest.get("state")),
        "sha": latest.get("meta", {}).get("githubCommitSha"),
        "environment": "production",
        "created_at": _milliseconds(latest.get("createdAt")),
        "deployed_at": _milliseconds(latest.get("ready")) if latest.get("state") == "READY" else None,
        "url": "https://" + latest["url"],
        "project": options["vercel_project"],
        "source": "Vercel latest production-target deployment",
        "updated_at": checked_at,
        "error": None,
    }
    if not options.get("production_alias"):
        output["serving"] = {"state": "unavailable", "error": "No production alias configured"}
        return output
    try:
        alias = _run_json([
            "vercel", "inspect", options["production_alias"], "--json",
            "--scope", options["vercel_scope"],
        ])
        if (
            alias.get("target") != "production"
            or options["production_alias"] not in alias.get("aliases", [])
        ):
            raise RuntimeError("Production alias could not be verified")
        matching = next((item for item in deployments if item.get("url") == alias.get("url")), {})
        output["serving"] = {
            "state": _deploy_state(alias.get("readyState")),
            "sha": matching.get("meta", {}).get("githubCommitSha"),
            "deployed_at": _milliseconds(matching.get("ready")),
            "url": "https://" + options["production_alias"],
            "deployment_url": "https://" + alias["url"],
            "verified_at": checked_at,
            "error": None,
        }
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError, KeyError):
        output["serving"] = {
            "state": "unavailable", "error": "Production alias unavailable",
            "verified_at": checked_at,
        }
    return output


_COLLECTION_ERRORS = (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError, KeyError, TypeError)


def _collect_section(name: str, function, config: dict) -> dict:
    try:
        return function(config)
    except _COLLECTION_ERRORS as exc:
        return {
            "state": "unavailable", "updated_at": _now(),
            "error": str(exc) if isinstance(exc, RuntimeError) else f"{name} collection unavailable",
        }


def github_provider(config: dict, previous_state: dict | None = None) -> dict:
    """Configure github_login and optionally github_org; otherwise disabled."""
    if not config.get("github_login"):
        return {}
    return {
        "prs": _collect_section("prs", collect_prs, config),
        "garden": _collect_section("garden", collect_garden, config),
    }


def vercel_provider(config: dict, previous_state: dict | None = None) -> dict:
    """Configure vercel_project and vercel_scope; otherwise disabled."""
    if not config.get("vercel_project") and not config.get("vercel_scope"):
        return {}
    if not config.get("vercel_project") or not config.get("vercel_scope"):
        return {"deploy": {"state": "unavailable", "updated_at": _now(), "error": "Both vercel_project and vercel_scope are required"}}
    return {"deploy": _collect_section("deploy", collect_deploy, config)}


def probe_health(config: dict, previous_state: dict | None = None) -> dict:
    """A configured health probe; transport errors are UNKNOWN, not an outage."""
    if not config.get("health_url"):
        return {}
    options = config
    expected = config.get("health_expected", {"status": "ok"})
    if not isinstance(expected, dict) or not expected:
        raise ValueError("health_expected must be a nonempty JSON object")
    if not isinstance(options["health_url"], str) or not options["health_url"].startswith("https://"):
        raise ValueError("health_url must use HTTPS")
    previous_state = previous_state or {}
    previous_health = previous_state.get("health", {})
    previous_uptime = previous_state.get("uptime", {})
    # A changed endpoint starts a new measurement window.
    if (
        previous_uptime.get("url") != options["health_url"]
        or previous_uptime.get("expected") != expected
    ):
        previous_uptime = {}
        previous_health = {}
    checked_at = _now()
    started = time.monotonic()
    state = "unknown"
    status_code = None
    error = None
    latency_ms = None
    request = urllib.request.Request(
        options["health_url"], headers={"User-Agent": "pixelctl/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            status_code = response.status
            raw = response.read(4097)
            latency_ms = round((time.monotonic() - started) * 1000)
            if status_code == 200 and len(raw) <= 4096:
                try:
                    body = json.loads(raw)
                    state = "ok" if isinstance(body, dict) and all(body.get(key) == value for key, value in expected.items()) else "unknown"
                except (json.JSONDecodeError, UnicodeDecodeError):
                    state = "unknown"
            if state == "unknown":
                error = "Health response did not match the expected JSON"
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        latency_ms = round((time.monotonic() - started) * 1000)
        state = "down" if 500 <= exc.code < 600 else "unknown"
        error = f"Health probe returned HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError):
        error = "Endpoint unreachable from this monitor; service state unknown"

    samples = previous_uptime.get("samples", 0) + (state != "unknown")
    failures = previous_uptime.get("failures", 0) + (state == "down")
    unknown = previous_uptime.get("unknown", 0) + (state == "unknown")
    history = (previous_health.get("history", []) + [latency_ms if state == "ok" else None])[-52:]
    health = {
        "state": state, "status_code": status_code, "latency_ms": latency_ms,
        "url": options["health_url"], "checked_at": checked_at, "updated_at": checked_at,
        "scope": "Configured health endpoint, observed from this monitor",
        "source": "HTTP 200 with configured JSON predicate", "history": history, "error": error,
    }
    uptime = {
        "availability_pct": round(100 * (samples - failures) / samples, 3) if samples else None,
        "window_start": previous_uptime.get("window_start", checked_at),
        "updated_at": checked_at,
        "samples": samples, "checks": samples + unknown,
        "failures": failures, "unknown": unknown,
        "scope": "Observed checks only; unknown results and unobserved gaps excluded",
        "label": "LOCAL", "url": options["health_url"], "expected": expected, "error": None,
    }
    return {"health": health, "uptime": uptime}


provider_functions = {
    "github": github_provider,
    "vercel": vercel_provider,
    "http-health": probe_health,
}


def collect_sources(config: dict, previous_state: dict | None = None) -> dict:
    """Collect only configured built-ins; applications may register their own functions."""
    result = {}
    for function in provider_functions.values():
        result.update(function(config, previous_state))
    return result
