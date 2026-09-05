# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Codex-specific driver for the interactive ``codex`` harness.

It owns Codex launch, readiness, prompt delivery, interruption, and state while
depending only on the provider-neutral Runtime contract.

Grounded in codex-cli 0.142.5:
  * interactive TUI is ``codex`` (no subcommand); submits on Enter.
  * ``--sandbox read-only|workspace-write|danger-full-access`` picks the fs policy.
  * ``-a/--ask-for-approval untrusted|on-failure|on-request|never`` picks the
    approval policy; ``--dangerously-bypass-approvals-and-sandbox`` skips both.
  * session transcript persists under $CODEX_HOME/sessions (see codex_rollout).
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from .codex_rollout import (
    codex_sessions_root,
    discover_newest_rollout,
    extract_session_id_from_name,
    tail_rollout_until_idle,
)
from .harness_driver import DriverServices, NativeSnapshot
from .runtime import InputKey, ProcessSpec, Runtime, RuntimeHandle
from . import session_store

logger = logging.getLogger("sh41.driver.codex")

# Cold sandboxes boot the Codex TUI slowly; wait generously for the composer to
# become ready before submitting the prompt. Overridable per-operator via env.
CODEX_STARTUP_SEC = int(os.environ.get("CODEX_STARTUP_SECONDS", "60"))
CODEX_TURN_IDLE_SEC = float(os.environ.get("CODEX_TURN_IDLE_SECONDS", "45"))
# v1 default: `never`. Interactive approval prompts are NOT persisted to the
# rollout in 0.142.5, so `on-request` would hang waiting for a TUI answer we
# cannot yet drive. Flip to `on-request` once approval HITL detection lands.
CODEX_APPROVAL_POLICY = os.environ.get("CODEX_APPROVAL_POLICY", "never")

_SANDBOX_BY_PERMISSION_MODE = {
    "default": "workspace-write",
    "acceptEdits": "workspace-write",
    "dontAsk": "workspace-write",
    "plan": "read-only",
    "bypassPermissions": "danger-full-access",
}

# Per-deployment state keys owned by the Codex path (kept in the shared
# claude_state.json alongside Claude's — a deployment runs one agent kind).
STATE_KEYS = (
    "codex_started",
    "codex_start_mtime",
    "codex_permission_mode",
    "codex_system_seeded",
    "rollout_path",
    "rollout_offset",
    "codex_session_id",
    "codex_model",
)
PROCESS_STATE_KEYS = (
    "codex_started",
    "codex_start_mtime",
    "codex_permission_mode",
    "codex_model",
)

_ONBOARDING_MARKERS = (
    "sign in with chatgpt",
    "sign in with your chatgpt",
    "not logged in",
    "please log in",
    "run `codex login`",
    "codex login",
    "authenticate",
)
_ONBOARDING_MARKERS += tuple(
    m.strip().lower()
    for m in os.environ.get("CODEX_ONBOARDING_MARKERS", "").split("|")
    if m.strip()
)

_MODEL_PICKER_MARKERS = ("select model and effort", "select model")
_MODEL_SWITCH_ERROR_MARKERS = (
    "model selection is disabled",
    "no additional models are available",
    "failed to persist model selection",
    "not allowed by this account",
    "model is not available",
    "unknown model",
    "model not found",
    "invalid model",
    "unexpected argument",
)
_ANY_MODEL_STATUS_RE = re.compile(
    r"^\s*[a-zA-Z0-9][a-zA-Z0-9._-]*\s+\S+\s+·",
    re.MULTILINE,
)


def sandbox_for_permission_mode(permission_mode: str | None) -> str:
    mode = (permission_mode or "acceptEdits").strip()
    if mode not in _SANDBOX_BY_PERMISSION_MODE:
        raise RuntimeError(
            f"Unsupported permission_mode for Codex: {mode!r}. "
            "Supported modes: default, acceptEdits, plan, dontAsk, bypassPermissions."
        )
    return _SANDBOX_BY_PERMISSION_MODE[mode]


def build_first_prompt(*, system_prompt: str, prompt: str) -> str:
    """First turn only: fold the system prompt into the submitted message.

    The interactive launcher has no ``--append-system-prompt``; on later turns the
    thread already carries this context, so we submit the bare prompt.
    """
    if not system_prompt.strip():
        return prompt
    return f"{system_prompt.strip()}\n\nUser request:\n{prompt}"


