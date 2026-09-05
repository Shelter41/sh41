# Architecture

The CLI parses one strict versioned manifest, resolves local paths and secret
references, and calls a user-local supervisor over an owner-only Unix socket.
The supervisor holds per-agent operation locks and owns SQLite metadata. Database
constraints enforce one active deployment, conversation and submitted run per
identity. No HTTP API, SaaS authentication, Redis or external database is needed.

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
journals interrupted. Native conversation files and IDs survive recreation. Direct
terminal turns stay in the harness history rather than acquiring synthetic sh41
run records. OpenCode's message API returns tool/result events after completion;
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
