"""Private credential input and validation for device-side providers."""
from __future__ import annotations

import getpass
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import home, read_json, save_json

SERVICES = ("github", "vercel")


def _normalize_token(token: str) -> str:
    token = token.strip()
    if not token or len(token) > 4095 or any(ord(char) <= 32 or ord(char) >= 127 for char in token):
        raise ValueError("Expected one nonempty ASCII token with no whitespace or control characters")
    return token


def credential_path(service: str) -> Path:
    if service not in SERVICES:
        raise ValueError("Unsupported credential service")
    return home() / "credentials" / f"{service}.token"


def store(service: str, token: str, *, metadata=None) -> Path:
    token = _normalize_token(token)
    path = credential_path(service)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(".new")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(token)
    os.replace(temporary, path)
    save_json(path.with_suffix(".json"), {"service": service, "saved_at": datetime.now(timezone.utc).isoformat()} | (metadata or {}))
    return path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Credential validation refused an unexpected redirect")


def validate(service: str, token: str, config: dict):
    # Validate before constructing an HTTP header. http.client's error for an
    # embedded newline includes the offending header value, including secrets.
    token = _normalize_token(token)
    if service == "github":
        url = "https://api.github.com/user"
    elif service == "vercel":
        project = config.get("vercel_project_id") or config.get("vercel_project")
        if not project:
            raise ValueError("Configure vercel_project or vercel_project_id before validating its token")
        url = "https://api.vercel.com/v9/projects/" + urllib.parse.quote(project, safe="")
        scope = config.get("vercel_scope")
        if scope:
            url += "?" + urllib.parse.urlencode({"teamId" if scope.startswith("team_") else "slug": scope})
    else:
        raise ValueError("Unsupported credential service")
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "User-Agent": "pixelctl/standalone", "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            body = json.loads(response.read(1024 * 1024))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"{service} credential validation returned HTTP {exc.code}") from None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError(f"{service} credential validation could not complete") from None
    if not isinstance(body, dict):
        raise ValueError(f"{service} credential validation returned an unexpected response")
    if service == "github":
        if not body.get("login"):
            raise ValueError("GitHub did not return an authenticated identity")
        expected = config.get("github_login")
        if expected and body["login"].lower() != expected.lower():
            raise ValueError("GitHub token belongs to a different configured user")
        return {"validated": True, "identity": body["login"], "validation": "identity"}
    if not body.get("id"):
        raise ValueError("Vercel token could not read the configured project")
    expected = config.get("vercel_project_id")
    if expected and body["id"] != expected:
        raise ValueError("Vercel token belongs to a different configured project")
    requested = config.get("vercel_project")
    if not expected and requested:
        actual = body["id"] if requested.startswith("prj_") else body.get("name")
        if actual != requested:
            raise ValueError("Vercel token belongs to a different configured project")
    return {"validated": True, "project_id": body["id"], "project_name": body.get("name")}


def set_interactive(service: str, config: dict):
    token = getpass.getpass(f"{service} token (hidden): ")
    metadata = validate(service, token, config)
    path = store(service, token, metadata=metadata)
    print(f"Validated and stored {service} credential privately: {path}")


def status():
    result = {}
    for service in SERVICES:
        path = credential_path(service)
        result[service] = {"configured": path.exists()}
        if path.exists():
            result[service].update(read_json(path.with_suffix(".json"), {}))
            result[service]["private_permissions"] = path.stat().st_mode & 0o077 == 0
    return result