def build_interactive_argv(
    *,
    permission_mode: str | None,
    model: str | None = None,
    resume_session_id: str | None = None,
) -> list[str]:
    """Interactive launcher argv (the prompt is delivered after readiness).

    ``resume_session_id`` continues an existing conversation:
    ``codex [OPTIONS] resume <SESSION_ID>``. The id is passed explicitly rather
    than using ``--last``, which would pick whatever session happens to be
    newest — including one this deployment never asked for. Global options
    precede the subcommand, which is the shape the CLI documents
    (``codex [OPTIONS] <COMMAND>``) and which was verified against 0.144.4.
    """
    mode = (permission_mode or "acceptEdits").strip()
    # --no-alt-screen keeps output inline so Runtime inspection stays stable.
    argv = ["codex", "--no-alt-screen"]
    if model:
        argv += ["-m", model]
    if mode == "bypassPermissions":
        argv += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        argv += [
            "--sandbox", sandbox_for_permission_mode(mode),
            "-a", CODEX_APPROVAL_POLICY,
        ]
    if resume_session_id:
        argv += ["resume", resume_session_id]
    return argv


def detect_onboarding(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in _ONBOARDING_MARKERS)


def is_running(runtime: Runtime, handle: RuntimeHandle) -> bool:
    """True if the Codex process remains active in its execution."""
    return runtime.inspect(handle).active


def clear_state(state: dict[str, Any]) -> None:
    for key in STATE_KEYS:
        state.pop(key, None)


def clear_process_state(state: dict[str, Any]) -> None:
    for key in PROCESS_STATE_KEYS:
        state.pop(key, None)


# Codex 0.144.4 gates a new or changed hook plugin behind an interactive review
# before it will show a composer. The trust it records is a sha256 over some
# internal normalisation of the hook — not of our command, our hook entry or our
# hooks file, all of which were checked against a real trusted_hash and none of
# which matched. Seeding it would mean reverse-engineering an undocumented,
# version-specific digest and silently disabling memory whenever it changed, so
# the gate is answered the way a human answers it.
# Codex puts interactive gates between a launch and its composer. Each entry is
# (what identifies the gate, which option to take, why). The option is matched by
# its label, never by position: the menu is the CLI's and its ordering is not
# ours to depend on.
_STARTUP_GATES = (
    (
        ("hooks need review", "hook is new or changed"),
        re.compile(r"(\d+)\.\s*trust all", re.IGNORECASE),
        "trusting Hatchery's own hook plugin",
    ),
    (
        ("update available",),
        re.compile(r"(\d+)\.\s*skip until next version", re.IGNORECASE),
        # "Update now" runs `npm install -g @openai/codex` inside the sandbox,
        # which would silently replace the CLI version the runner image pins.
        # Skipping is a correctness requirement, not a convenience.
        "declining an in-sandbox CLI upgrade",
    ),
)
# Answer no more than this many gates before giving up, so one we cannot dismiss
# times out with a runtime-output preview rather than looping until the deadline.
_STARTUP_GATE_MAX_ANSWERS = 4


def _answer_startup_gate(
    runtime: Runtime, handle: RuntimeHandle, output: str,
) -> bool:
    """Answer a known startup gate so the composer can appear. True if answered."""
    lower = output.lower()
    for markers, option, why in _STARTUP_GATES:
        if not any(marker in lower for marker in markers):
            continue
        match = option.search(output)
        if not match:
            logger.warning(
                "Codex gate %r shown without its expected option; output=%r",
                markers[0], _preview(output, limit=300),
            )
            return False
        choice = match.group(1)
        logger.info("Codex startup gate: %s (option %s)", why, choice)
        runtime.write_text(handle, choice)
        time.sleep(0.3)
        runtime.press(handle, InputKey.ENTER)
        return True
    return False


