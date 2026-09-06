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
per agent, with SQLite operation records and one active job per resource. Only
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

A fixed-width left sidebar consumes these same polls and exposes runtime health,
agent lifecycle/activity, and loaded models, preserving list selection on refresh.
Its Ollama start control submits the existing supervisor-owned background job;
it introduces no process management or additional polling in the UI. Sidebar
selection navigates the main tables without attaching or starting an agent.

The architecture diagram and phase acceptance checklist are in SHELL_PLAN.md.
The shell copies Shelter41's dark-theme color tokens locally and uses a persistent
Shelter41 header; typography is limited to terminal-supported weight and emphasis,
with font family and size owned by the terminal emulator, not the application.

The wizard completes source paths with Textual's inline suggester using read-only
directory scans off the UI thread. It preserves relative and home-directory
prefixes and suggests directories only. A directory-only Textual tree provides
filesystem browsing with root/home/parent navigation and asynchronous path checks;
browsing does not reserve or materialize an agent directory. Its Ollama selector follows the existing
passive model snapshots, preserving explicit/custom choices on refresh; no model
is started or downloaded by choosing it. Remote/native models remain free text.
Wizard model snapshots retain server health and address rather than reducing
unavailable inventories to an unexplained empty list. Explicit start/pull actions
use the existing supervisor jobs. CLI model listing uses the same passive discovery
and reports unreachable servers as errors, including externally managed endpoints
that have no saved ownership record.

Directory inspection is a read-only supervisor operation backed by Git discovery
and the local binding registry. The wizard debounces inspection, discards stale
replies and requires a non-Git access choice. YAML adds source.mode
(worktree/direct/copy); omission retains legacy copy semantics. CLI authoring
detects repositories and requires explicit non-Git choices outside interactive
terminals. Save Only performs no directory materialization.

SQLite v3 adds immutable per-identity bindings: source, working path, mode,
repository/common directory, branch, starting commit and preparation state.
Migration backfills existing private copies in place. Normalized-spec comparisons
preserve idempotent deployment across the additive default field. Direct-folder
sharing is checked under the reservation transaction, including parent/child and
filesystem aliases. Acknowledgements are launch-only, not persisted in YAML;
new conflicts require renewed confirmation unless automation explicitly allows
shared writes. Associations include paused/parked bindings and are local-state scoped.

DockerProvider builds pinned harness images and creates one restricted container
per deployment. Working files always appear at /workspace/agent; private harness
home and control/journals retain their existing mounts. Worktree mode creates
agent/<name> from committed HEAD under state/worktrees/<name>, mounting the Git
common directory at its host absolute path so linked-worktree pointers resolve
inside Docker. Original checkout files are not mounted, but repository metadata,
refs, configuration and history are shared and writable. Git author settings use
container environment variables, never shared config writes. Host setup disables
hooks/fsmonitor and rejects configured checkout filters and submodule layouts.

Direct mode mounts the selected non-Git folder read-write with no initialization
or permission changes. Copy mode retains exclusions and private snapshot behavior,
without initializing Git for new non-Git copies. Worktree creation uses a per-repo
file lock and durable preparation states. Collisions and ambiguous interrupted
checkouts fail without resetting files; clean completed checkouts can be recovered.
Missing/moved ready worktrees require repair. Parking deletes only compute;
redeploy/resume reuse and validate bindings. Source/mode changes require another
identity. Workspace groups remain metadata, not shared filesystems.

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
