from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

from .paths import atomic_json


def import_native(root: Path, harness: str, file: Path | None = None):
    if file:
        raw = file.read_text()
    elif harness == "claude-code" and platform.system() == "Darwin":
        try:
            found = subprocess.run(["security", "find-generic-password", "-s",
                                    "Claude Code-credentials", "-w"],
                                   capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise ValueError("Keychain access timed out; use --file with exported native credentials") from None
        if found.returncode:
            raise ValueError("Claude credentials unavailable; log in with Claude or use --file")
        raw = found.stdout
    else:
        path = Path.home() / (".claude/.credentials.json" if harness == "claude-code" else ".codex/auth.json")
        if not path.exists():
            raise ValueError("Native credentials not found; log in with the harness or use --file")
        raw = path.read_text()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not data:
            raise ValueError
        if harness == "claude-code":
            oauth = data.get("claudeAiOauth", data)
            if not all(oauth.get(key) for key in ("accessToken", "refreshToken", "expiresAt")):
                raise ValueError
            data = {"claudeAiOauth": oauth}
        elif not (data.get("tokens") or data.get("OPENAI_API_KEY")):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid native credential file; export credentials from the harness again") from None
    atomic_json(root / "credentials" / f"{harness}.json", data)


def native_profile(root: Path, harness: str) -> dict | None:
    path = root / "credentials" / f"{harness}.json"
    return json.loads(path.read_text()) if path.exists() else None


def required_secrets(spec) -> dict[str, str]:
    names = spec.secret_names()
    missing = names - os.environ.keys()
    if missing:
        raise ValueError("Missing environment variables: " + ", ".join(sorted(missing)))
    return {name: os.environ[name] for name in names}
