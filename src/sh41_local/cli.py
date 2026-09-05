import json
import os
import secrets
import subprocess
import time
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
        click.echo("Preparing the agent; first use builds an image and may download a model.", err=True)
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


def lifecycle_command(name):
    @main.command(name)
    @click.argument("agent")
    def command(agent):
        """Change an agent's execution lifecycle, retaining its files."""
        payload = {"op": name, "agent": agent}
        if name == "redeploy":
            payload["secrets"] = agent_secrets(agent)
        emit(call(payload))


for _name in ("pause", "resume", "park", "redeploy"):
    lifecycle_command(_name)


@main.command("export")
@click.argument("agent")
@click.option("--output", required=True, type=click.Path(path_type=Path))
def export_command(agent, output):
    """Copy working files to a new local directory."""
    emit(call({"op": "export", "agent": agent, "output": str(output.resolve())}))


@main.command()
def doctor():
    """Check local execution dependencies."""
    from .docker import DockerProvider
    from .inference import ollama_binary
    from .paths import state_home

    checks = {"state_home": str(state_home())}
    try:
        checks["ollama"] = ollama_binary()
    except ValueError:
        checks["ollama"] = "not installed (optional for remote inference)"
    try:
        DockerProvider(state_home()).require()
        checks["docker"] = "ready"
    except RuntimeError as exc:
        checks["docker"] = str(exc)
    emit(checks)


def agent_secrets(agent):
    from .credentials import required_secrets
    spec = AgentSpec.model_validate(call({"op": "spec", "agent": agent}))
    try:
        return required_secrets(spec)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None


def print_event(event, as_json=False):
    if as_json:
        click.echo(json.dumps(event))
        return
    if event.get("type") == "user":
        return
    message = event.get("message")
    if isinstance(message, str):
        click.echo(message)
    elif isinstance(message, dict):
        blocks = message.get("content") or message.get("parts") or []
        if isinstance(blocks, str):
            click.echo(blocks)
        else:
            for block in blocks:
                if isinstance(block, dict) and block.get("text"):
                    click.echo(block["text"])
    elif event.get("result"):
        click.echo(event["result"])


@main.command()
@click.argument("agent")
@click.option("--message", required=True)
@click.option("--json", "as_json", is_flag=True)
def run(agent, message, as_json):
    """Send a message and stream durable run events."""
    result = call({"op": "run", "agent": agent, "message": message, "secrets": agent_secrets(agent)})
    offset = 0
    try:
        while True:
            state = call({"op": "run-status", "agent": agent, "run_id": result["id"], "offset": offset})
            for event in state["events"]:
                print_event(event, as_json)
            offset += len(state["events"])
            if state["status"] not in {"pending", "running"}:
                if as_json:
                    click.echo(json.dumps({"type": "run_end", "id": result["id"], "status": state["status"]}))
                elif state["status"] != "completed":
                    click.echo(f"Run {state['status']}. Use sh41 attach to inspect the agent.", err=True)
                raise SystemExit(0 if state["status"] == "completed" else 1)
            time.sleep(0.5)
    except KeyboardInterrupt:
        call({"op": "interrupt", "agent": agent})
        raise click.Abort() from None


main.add_command(run, "chat")


@main.command()
@click.argument("agent")
@click.option("--read-only", is_flag=True)
def attach(agent, read_only):
    """Attach to the native terminal; detach with Ctrl-b then d."""
    from .docker import docker_binary
    if not os.isatty(0):
        raise click.ClickException("attach requires an interactive terminal")
    result = call({"op": "attach", "agent": agent, "readonly": read_only,
                   "secrets": agent_secrets(agent)})
    if result["readonly"]:
        click.echo("Read-only attachment.")
    code = subprocess.call([docker_binary(), "exec", "-it", result["container"], "python", "-m",
                            "sh41_local.worker", "attach", result["token"], json.dumps(result["argv"])])
    raise SystemExit(code)


@main.command()
@click.argument("agent")
def interrupt(agent):
    """Interrupt the current agent turn."""
    emit(call({"op": "interrupt", "agent": agent}))


@main.command("new-session")
@click.argument("agent")
def new_session(agent):
    """Start a fresh conversation, preserving previous conversations."""
    emit(call({"op": "new-session", "agent": agent}))


@main.command("continue-session")
@click.argument("agent")
@click.argument("session_id")
def continue_session(agent, session_id):
    """Select a previous conversation for the next turn."""
    emit(call({"op": "continue-session", "agent": agent, "session_id": session_id}))


def auth_command(name, harness):
    @main.command(name)
    @click.option("--file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    def command(file):
        """Explicitly import native credentials into local private storage."""
        from .credentials import import_native
        from .paths import state_home
        try:
            import_native(state_home(), harness, file)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(f"Imported {harness} credentials locally.")


auth_command("claude-auth", "claude-code")
auth_command("codex-auth", "codex")


@main.group()
def models():
    """Manage local Ollama models and its serving process."""


def ollama_service():
    from .docker import DockerProvider
    from .inference import Ollama
    from .paths import state_home
    root = state_home()
    return Ollama(root, DockerProvider(root))


@models.command("pull")
@click.argument("model")
def pull_model(model):
    """Start or reuse Ollama and download a tool-capable local model."""
    last = None

    def progress(event):
        nonlocal last
        percent = int(100 * event.get("completed", 0) / event["total"]) if event.get("total") else None
        message = event.get("status", "downloading") + (f" {percent}%" if percent is not None else "")
        if message != last:
            click.echo(message, err=True)
            last = message
    try:
        ollama_service().pull(model, progress=progress)
    except Exception as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"Ready: {model}")


@models.command("ls")
def list_models():
    try:
        emit(ollama_service().models(), as_json=True)
    except Exception as exc:
        raise click.ClickException(str(exc)) from None


@models.command("stop")
def stop_models():
    """Stop only sh41-owned Ollama, retaining downloaded models."""
    try:
        emit(ollama_service().stop())
    except (ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