def wait_for_ready(
    runtime: Runtime,
    handle: RuntimeHandle,
    *,
    requested_model: str | None = None,
    timeout_sec: float = CODEX_STARTUP_SEC,
) -> None:
    """Wait for a live composer, rejecting auth/model/early-process failures.

    The model is set at launch via ``-m`` (see ``build_interactive_argv``); we
    only wait for the composer to become ready before submitting the prompt — no
    in-TUI model confirmation.
    """
    deadline = time.time() + timeout_sec
    started = time.time()
    last = ""
    answered_gates = 0
    while time.time() < deadline:
        last = runtime.inspect(handle).output
        lower = last.lower()
        if (
            answered_gates < _STARTUP_GATE_MAX_ANSWERS
            and _answer_startup_gate(runtime, handle, last)
        ):
            answered_gates += 1
            time.sleep(1.0)
            continue
        if detect_onboarding(last):
            raise RuntimeError(
                "Codex is not authenticated in this workspace. Run `sh41 codex-auth` "
                "and start a new run. The prompt was not submitted."
            )
        error = next((m for m in _MODEL_SWITCH_ERROR_MARKERS if m in lower), None)
        if error:
            raise RuntimeError(
                f"Codex failed to start with model {requested_model!r}: {error}. "
                "The prompt was not submitted."
            )
        if _composer_is_ready(last):
            logger.info("Codex startup composer ready (model=%s)", requested_model)
            return
        if time.time() - started >= 0.5 and not is_running(runtime, handle):
            raise RuntimeError(
                f"Codex exited before its composer became ready for model "
                f"{requested_model!r}. The prompt was not submitted. "
                f"output={_preview(last, limit=300)!r}"
            )
        time.sleep(0.1)
    raise RuntimeError(
        f"Codex startup timed out before its composer became ready for model "
        f"{requested_model!r}. The prompt was not submitted. "
        f"output={_preview(last, limit=300)!r}"
    )


def ensure_codex_running(
    runtime: Runtime,
    handle: RuntimeHandle,
    argv: list[str],
    *,
    state: dict[str, Any],
    permission_mode: str,
    requested_model: str | None = None,
    skip_startup_wait: bool = False,
) -> dict[str, Any]:
    """Start the interactive Codex TUI if it is not already running.

    The model is set once, at launch, via the ``-m`` flag baked into ``argv``
    (see ``build_interactive_argv``). A live session is reused across turns only
    while both its permission mode and its launched model are unchanged; when
    either differs the process is restarted so the new launch flags take effect
    (there is no in-place model switch).
    """
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        runtime.set_environment(handle, {"CODEX_HOME": codex_home})

    stored_mode = state.get("codex_permission_mode")
    stored_model = state.get("codex_model")
    if state.get("codex_started"):
        if stored_mode == permission_mode and stored_model == requested_model:
            return state
        logger.info(
            "Restarting Codex (permission_mode %s -> %s, model %s -> %s)",
            stored_mode, permission_mode, stored_model, requested_model,
        )
        cancel_turn(runtime, handle)
        time.sleep(0.5)
        clear_process_state(state)

    logger.info("Starting Codex execution %s", handle.key)
    runtime.start(
        handle,
        ProcessSpec(
            argv=argv,
            env={"CODEX_HOME": codex_home} if codex_home else {},
        ),
    )
    state["codex_started"] = True
    state["codex_start_mtime"] = time.time()
    state["codex_permission_mode"] = permission_mode
    state["codex_model"] = requested_model
    if not skip_startup_wait:
        try:
            wait_for_ready(
                runtime, handle, requested_model=requested_model,
            )
        except RuntimeError:
            clear_process_state(state)
            raise
    return state


def submit_prompt(runtime: Runtime, handle: RuntimeHandle, prompt: str) -> None:
    """Type a prompt into the live codex composer (plain Enter, no autocomplete quirk)."""
    prompt = prompt.strip()
    if not prompt:
        return
    runtime.write_text(handle, prompt)
    time.sleep(0.3)
    runtime.press(handle, InputKey.ENTER)


def cancel_turn(runtime: Runtime, handle: RuntimeHandle) -> None:
    """Interrupt the current codex turn (Esc); C-c would quit the app."""
    runtime.press(handle, InputKey.ESCAPE)


def _picker_is_open(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in _MODEL_PICKER_MARKERS)


def _composer_is_ready(text: str) -> bool:
    return (
        not _picker_is_open(text)
        and "select reasoning level" not in text.lower()
        and bool(_ANY_MODEL_STATUS_RE.search(text))
    )


def _preview(text: str, *, limit: int = 500) -> str:
    preview = text.strip()
    if len(preview) > limit:
        preview = preview[-limit:]
    return preview.replace("\n", "\\n")


