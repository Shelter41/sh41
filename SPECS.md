# Architecture

The CLI parses one strict versioned manifest, resolves local paths and secret
references, and calls a user-local supervisor over an owner-only Unix socket.
The supervisor holds per-agent operation locks and owns SQLite metadata. Database
constraints enforce one active deployment, conversation and submitted run per
identity. No HTTP API, SaaS authentication, Redis or external database is needed.

Interactive `sh41` and `sh41 shell` run a Textual client of that same supervisor.
The wizard and CLI flags share manifest construction, validation and exclusive
YAML writes. Native terminal attachment suspends Textual and hands the terminal
to Docker exec/tmux; detaching restores the dashboard. The UI owns no agent
processes. A new worker Start RPC creates/reuses detached native execution without
submitting a turn or reserving a writer. Existing CLI deployment remains lazy.

The control plane accepts immutable-ID background jobs and serializes mutations
per agent, with SQLite v2 operation records and one active job per resource. Only
kind, resource, timestamps, status and redacted progress are persisted, never job
payloads or credentials. Accepted jobs run in supervisor threads after the client
disconnects. Supervisor startup marks unfinished jobs interrupted and retains the
existing incomplete-provisioning cleanup; it never blindly replays work.

Dashboard observations are independent from lifecycle reconciliation: bounded
Docker/worker/Ollama probes populate a timestamped memory cache using four probe
workers. UI polls do not overlap; unavailable probes produce unknown/stale states
without changing SQLite lifecycle records. Each agent shows recorded and observed
state separately. Ollama GET version/tags/ps probes discover the selected service
without starting it, adopting ownership or pulling a model. Downloaded weights,
loaded memory and configured agent model references are distinct concepts.

The architecture diagram and phase acceptance checklist are in SHELL_PLAN.md.

DockerProvider builds pinned harness images and creates one restricted container
per deployment. Each identity has three private host directories mounted into its
container: workspace, native home and manager control/journals. Host source paths
are copied, never mounted. Workspace groups are metadata, not shared filesystems.
Parking deletes only compute; redeployment reuses the three directories.

Docker exec RPC reaches the container manager's Unix socket. This avoids published
agent ports and backend callbacks. The manager owns tmux and native process/session
state. Claude/Codex reuse adapted cloud Runtime/driver conventions; OpenCode uses
its loopback-only native server and a tmux-attached TUI. One writable attachment
or submitted run owns input at a time. A read-only observer cannot submit turns.

Submitted turns have immutable IDs and fsynced event/status journals. The supervisor
imports normalized events idempotently into SQLite. A lost client reply does not
cause a replay; status is reconciled by ID. A restarted manager marks unfinished
journals interrupted. Native conversation files and IDs survive recreation.
Empty native completion markers without a response or tool activity are not
reported as successful; authentication diagnostics distinguish failed requests
from uncertain completion. Direct terminal turns stay in the harness history
rather than acquiring synthetic sh41 run records. OpenCode's message API returns tool/result events after completion;
Claude/Codex JSONL is polled incrementally.

Common MCP definitions translate to each harness's native config. Imported account
profiles and environment-referenced API keys are explicitly materialized in private
agent state. They never belong in manifests or SQLite. Redaction is best-effort;
the agent itself has access to its credentials and arbitrary network egress.

OpenCode accepts compatible Chat Completions endpoints or a host Ollama service.
Ollama lifecycle and downloads are serialized with a local file lock. Ownership
records include PID and process start signature; externally managed processes are
never stopped. On macOS the container reaches the host through Docker Desktop;
Linux-managed inference binds a dedicated Docker bridge gateway. Model weights
are shared cache, not baked into agent images.

This project has no imports, snapshot transport, callbacks or runtime dependency
on Shelter41. Cloud-compatible concepts do not imply interchangeable manifests or
conversation formats. Read PLAN.md for acceptance status and deliberate limits.
