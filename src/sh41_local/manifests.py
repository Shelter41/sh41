"""Shared authoring path for CLI flags and the shell wizard."""
from pathlib import Path

import yaml

from .spec import AgentSpec, parse_yaml


def build_spec(*, name, harness, workspace="default", source=None, source_mode="copy", model=None,
               provider="native", base_url=None, api_key_env=None, instructions=None, mcp=None):
    payload = {"agent": name, "harness": harness, "workspace": workspace,
               "inference": {"provider": provider}, "mcp": mcp or {}}
    if model:
        payload["model"] = model
    if base_url:
        payload["inference"]["base_url"] = base_url
    if api_key_env:
        payload["inference"]["api_key"] = {"env": api_key_env}
    if source:
        payload["source"] = {"provider": "local", "path": str(source), "mode": source_mode}
    if instructions:
        payload["instructions"] = str(instructions)
    return parse_yaml(yaml.safe_dump(payload)).resolved(Path.cwd())


def write_manifest(spec: AgentSpec, destination: Path):
    with destination.expanduser().open("x") as stream:
        stream.write(spec.as_yaml())
