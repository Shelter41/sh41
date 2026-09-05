import json
import os
import secrets
from pathlib import Path

import click

from . import __version__
from .spec import AgentSpec, parse_yaml
from .supervisor import request


@click.group()
@click.version_option(__version__, prog_name="sh41 local")
def main() -> None:
    """Run persistent coding agents locally."""


def call(payload):
    try:
        return request(payload)
    except (RuntimeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None


def emit(value, as_json=False):
    if as_json or not isinstance(value, list):
        click.echo(json.dumps(value, indent=2))
    elif not value:
        click.echo("No records.")
    else:
        keys = [key for key in ("slug", "harness", "workspace", "state", "id", "status", "active")
                if key in value[0]]
        widths = {key: max(len(key), max(len(str(row.get(key) or "-")) for row in value))
                  for key in keys}
        click.echo("  ".join(key.upper().ljust(widths[key]) for key in keys))
        for row in value:
            click.echo("  ".join(str(row.get(key) or "-").ljust(widths[key]) for key in keys))


def deploy_spec(spec: AgentSpec):
    missing = spec.secret_names() - os.environ.keys()
    if missing:
        raise click.ClickException("Missing environment variables: " + ", ".join(sorted(missing)))
    return call({"op": "deploy", "spec": spec.model_dump(),
                 "secrets": {key: os.environ[key] for key in spec.secret_names()}})


@main.command()
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def deploy(manifest):
    """Deploy an agent from YAML."""
    try:
        spec = parse_yaml(manifest.read_text()).resolved(manifest.resolve().parent)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    emit(deploy_spec(spec))


@main.command()
@click.argument("name", required=False)
@click.option("--harness", type=click.Choice(["opencode", "claude-code", "codex"]), default="opencode")
@click.option("--workspace", default="default", show_default=True)
@click.option("--source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--model")
@click.option("--ollama", is_flag=True)
@click.option("--base-url")
@click.option("--api-key-env")
@click.option("--instructions", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--mcp", "servers", nargs=2, multiple=True, metavar="NAME JSON")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--write-only", is_flag=True)
def launch(name, harness, workspace, source, model, ollama, base_url, api_key_env,
           instructions, servers, output, write_only):
    """Generate YAML from flags and deploy; NAME is optional."""
    if ollama and base_url:
        raise click.UsageError("--ollama and --base-url are mutually exclusive")
    name = name or f"{harness}-{secrets.token_hex(2)}"
    payload = {"agent": name, "harness": harness, "workspace": workspace,
               "inference": {"provider": "ollama" if ollama else
                             "openai-compatible" if base_url else "native"}}
    if model:
        payload["model"] = model
    if base_url:
        payload["inference"]["base_url"] = base_url
    if api_key_env:
        payload["inference"]["api_key"] = {"env": api_key_env}
    if source:
        payload["source"] = {"provider": "local", "path": str(source.resolve())}
    if instructions:
        payload["instructions"] = str(instructions.resolve())
    try:
        if len({name for name, _ in servers}) != len(servers):
            raise ValueError("MCP names must be unique")
        payload["mcp"] = {name: json.loads(raw) for name, raw in servers}
        # Same validation and error sanitization as authored YAML.
        import yaml
        spec = parse_yaml(yaml.safe_dump(payload))
        destination = output or Path(f"{spec.agent}.yaml")
        with destination.open("x") as stream:
            stream.write(spec.as_yaml())
    except FileExistsError:
        raise click.ClickException("Manifest already exists; use deploy or another --output") from None
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"Wrote {destination}")
    if not write_only:
        emit(deploy_spec(spec))


@main.command()
@click.option("--json", "as_json", is_flag=True)
def agents(as_json):
    """List durable agent identities."""
    emit(call({"op": "agents"}), as_json)


main.add_command(agents, "ls")


def list_command(name):
    @main.command(name)
    @click.argument("agent")
    @click.option("--json", "as_json", is_flag=True)
    def command(agent, as_json):
        """List an agent's durable records."""
        emit(call({"op": name, "agent": agent}), as_json)


for _name in ("sessions", "history"):
    list_command(_name)
