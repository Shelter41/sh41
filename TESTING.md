# Acceptance Tests

Default tests need Python and Git only. Real tests create and remove only their
own labeled containers and temporary directories; they never prune Docker state.

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python -m build
SH41_TEST_DOCKER=1 .venv/bin/pytest -q -m 'docker and not live'
SH41_TEST_NATIVE=1 .venv/bin/pytest -q tests/test_live.py
SH41_TEST_MCP=1 .venv/bin/pytest -q tests/test_mcp_live.py
SH41_TEST_OLLAMA=1 .venv/bin/pytest -q tests/test_local_model.py
SH41_TEST_OLLAMA=1 SH41_TEST_OFFLINE=1 .venv/bin/pytest -q tests/test_local_model.py
SH41_TEST_API_KEYS=1 .venv/bin/pytest -q tests/test_external_acceptance.py -k api_key
SH41_TEST_REMOTE=1 .venv/bin/pytest -q tests/test_external_acceptance.py -k remote
SH41_TEST_KEY_REJECTION=1 .venv/bin/pytest -q tests/test_external_acceptance.py -k rejected_clearly
```

Native and MCP tests explicitly import existing Claude/Codex logins and consume
model usage. Do not enable them in untrusted pull requests. The OpenCode protocol
fixture uses a controlled HTTP server, not a real model. Terminal acceptance
opens real PTYs, checks detach/read-only/writer exclusion/interrupt, restarts the
container during a turn, and checks that it is not replayed.

On Linux set `SH41_TEST_BIND=0.0.0.0` for HTTP fixtures; the test host must not be
publicly exposed. On macOS the fixtures bind loopback. `SH41_TEST_HOST` overrides
the container-visible fixture hostname (default `host.docker.internal`).

The Ollama test downloads `qwen3:4b-instruct` by default, edits a deliberately broken
Python function and verifies its tests. `SH41_TEST_MODEL` selects another model;
`SH41_TEST_OLLAMA_URL` reuses a host server. It requires actual tool execution,
not a text response claiming success. Downloads and inference may need several
GiB of disk/RAM and many minutes. Small models are not presumed capable.

The offline variant warms the image and native provider cache, then recreates the
agent on a test-only internal Docker network. A fixed-upstream relay exposes only
the local model API; a public-internet request must fail before the second coding
task passes. This changes only test containers/networks, not host firewall rules.

To check a built wheel end to end, install it into a separate virtual environment
and set `SH41_TEST_WHEEL_PYTHON` to that environment's absolute Python path, then
run `pytest -q tests/test_wheel.py`. This executes the installed CLI and supervisor
outside the checkout, including deployment, a real harness turn, history and park.

API-key acceptance requires `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in the local
environment. Use `-k codex` or `-k claude` to select just one harness. Optional
`SH41_TEST_CODEX_MODEL` and `SH41_TEST_CLAUDE_MODEL` select models allowed by those
accounts. The tests use fresh state with no native account imports, then check a
valid key, a deliberately invalid test key, and recovery with the valid key. They
do not revoke or modify your real provider credentials. Actual provider-side token
expiry remains a separate manual check.

Remote-model acceptance requires `SH41_TEST_REMOTE_URL` (container-reachable
Chat Completions base URL) and `SH41_TEST_REMOTE_MODEL`. Choose an actual remote
open-weight model for the release gate. Set `SH41_TEST_REMOTE_KEY_ENV` to the name
of its key variable when authentication is required. This test requires a real
file edit and a completed bash unittest invocation, then independently reruns the
tests in the sandbox and checks the original source is unchanged. These tests
consume provider usage; enable them deliberately. Missing required variables fail
an explicitly enabled gate rather than silently skipping it. Do not use pytest's
`--showlocals` with credentials, and do not paste keys into commands or reports.
The separate key-rejection gate contacts the native providers with a deliberately
invalid key. It needs network access but no valid credentials or paid inference.

## Manual Release Gates

These checks are not replaced by mocked process tests:

1. Run the Docker and actual Ollama suites on a native Linux host and macOS.
2. Warm an OpenCode agent's image, provider packages and model. Block outbound
   internet for the test agent while retaining access to its host model server;
   run an edit/test task, detach/reattach, and resume the same conversation.
   Do not modify firewall rules affecting unrelated agents or the user's machine.
3. Use a disposable machine/VM for actual reboot recovery. Create an agent,
   record a conversation detail and file, reboot, start Docker if needed, run
   `sh41 agents`, then continue the conversation. Verify identity/native ID and
   files persist. An in-flight turn must report interrupted, never auto-replay.
4. Exercise Claude/Codex with real API keys as well as native accounts; check
   expired/revoked credentials fail clearly and importing fresh auth recovers.
5. Install the built wheel into a fresh environment outside this checkout. Run
   help, generated YAML, deployment and a turn without a SaaS checkout or service.

Record actual outcomes in PLAN.md. Do not report CI configuration as a CI pass,
container restart as a machine reboot, or fixture completions as model quality.