class CodexDriver:
    kind = "codex"
    CODEX_TURN_IDLE_SEC = CODEX_TURN_IDLE_SEC

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def configure(
        self, run: dict[str, Any], cwd: str, services: DriverServices,
    ) -> None:
        services.prepare_codex_home(run.get("codex_auth_json"))
        services.seed_codex_trust(services.git_project_root(cwd))
        services.write_global_instructions(self.kind, run.get("system_prompt") or "")
        services.configure_memory(
            self.kind, cwd, enabled=bool(run.get("memory_enabled", False)),
        )
        services.ensure_generated_excludes(cwd)

    build_interactive_argv = staticmethod(build_interactive_argv)
    build_first_prompt = staticmethod(build_first_prompt)
    detect_onboarding = staticmethod(detect_onboarding)
    clear_state = staticmethod(clear_state)
    clear_process_state = staticmethod(clear_process_state)

    def is_running(self, handle: RuntimeHandle) -> bool:
        return is_running(self.runtime, handle)

    def ensure_started(
        self,
        handle: RuntimeHandle,
        argv: list[str],
        *,
        state: dict[str, Any],
        permission_mode: str,
        requested_model: str | None = None,
        skip_startup_wait: bool = False,
        oauth_token: str | None = None,
    ) -> dict[str, Any]:
        del oauth_token
        return ensure_codex_running(
            self.runtime,
            handle,
            argv,
            state=state,
            permission_mode=permission_mode,
            requested_model=requested_model,
            skip_startup_wait=skip_startup_wait,
        )

    def send_message(self, handle: RuntimeHandle, prompt: str) -> None:
        submit_prompt(self.runtime, handle, prompt)

    def interrupt(self, handle: RuntimeHandle) -> None:
        cancel_turn(self.runtime, handle)

    def output(self, handle: RuntimeHandle) -> str:
        return self.runtime.inspect(handle).output

    def press_enter(self, handle: RuntimeHandle) -> None:
        self.runtime.press(handle, InputKey.ENTER)

    @staticmethod
    def transcript_root() -> Path:
        return codex_sessions_root()

    @staticmethod
    def resolve_transcript(
        state: dict[str, Any], *, cwd: str | None = None,
        after_mtime: float | None = None,
    ) -> Path | None:
        del cwd
        existing = state.get("rollout_path")
        if existing:
            path = Path(existing)
            if path.is_file():
                return path
        mtime_filter = after_mtime if after_mtime is not None else state.get(
            "codex_start_mtime",
        )
        expected = state.get("codex_session_id")
        if expected:
            return next(codex_sessions_root().glob(f"**/rollout-*-{expected}.jsonl"), None)
        baseline = set(state.get("rollout_baseline") or [])
        if baseline:
            fresh = [p for p in codex_sessions_root().glob("**/rollout-*.jsonl") if str(p) not in baseline]
            return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None
        return discover_newest_rollout(
            codex_sessions_root(),
            after_mtime=float(mtime_filter) if mtime_filter else None,
            session_id=state.get("codex_session_id"),
        )

    @staticmethod
    def transcript_offset(path: Path | None) -> int:
        return path.stat().st_size if path is not None and path.is_file() else 0

    @staticmethod
    def transcript_session_id(path: Path) -> str | None:
        return extract_session_id_from_name(path)

    @staticmethod
    def consume_transcript(
        path: Path,
        offset: int,
        emit: Callable[[dict[str, Any]], None],
        *,
        turn_idle_sec: float,
        cancel_check: Callable[[], bool],
        stall_check: Callable[[], bool] | None = None,
        idle_debug: Callable[[dict[str, Any]], None] | None = None,
        first_activity_sec: float | None = None,
    ) -> int:
        del stall_check, idle_debug
        return tail_rollout_until_idle(
            path,
            offset,
            emit,
            turn_idle_sec=turn_idle_sec,
            first_activity_sec=first_activity_sec,
            cancel_check=cancel_check,
        )

    def reconcile(
        self, handle: RuntimeHandle | None, state: dict[str, Any],
    ) -> None:
        if state.get("codex_started") and (
            handle is None or not self.runtime.inspect(handle).active
        ):
            clear_process_state(state)

    @classmethod
    def restore_transcript(
        cls, data: bytes, state: dict[str, Any], *, cwd: str,
    ) -> bool:
        del cwd
        root = cls.transcript_root()
        session_store.unpack_into(data, root)
        restored = cls.resolve_transcript(state)
        if restored is None:
            return False
        state["rollout_path"] = str(restored)
        state["rollout_offset"] = restored.stat().st_size
        native = cls.transcript_session_id(restored)
        if native:
            state["codex_session_id"] = native
        return True

    @classmethod
    def snapshot_transcript(
        cls, state: dict[str, Any], *, cwd: str, since: float | None = None,
    ) -> NativeSnapshot | None:
        del cwd, since
        raw = state.get("rollout_path")
        if not raw:
            return None
        data = session_store.pack_paths([Path(raw)], root=cls.transcript_root())
        native = state.get("codex_session_id")
        return NativeSnapshot(data, str(native) if native else None) if data else None
