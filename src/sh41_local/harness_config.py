from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import tomli_w

from .paths import atomic_json, private_dir
from .spec import AgentSpec


def native_mcp(spec: AgentSpec, secrets: dict[str, str]) -> dict:
    servers = {}
    for name, server in spec.mcp.items():
        if server.transport == "stdio":
            servers[name] = {"type": "stdio", "command": server.command[0], "args": server.command[1:],
                             "env": {key: secrets[ref.env] for key, ref in server.env.items()}}
        else:
            servers[name] = {"type": "http", "url": server.url,
                             "headers": {key: secrets[ref.env] for key, ref in server.headers.items()}}
    return servers


def configure(home: Path, control: Path, payload: dict) -> dict[str, str]:
    spec = AgentSpec.model_validate(payload["spec"])
    secrets = payload.get("secrets") or {}
    servers = native_mcp(spec, secrets)
    key = secrets.get(spec.inference.api_key.env) if spec.inference.api_key else None
    native = payload.get("native")
    private_dir(home)
    private_dir(control)
    environment = {}
    if spec.harness == "claude-code":
        directory = private_dir(home / ".claude")
        config = directory / ".claude.json"
        existing = json.loads(config.read_text()) if config.exists() else {}
        existing.update({"hasCompletedOnboarding": True, "theme": "dark",
                         "mcpServers": servers,
                         "projects": {"/workspace/agent": {"hasTrustDialogAccepted": True,
                                                          "hasCompletedProjectOnboarding": True}}})
        atomic_json(config, existing)
        # Older pinned releases use the HOME-level config even with CLAUDE_CONFIG_DIR.
        atomic_json(home / ".claude.json", existing)
        atomic_json(directory / "settings.json", {"permissions": {"defaultMode": "bypassPermissions"},
                                                 "skipDangerousModePermissionPrompt": True})
        auth_file = directory / ".credentials.json"
        if key:
            environment["ANTHROPIC_API_KEY"] = key
    elif spec.harness == "codex":
        directory = private_dir(home / ".codex")
        config = {"projects": {"/workspace/agent": {"trust_level": "trusted"}},
                  "mcp_servers": {}}
        for name, server in servers.items():
            if server["type"] == "stdio":
                config["mcp_servers"][name] = {k: server[k] for k in ("command", "args", "env")}
            else:
                config["mcp_servers"][name] = {"url": server["url"], "http_headers": server["headers"]}
        path = directory / "config.toml"
        path.write_text(tomli_w.dumps(config))
        path.chmod(0o600)
        auth_file = directory / "auth.json"
        if key:
            native = {"OPENAI_API_KEY": key}
            environment["OPENAI_API_KEY"] = key
    else:
        url = payload.get("inference_url") or spec.inference.base_url
        if not url:
            raise ValueError("OpenCode requires a reachable inference URL")
        models = {spec.model: {"name": spec.model}}
        if spec.inference.provider == "ollama":
            models[spec.model]["limit"] = {"context": 16384, "output": 4096}
        mcp = {}
        for name, server in servers.items():
            if server["type"] == "stdio":
                mcp[name] = {"type": "local", "command": [server["command"], *server["args"]],
                             "environment": server["env"]}
            else:
                mcp[name] = {"type": "remote", "url": server["url"], "headers": server["headers"],
                             "oauth": False}
        config = {"$schema": "https://opencode.ai/config.json", "model": f"local/{spec.model}",
                  "small_model": f"local/{spec.model}", "enabled_providers": ["local"],
                  "permission": "allow", "autoupdate": False, "share": "disabled",
                  "provider": {"local": {"npm": "@ai-sdk/openai-compatible", "models": models,
                                         "options": {"baseURL": url, "apiKey": key or "local"}}},
                  "mcp": mcp}
        atomic_json(home / ".config/opencode/opencode.json", config)
        return {"OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_SHARE": "true",
                "OPENCODE_DISABLE_MODELS_FETCH": "true", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
                "OPENCODE_DISABLE_LSP_DOWNLOAD": "true"}
    if native and not (spec.harness == "claude-code" and key):
        digest = hashlib.sha256(json.dumps(native, sort_keys=True).encode()).hexdigest()
        marker = control / "credential-revision"
        if not auth_file.exists() or not marker.exists() or marker.read_text() != digest:
            atomic_json(auth_file, native)
            marker.write_text(digest)
    if spec.harness == "claude-code" and key:
        auth_file.unlink(missing_ok=True)
    if not key and not auth_file.exists():
        raise ValueError(f"Missing {spec.harness} credentials; import account credentials or set --api-key-env")
    if spec.harness == "codex" and (control / "instructions.md").exists():
        (home / ".codex/AGENTS.md").write_text((control / "instructions.md").read_text())
    elif spec.harness == "codex":
        (home / ".codex/AGENTS.md").unlink(missing_ok=True)
    return environment


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[redacted]@", value)
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
                   "[redacted]", value)
    return value
