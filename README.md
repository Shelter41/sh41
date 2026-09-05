# sh41 Local

Run persistent coding agents locally with your choice of harness and model.

An independent, open-source CLI for deploying named agents from YAML or command-line
flags. The MVP targets OpenCode, Claude Code, and Codex, with private Docker
workspaces, durable conversations, compatible model endpoints, and local Ollama.
No Shelter41 account or SaaS backend is required.

**Status:** implementation in progress. Manifest generation (`launch --write-only`),
validation, local metadata listing and supervisor coordination are implemented.
Agent execution is next. [PLAN.md](PLAN.md) records the implementation phases and
their acceptance gates; examples below describe the intended MVP interface.

## Install From Source

Requires Python 3.12+. Docker Desktop (macOS) or Docker Engine (Linux) is needed for
agent execution. Install Ollama separately to use locally served models.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/sh41 --help
```

Use the project virtual environment to avoid replacing an existing cloud `sh41`.

## Intended Quickstart

```sh
sh41 launch reviewer --harness opencode --source . --ollama --model MODEL
sh41 agents
sh41 attach reviewer
sh41 run reviewer --message "Review this project and run its tests"
sh41 pause reviewer
sh41 resume reviewer
sh41 export reviewer --output ./reviewer-work
```

`launch` writes `reviewer.yaml` before deploying. `--write-only` generates the file
without starting anything; `sh41 deploy reviewer.yaml` uses the same specification.
An agent's private copy is independent of the original project.

## Cloud Compatibility

The domain model remains Workspace -> Agent identity -> Deployment, with sessions
owned by identities and runs owned by sessions. Agent names, harness slugs,
`launch`/`deploy`, and `deployed`/`paused`/`parked` semantics follow Shelter41.

Local execution introduces SQLite, Docker, local source paths, and inference/MCP
configuration. Cloud manifests are not directly interchangeable. There is no SaaS
dependency, cloud synchronization, or cloud deployment command.

## Persistence And Credentials

Metadata will live in SQLite under `~/.local/share/sh41-local`; set
`SH41_LOCAL_HOME` to isolate installations. Working files and native harness state
will live outside disposable container layers. Pausing or parking will retain
them. Native credentials are explicitly imported; API keys use environment
references. YAML and exports must not contain imported credentials.

## Isolation

One Docker container per agent, running as a non-root user without a host Docker
socket or home-directory mount. Containers are not a dedicated-VM security
boundary. Agents can modify their private workspace and use permitted network
connections. Local inference still needs suitable hardware; remote inference
sends prompts and context to the configured provider.

## Development

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m build
```

Live harness tests require account credentials or a running model service. Never
commit credentials or generated runtime state. See [PLAN.md](PLAN.md) for exact
completion criteria and the current validation record.

## License

Apache-2.0. Harnesses, model servers, and model weights retain their own licenses.
