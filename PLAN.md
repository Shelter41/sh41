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
- Status: Docker/filesystem implementation complete; 19 unit tests and one real
  two-container lifecycle test pass on macOS. Terminal/manager recovery is checked
  with the harness implementation in Phase 4.

## Phase 4: Harnesses, Authentication And MCP

- Claude/Codex adapters follow cloud runtime conventions; OpenCode uses native
  server message records. Pin binaries. One writable attachment or submitted run at a time.
  Persist native session IDs and structured events; no blind replay after crashes.
- Import accounts explicitly; use environment key references. Keep imported
  auth outside the workspace and YAML/SQLite; redact known secrets from normalized
  events. Native transcripts remain sensitive. Stdio and Streamable HTTP MCP.
- Gate: real per-harness message/attach/interrupt/session/recreation checks, input
  ownership, both MCP transports, both auth paths, expiry and missing-key errors.
- Status: implemented. Native account recovery and conversation switching pass
  for Claude/Codex; real OpenCode protocol, all three harnesses' stdio/HTTP MCP,
  PTY attach/detach/input ownership/interrupt and container-restart recovery pass
  on macOS. Direct-TUI-only conversation recovery also passes for Claude/Codex.
  Real API-key account acceptance remains a release check; key translation,
  redaction and error paths have unit coverage.

## Phase 5: External Inference And Ollama

- OpenCode supports Chat Completions endpoints and managed/reused host Ollama.
  Claude/Codex use native providers. Explicit model, shared serving process,
  serialized pulls, ownership-aware stop, readiness and container reachability.
- Gate: actual edit-and-test tasks remotely and locally; shared inference;
  download reuse; clear missing-binary/network/pull/memory/tool failures; leave
  unrelated Ollama services alone.
- Status: implemented. Managed Ollama startup, downloaded model cache, compatible
  endpoint execution and container reachability verified on macOS. Ownership,
  serialized pulls, reuse and failure paths have unit coverage. Actual local
  edit/test acceptance passes with qwen3:4b-instruct, including a second task on
  a network-isolated agent with only a local model relay. Linux host validation
  and a third-party remote open-weight endpoint remain outstanding.

## Phase 6: Release Gate

- Finish accurate README, tested examples, troubleshooting and CI.
- Gate: full suite/Ruff/wheel install; real harness acceptance; macOS and Linux
  Docker/Ollama tests; actual reboot recovery; SaaS-blocked and offline local
  inference after downloads. Record unrun checks; never equate mocks to live proof.
- Status: in progress. README, examples, architecture/decisions, testing guide
  and macOS/Linux CI configuration added. Wheel install plus actual CLI deployment
  and a turn outside the checkout pass. CI has not run remotely. Actual Linux
  host, machine reboot and live API-key authentication are not yet verified.

## Validation Record

Phase 1: isolated editable installation passed; help test passed; Ruff passed;
isolated Python import from `/tmp` passed. SaaS worktree remained clean. Docker
Desktop is installed but its daemon was initially unavailable; started the app
for later integration checks. Ollama was not installed at that phase.

Phase 2: 16 tests and Ruff passed. Docker Desktop now responds (29.6.2).

Phase 3: 19 non-Docker tests and real Docker isolation/pause/resume/park/recreate/
export test passed. Docker's desktop credential helper initially blocked public
pulls; acceptance used a temporary credential-free Docker configuration and the
same local daemon, without changing the user's Docker configuration.

Phase 4: 39 unit tests and 3 uncredentialed Docker tests pass (42 total). Native
Codex and Claude account tests pass across park/recreate, new conversation and
continue-conversation. All three actual harnesses invoked both MCP transports.
Real PTYs verified writable-client exclusion, read-only observation, detach,
interrupt and interrupted-run recovery after container restart. Fixed partial
UTF-8 JSONL handling, exact native transcript selection, and prompt provider
refusals being mistaken for idle turns. Provider refusal remains a failed run.

Phase 5: installed official Ollama 0.33.3 locally; downloaded qwen3:0.6b and
qwen3:4b-instruct. The 0.6B model served actual local inference but failed the
coding acceptance by describing rather than applying the edit. This is recorded
as a failed model-task test, not a passing implementation gate. The 4B model passed
actual editing and Python tests. A second run on an internal Docker network passed
with public internet unreachable and only a fixed local-model relay accessible;
the native conversation ID survived recreation. This checks agent runtime offline
behavior, not a machine-wide air-gap policy. An initial download stalled repeatedly;
retry reused its cache. Test model weights are retained in the gitignored
`.context/local-acceptance/inference/models` directory.

Phase 6: wheel and sdist build, four example manifests, Ruff, isolated wheel
installation, generated YAML, real CLI deploy/run/history/park and supervisor
cleanup outside the checkout pass. Native Linux and actual reboot need a
disposable Linux machine/VM; live API-key checks need keys not available in the
current shell. No user machine reboot or host firewall change was attempted.
These outstanding gates prevent declaring the release fully validated.

Combined acceptance on 2026-09-06: 51 passed and one intentionally skipped wheel
test; the wheel end-to-end test passed separately. Two subsequent regression tests
bring the unit suite to 42 passing tests (lost submission reply and Git symlink
replacement). Ruff passed. The host's pre-existing Python installation under
`/tmp` lost standard-library files during final packaging, so the project
environments were rebuilt against durable user-local Python 3.12.14. The global
Python symlink and SaaS environment were not changed.

After rebuilding the environment: 45 unit/Docker tests passed (the opt-in wheel
test skipped; 8 live tests deselected), Ruff passed, and wheel/sdist builds passed.
The installed-wheel end-to-end test then passed separately on the rebuilt Python.
The model server was stopped and only its cached weights retained; all test-owned
agent containers were removed. Unrelated Docker services were left running.
