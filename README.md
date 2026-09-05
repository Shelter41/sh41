# sh41 Local

Persistent local coding agents. Your harness, your model, your machine.

Deploy an agent from YAML or flags, attach to its native terminal, and keep its
workspace and conversations across container recreation. No Shelter41 account,
SaaS backend, Postgres, Redis, or API server is required.

**Status: pre-release MVP.** Implemented, with unit and real Docker/harness tests.
See [PLAN.md](PLAN.md) for verified checks and outstanding release gates. This is
an independent project, not a local mode of the cloud CLI.

## Install

Requires Python 3.12+, Git, and running Docker Desktop on macOS or Docker Engine
on Linux. Local inference additionally needs [Ollama](https://ollama.com/download)
and enough memory for the chosen model. No host tmux installation is needed.

From this checkout:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e .
source .venv/bin/activate
sh41 doctor
```

Use this virtual environment to avoid replacing an existing cloud `sh41` binary.
The distribution is named `sh41-local`; it is not published to PyPI yet. The first
deployment builds a pinned harness image and requires internet access.

## Quickstart

Already use Codex? Explicitly import its native login, then launch:

```sh
sh41 codex-auth
sh41 launch reviewer --harness codex --source /absolute/path/to/project
sh41 run reviewer --message "Review this project and run its tests"
sh41 attach reviewer
```

Detach with **Ctrl-b, then d**. Detaching leaves the agent running. An active
submitted run or another writable terminal makes subsequent attachments read-only.
`sh41 interrupt reviewer` interrupts a turn; it does not undo file edits.

For Claude Code, use `sh41 claude-auth` and `--harness claude-code`. These commands
import an existing host login; they do not log into Shelter41. On macOS, Claude
credentials are read from its Keychain entry. `--file PATH` explicitly imports a
native credential JSON file on either platform. Refresh the host login and import
again when it expires. Account access remains subject to the harness provider.

For API-key authentication, skip import and reference an existing environment
variable, for example `--api-key-env OPENAI_API_KEY` with Codex or
`--api-key-env ANTHROPIC_API_KEY` with Claude. Secret values are never CLI flags.

## Local Or Compatible Models

OpenCode is the MVP harness for open-weight models:

```sh
sh41 models pull qwen3:4b-instruct
sh41 launch local-reviewer --harness opencode --source /path/to/project \
  --ollama --model qwen3:4b-instruct
sh41 run local-reviewer --message "Review the code and run its tests"
sh41 models ls
```

The model name is explicit, not a promise of coding quality. A model advertising
tool support can still fail to use tools correctly. Larger models need more RAM;
inference runs on the host, separate from the agent's 4 GiB container limit.
The managed Ollama server serializes inference and shares one model cache across
agents. Pulls are serialized and reused; interrupted downloads retain cached parts.

Use any reachable compatible server, including one you run with another serving
tool, without making sh41 manage that server:

```sh
sh41 launch remote-reviewer --harness opencode --source /path/to/project \
  --base-url https://inference.example.com/v1 --model your-model-id \
  --api-key-env MODEL_API_KEY
```

This adapter requires **Chat Completions, tool calling, and `/v1/models`**. It does
not translate the Responses API. Omit `--api-key-env` for an unauthenticated local
endpoint. The URL must be reachable **from the container**: `localhost` there is
not your host. On macOS use `host.docker.internal` for a manually served host model.

sh41 reuses a healthy Ollama at `OLLAMA_HOST`, or the usual host port 11434, before
starting its own. On Linux an existing service must bind a Docker-reachable
interface; a managed service binds only the dedicated Docker bridge gateway.
sh41 never stops an externally managed server. Pause or park its agents before
`sh41 models stop`; downloaded models remain.

## YAML And Flags

`launch` writes `<agent>.yaml`, then deploys it. `--write-only` only generates the
file. Existing files are never overwritten; `--output PATH` chooses another path.
Authored YAML and flags use the same strict parser.

```yaml
version: 1
agent: reviewer
harness: opencode
workspace: development
source:
  provider: local
  path: ./project
model: qwen3:4b-instruct
inference:
  provider: ollama
sandbox:
  provider: docker
instructions: ./instructions.md
mcp:
  local-tools:
    transport: stdio
    command: [python, /workspace/agent/tools/server.py]
    env:
      SERVICE_TOKEN:
        env: SERVICE_TOKEN
  remote-tools:
    transport: http
    url: https://tools.example.com/mcp
    headers:
      Authorization:
        env: MCP_AUTHORIZATION
```

```sh
sh41 deploy reviewer.yaml
sh41 launch reviewer --harness opencode --source ./project --ollama \
  --model qwen3:4b-instruct --workspace development --instructions instructions.md \
  --mcp local-tools '{"transport":"stdio","command":["python","/workspace/agent/tools/server.py"],"env":{"SERVICE_TOKEN":{"env":"SERVICE_TOKEN"}}}' \
  --write-only --output generated.yaml
```

Source and instruction paths are relative to the YAML file. MCP commands execute
**inside** the agent container. HTTP MCP is Streamable HTTP, not legacy SSE, and
OAuth is out of scope. Header references supply the entire value, including any
`Bearer ` prefix. Keep tokens out of command arguments and instructions.
See [examples/](examples/) for minimal manifests. Without `source`, an agent gets
an empty Git workspace. Workspace names group agents; they do not share files.

## Manage Agents

| Command | Behavior |
| --- | --- |
| `sh41 agents` / `sh41 ls` | List durable identities and reconciled compute state |
| `sh41 run NAME --message TEXT` / `chat` | Submit one turn; `--json` emits structured events |
| `sh41 attach NAME [--read-only]` | Native harness TUI inside tmux |
| `sh41 pause NAME` / `resume NAME` | Stop/start compute without deleting files |
| `sh41 park NAME` / `redeploy NAME` | Remove/recreate compute, retaining identity and files |
| `sh41 sessions NAME` | List conversations belonging to the identity |
| `sh41 new-session NAME` | Select a fresh conversation, keeping previous ones |
| `sh41 continue-session NAME ID` | Select an earlier conversation |
| `sh41 history NAME` | List turns submitted through sh41 |
| `sh41 export NAME --output ./new-directory` | Copy working files out; pause or park first |

Run and attach automatically resume a paused agent. Parked agents need redeploy.
Only one active deployment, conversation, and submitted run exist per identity.
Changing harness requires a new identity: native conversation formats are not
interchangeable. To change other manifest settings, park, edit YAML, and deploy.
Changing `source` does not replace an identity's existing working files.

The original source is never an agent mount. sh41 copies tracked and nonignored
untracked working files, preserves dirty changes, and creates `agent/<name>`.
Git objects are independent and the origin remote is removed. `.env*`, caches,
and external/absolute symlinks are excluded or rejected. **Git history is copied**;
secrets committed in history remain accessible. Submodule materialization and
automatic dependency installation are not supported. Export omits Git metadata,
credentials stored outside the workspace, and the excluded paths.

## Persistence And Boundaries

State lives under `~/.local/share/sh41-local`; `SH41_LOCAL_HOME` selects another
short, private directory. SQLite stores metadata and normalized run events. Each
identity has separate working files, native harness home, and manager journals.
No container can access the host SQLite database or another agent's directories.

Native conversations survive pause, park, and container recreation. Direct TUI
turns remain in native harness history; they are not mirrored as separate sh41
run records. OpenCode tool/result events are imported when its turn finishes;
Claude/Codex transcript events are polled during the turn. Runs interrupted by a
manager crash are never silently replayed. The supervisor restarts on demand.
After an upgrade, stop the old local supervisor before using the new code; running
containers retain their version until parked and redeployed.

Credentials are explicitly copied into owner-only local files, not encrypted at
rest. Imported profiles and referenced keys are available to the agent process.
Known secrets are redacted from normalized events on a best-effort basis; native
transcripts, prompts, arbitrary tool output, and workspace files can contain
sensitive information. Review exports before sharing them. Removing an imported
profile alone does not revoke credentials already copied to an agent.

Docker is the only sandbox: non-root, all capabilities dropped, no privilege
escalation, no Docker socket or host home mount, 2 CPUs, 4 GiB RAM, 256 processes.
Harness tool approvals are disabled **inside this boundary** for unattended work.
Containers share the host kernel on Linux; this is not hostile multi-tenant VM
isolation. Network egress and host-network services remain reachable. Only grant
credentials and source access appropriate for the task.

No SaaS calls or telemetry are implemented by sh41. Third-party harnesses and
tools have their own network behavior. OpenCode is restricted to the configured
provider, with auto-update, sharing, model-catalog fetch, default plugins, and LSP
downloads disabled. First-use image/provider packages and model downloads need
network access. Fully offline operation after warming caches remains a release
acceptance check, not an air-gap security guarantee.

## Cloud Compatibility

The identity/deployment/conversation/run separation and Claude/Codex Runtime and
tmux conventions follow Shelter41; adapted files are listed in
[PROVENANCE.md](PROVENANCE.md). This preserves implementation concepts, **not YAML
wire compatibility**. The local manifest deliberately introduces source paths,
inference and MCP configuration. Local `sessions` means conversations, while the
cloud CLI calls deployments sessions. Local names are agent identities rather
than template-created cloud session names.

SQLite replaces Postgres; Docker replaces E2B; native files replace cloud snapshot
uploads. No orgs, hosted workspaces, GitHub authentication, callbacks, queues,
plugins, billing, or cloud sync are imported. A future managed service can host
these concepts without becoming a prerequisite for local execution.

## Development And Troubleshooting

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python -m build
SH41_TEST_DOCKER=1 .venv/bin/pytest -q -m 'docker and not live'
```

Live tests are opt-in and may use account quotas. See [TESTING.md](TESTING.md) for
the matrix, local-model tests, and recovery checks. CI runs unit checks on macOS
and Linux and uncredentialed Docker integration on Linux.

- Docker unavailable: start Docker, then `sh41 doctor`. Image-build failures are
  recorded in `image-build.log` beneath the state directory.
- Missing/expired auth: refresh the native host login, re-import, and retry an idle
  agent. API-key references must be set in the invoking shell.
- Unreachable model: check `/v1/models` from the sandbox, not only from the host.
- Slow/failed local model: inspect `inference/ollama.log`, check available RAM,
  select a suitable tool-capable model, and retry interrupted pulls explicitly.
- Busy agent: detach its writable terminal or interrupt its submitted turn first.
- Supervisor disconnected: inspect `agents` and `history` before retrying; the
  submission may already have been accepted.

## License

Apache-2.0. Harnesses, model servers, and model weights retain their own licenses.
No proprietary harness binaries or account credentials are committed here.
