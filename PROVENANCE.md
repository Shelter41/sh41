# Provenance

The domain model and runtime conventions follow Shelter41 at source commit
`1d085945854a8f77bcf624f1dd0dd123f355cb2b`.

This project is independently packaged. It does not import, mount, or contact the
SaaS project. Any adapted source files are recorded here as they are introduced.

`src/sh41_local/drivers/` adapts the source commit's `sandbox/runtime.py`,
`tmux_runtime.py`, `harness_driver.py`, `claude_driver.py`, `codex_driver.py`,
`claude_jsonl.py`, `codex_rollout.py`, `codex_events.py`, and `session_store.py`.
Imports are package-relative. HTTP snapshot upload/download is removed. Local
native state persists directly. The local transcript reader buffers partial UTF-8
lines and treats idle-without-completion as uncertain rather than successful.
Tmux gains attachment/input-ownership helpers; its existing process contract stays.
