# sh41 Local MVP Implementation

## Accepted Scope

Independent project at `/Users/spider/Projects/sh41-local`, distribution
`sh41-local`, executable `sh41`, Apache-2.0. Python 3.12+, Click, Pydantic, PyYAML,
SQLite. macOS/Docker Desktop and Linux/Docker Engine. One container per agent,
tmux attachment, private source copies, persistent identity and native sessions.
OpenCode, Claude Code, Codex; API keys and native account import. External
compatible inference and host Ollama service lifecycle. Stdio and HTTP MCP.

No SaaS edits or runtime dependencies, cloud deployment, web UI, shared semantic
memory, schedules, custom images, additional sandbox providers, or multi-agent
coordination. Preserve the cloud domain model and name/lifecycle conventions,
but explicitly extend manifests for local sources, inference, and MCP.

## Phase 1: Scaffold And Documentation

- Independent packaging, license, README, plan, isolated environment and Git repo.
- Gate: clean install, help/version, import outside the SaaS checkout, unchanged
  SaaS worktree, documentation agrees with the intended interface.
- Status: complete (2026-09-05).

## Phase 2: Specification, CLI And SQLite

- One strict parser for YAML and generated flag manifests; safe paths and secret
  references. SQLite identities/workspaces/deployments/sessions/runs/events,
  migrations, constraints, WAL, bounded contention. User-local Unix socket
  supervisor coordinates agent operations; no container access to the database.
- Gate: flag/YAML equivalence, invalid inputs before side effects, no overwrites,
  credential-free manifests, migration preservation, concurrent identity safety,
  fake-provider lifecycle checks.
- Status: complete (2026-09-05); 16 tests pass, including concurrent reservation,
  socket input ownership, parser security, relative paths and fake-provider cleanup.

## Phase 3: Docker And Persistent Workspaces

- Docker only; restricted non-root containers, persistent private workspace and
  harness home, stable `/workspace/agent`, manager via Docker exec. Preserve source
  working changes without sharing Git objects or following external symlinks.
- Pause stops compute; resume restarts it; park removes compute but retains files;
  redeploy reuses identity and files. Export to a new directory only.
- Gate: two-agent isolation, source unchanged, detach persistence, lifecycle and
  crash recovery, idempotency, targeted cleanup and faithful export.
- Status: pending.

## Phase 4: Harnesses, Authentication And MCP

- Claude/Codex adapters follow cloud runtime conventions; OpenCode uses native
  server events. Pin binaries. One writable attachment or submitted run at a time.
  Persist native session IDs and structured events; no blind replay after crashes.
- Import accounts explicitly; use environment key references. Restricted native
  auth state only, never YAML/SQLite/logs/export. Stdio and Streamable HTTP MCP.
- Gate: real per-harness message/attach/interrupt/session/recreation checks, input
  ownership, both MCP transports, both auth paths, expiry and missing-key errors.
- Status: pending.

## Phase 5: External Inference And Ollama

- OpenCode supports Chat Completions endpoints and managed/reused host Ollama.
  Claude/Codex use native providers. Explicit model, shared serving process,
  serialized pulls, ownership-aware stop, readiness and container reachability.
- Gate: actual edit-and-test tasks remotely and locally; shared inference;
  download reuse; clear missing-binary/network/pull/memory/tool failures; leave
  unrelated Ollama services alone.
- Status: pending.

## Phase 6: Release Gate

- Finish accurate README, tested examples, troubleshooting and CI.
- Gate: full suite/Ruff/wheel install; real harness acceptance; macOS and Linux
  Docker/Ollama tests; actual reboot recovery; SaaS-blocked and offline local
  inference after downloads. Record unrun checks; never equate mocks to live proof.
- Status: pending.

## Validation Record

Phase 1: isolated editable installation passed; help test passed; Ruff passed;
isolated Python import from `/tmp` passed. SaaS worktree remained clean. Docker
Desktop is installed but its daemon was initially unavailable; started the app
for later integration checks. Ollama is not installed yet.

Phase 2: 16 tests and Ruff passed. Docker Desktop now responds (29.6.2).
