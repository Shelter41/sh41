# sh41 Local

Persistent local coding agents. Your harness, your model, your machine.

Deploy an agent from YAML or flags, attach to its native terminal, and keep its
workspace and conversations across container recreation. No Shelter41 account,
SaaS backend, Postgres, Redis, or API server is required.

**Status: pre-release MVP.** Implemented, with unit and real Docker/harness tests.
See [PLAN.md](PLAN.md) and [SHELL_PLAN.md](SHELL_PLAN.md) for implementation and acceptance records. This is
an independent project, not a local mode of the cloud CLI.
The subsequent [coding-directory plan](CODING_DIRECTORY_PLAN.md) records worktree,
direct-folder and sharing acceptance.

## Install

Requires Python 3.12+, Git, and running Docker Desktop on macOS. Linux/Docker
Engine support is experimental and has not been host-validated. Local inference additionally needs [Ollama](https://ollama.com/download)
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

Open the local control panel:

```sh
sh41
# Explicit equivalent:
sh41 shell
```

The **Agents** view lists lifecycle, live activity, harness, model and workspace.
Select an agent and attach to its native terminal, or use the action menu for
Start, pause/resume, park/redeploy, conversations, run history and export. New
opens a guided wizard; Import YAML deploys an existing manifest after confirmation.
The wizard writes the same schema as CLI flags, with Save Only or Save and Start.
Source directories autocomplete as you type; press Right at the end to accept a
suggestion. Absolute, relative, and `~/` paths are supported, including spaces.
Browse opens a directory tree with Root, Home, and Up navigation, hidden folders,
and an editable path. Browsing is not restricted to the starting folder; selection
still obeys source safety checks and your operating system's access permissions.
Selecting a Git checkout proposes a new worktree and `agent/<agent-name>` branch
from its committed `HEAD`; uncommitted changes stay in the original checkout.
Selecting a non-Git folder requires Original folder (read-write) or Private copy.
Other agents using the repository/folder appear with their mode and recorded
status. Shared original-folder edits require confirmation, including overlapping
parent/child folders and paused/parked agents that can later resume.
The Ollama model dropdown combines downloaded models with families from the live
[Ollama tools library](https://ollama.com/search?c=tools), labeled separately.
Search the library or use More for further results, then choose a local variant
from its dropdown, including the published download size (not required RAM).
Cloud-only variants are excluded. Choose Custom model to enter another tag.
Compatible endpoints and native providers retain manual model entry.
The wizard shows the selected Ollama address and distinguishes an unreachable
server, an unavailable inventory, and a server with no downloads. Start / Reuse
and Pull model are available there; opening the wizard never starts a server or
downloads weights automatically. Library browsing stays inside the terminal and
does not require a running Ollama server, but needs internet access to ollama.com.
If the library is unavailable, downloaded models and custom entry remain usable.
Pull model explicitly downloads the chosen tag; Save and Start also downloads
missing weights during deployment. Save Only writes YAML without downloading.
It never overwrites a file, and saved YAML remains available if deployment fails.

Each started agent has its own detached tmux session **inside its Docker sandbox**.
Detach with **Ctrl-b, then d** to return to the dashboard; **Ctrl-q** closes the
dashboard without stopping agents or accepted background operations. Native
terminals remain the conversation interface, not an embedded shell chat renderer.
Start is idempotent and does not send a prompt. Shell Resume/Redeploy and
conversation changes also start the selected native terminal; the existing CLI
commands retain their original lifecycle behavior, with `sh41 start NAME` added.

The persistent left sidebar shows supervisor, Docker and Ollama health, agent
states, and currently loaded models with memory usage. Select an agent or model
to open its view; **Start Ollama** starts/reuses the selected local service through
the supervisor. Stale observations are marked rather than shown as running.

The **Models** view distinguishes downloaded weights from loaded models, reports
Ollama ownership and reachability, and offers Start/Reuse, Pull and guarded Stop.
Model rows list agent configuration references, not exclusive GPU ownership.
Simply opening the shell never starts Ollama or downloads weights. The
**Operations** view retains background progress and failures across shell exits;
interrupted supervisor operations are not automatically retried.

Both shell entrypoints require an interactive terminal. Bare `sh41` in a pipe
prints help; all existing commands remain scriptable. Secret references use the
shell process's environment; native account import is always explicit. A `*` on
an agent state means the observation is stale. Unknown is not proof of stopped
compute; the detail pane separately shows the recorded lifecycle and timestamp.
Use an 80-column, 24-line terminal or larger. Long tables scroll horizontally.
The shell uses Shelter41's dark palette: near-black surfaces, warm white text and
amber controls. Font family and size come from your terminal emulator; a TUI cannot
load the web app's Inter font. SF Mono matches the web console's code-font stack
where available. The shell does not change your terminal profile or native harness themes.
The supervisor inherits `OLLAMA_HOST` when first started; set it before your first
command for a custom server. Existing model-server ownership guards still apply.

### Command-line workflow

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
sh41 models status --json
sh41 models ps --json
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
`sh41 models start` starts or reuses the server without downloading a model.
`models ps` reports loaded models, not active generation requests. Status probes
never start a server and report unavailable inventory distinctly from an empty list.

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
  mode: worktree
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

### Directory Modes

| `source.mode` | Behavior |
| --- | --- |
| `worktree` | A dedicated Git worktree and branch from the selected checkout's committed HEAD; selecting a subdirectory uses the repository root |
| `direct` | Edit the original non-Git folder in place, including hidden files; no Git initialization |
| `copy` | Private copy with exclusions; non-Git sources stay non-Git |

The wizard and `launch --source <repo>` choose worktree mode for repositories.
For a non-Git folder, interactive CLI launches prompt; scripts must specify
`--source-mode copy` or `--source-mode direct`. `--source-mode worktree` is also
available explicitly. YAML without `source.mode` retains legacy copy semantics.

```sh
sh41 launch editor --harness codex --source ~/Projects/app
sh41 launch files --harness codex --source ~/Documents/task --source-mode copy
sh41 launch shared --harness codex --source ~/Documents/task --source-mode direct --allow-shared-folder
sh41 deploy shared.yaml --allow-shared-folder
```

`--allow-shared-folder` explicitly acknowledges shared writes for automation.
Interactive confirmation acknowledges the listed agents; launch reservation
rechecks sharing and rejects newly discovered conflicts. A failed shell job can
be retried by importing its saved YAML and confirming the updated agent list.
Detection covers this `SH41_LOCAL_HOME`, not arbitrary editors or other tools.
Save Only / `--write-only` never creates a branch, worktree, copy, or container.

Worktrees live in `$SH41_LOCAL_HOME/worktrees/<agent-name>` (by default
`~/.local/share/sh41-local/worktrees/<agent-name>`). Existing branches or unowned
destinations are never overwritten. Repositories need a commit; bare repositories,
submodules, and configured host checkout filters are currently unsupported.

**Worktrees share Git metadata, refs, configuration, history, and remotes with the
original repository. They are not a repository security boundary.** Tracked
secrets remain available, and agents can change shared Git metadata. Only use
repositories you trust with these agents. Direct mode grants read-write access
to every file inside the selected folder, including `.env` and other hidden files.

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
An identity's source path and mode are immutable; choose a new agent name to
change either. Existing agents keep their private working copies after upgrade.

In copy mode, the original source is never an agent mount. sh41 copies tracked and nonignored
untracked working files, preserves dirty changes, and creates `agent/<name>`.
Git objects are independent and the origin remote is removed. `.env*`, caches,
and external/absolute symlinks are excluded or rejected. **Git history is copied**;
secrets committed in history remain accessible. Submodule materialization and
automatic dependency installation are not supported. Export omits Git metadata,
credentials stored outside the workspace, and the excluded paths. Worktree mode
instead checks out committed tracked files without copy exclusions. Direct-mode
files already live on the host, so `export` is not applicable.

## Persistence And Boundaries

State lives under `~/.local/share/sh41-local`; `SH41_LOCAL_HOME` selects another
short, private directory. SQLite stores metadata and normalized run events. Each
identity retains a working-directory binding, private native harness home, and
manager journals. Containers cannot access the host SQLite database or another
agent's private home/journals. Direct-folder agents can share working files;
worktree agents share repository metadata but have separate working files.
Pause, park, and redeploy retain these bindings and do not delete branches or
worktrees. Missing/moved worktrees require repair, never automatic replacement.
Interrupted setup resumes only when nothing was created or the registered
checkout is complete and clean; ambiguous files are preserved with a repair error.

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
network access. A warmed agent passed an edit/test task on an internal Docker
network with access only to a local model relay. This is not a machine-wide
air-gap security guarantee or an optional production network policy.

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
the matrix, local-model tests, API-key/remote-endpoint gates, and recovery checks. CI runs unit checks on macOS
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
