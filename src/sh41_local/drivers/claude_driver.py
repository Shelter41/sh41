# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Claude Code harness lifecycle on top of the provider-neutral Runtime."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from .claude_jsonl import discover_newest_jsonl, project_dir_for_cwd, tail_jsonl_until_idle
from .harness_driver import DriverServices, NativeSnapshot
from . import session_store
from .runtime import InputKey, ProcessSpec, Runtime, RuntimeHandle

logger = logging.getLogger("sh41.driver.claude")

CLAUDE_STARTUP_SEC = int(os.environ.get("CLAUDE_STARTUP_SECONDS", "60"))
PROMPT_CONFIRM_SEC = float(os.environ.get("CLAUDE_PROMPT_CONFIRM_SECONDS", "3"))
PROMPT_SUBMIT_ATTEMPTS = int(os.environ.get("CLAUDE_PROMPT_SUBMIT_ATTEMPTS", "4"))
CLAUDE_DEAD_CONFIRM_SEC = 5
CLAUDE_RESPAWN_SEC = 30
CLAUDE_RESPAWN_MAX_ATTEMPTS = 3

_VALID_PERMISSION_MODES = frozenset({
    "default", "acceptEdits", "plan", "dontAsk", "bypassPermissions",
})
_PINNED_MODEL_IDS = {"opus-4-8": "claude-opus-4-8"}
_ONBOARDING_MARKERS = (
    "select login method",
    "claude account with subscription",
    "anthropic console account",
    "do you trust the files",
    "choose a theme",
    "pick a theme",
)
_UI_MARKERS = (
    "claude code",
    "esc to interrupt",
    "ctrl+r to restart",
    "anthropic",
    "(shift+tab to cycle)",
)
_BUSY_MARKERS = ("esc to interrupt",)
_COMPOSER_MATCH_CHARS = 40
_LAUNCH_ERROR_MARKERS = (
    "not in the availablemodels allowlist",
    "not allowed by this account's model settings",
    "is restricted by your organization's settings",
    "unknown model",
    "invalid model",
    "model is not available",
    "you're out of usage credits",
    "unable to connect to anthropic services",
    "failed to connect to api.anthropic.com",
    "authentication failed",
    "not authenticated",
    "usage credits are required",
    "turn on usage credits",
    "enable usage credits",
)


def _preview(text: str, *, limit: int = 500) -> str:
    preview = text.strip()
    if len(preview) > limit:
        preview = preview[-limit:]
    return preview.replace("\n", "\\n")


class ClaudeDriver:
    kind = "claude_code"

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def configure(
        self, run: dict[str, Any], cwd: str, services: DriverServices,
    ) -> str | None:
        token = services.prepare_claude_home(
            creds_json=run.get("claude_credentials_json"),
            cwd=cwd,
            allowed_tools=run.get("allowed_tools") or [],
            permission_mode=run.get("permission_mode") or "acceptEdits",
            system_prompt=run.get("system_prompt") or "",
        )
        services.configure_memory(
            self.kind, cwd, enabled=bool(run.get("memory_enabled", False)),
        )
        services.ensure_generated_excludes(cwd)
        return token

    @staticmethod
    def clear_process_state(state: dict[str, Any]) -> None:
        """Forget only the local process while retaining transcript identity."""
        for key in (
            "claude_started",
            "claude_start_mtime",
            "claude_model",
            "claude_argv",
        ):
            state.pop(key, None)

    @classmethod
    def clear_state(cls, state: dict[str, Any]) -> None:
        """Forget process and transcript state when changing conversations."""
        cls.clear_process_state(state)
        for key in ("jsonl_path", "jsonl_offset"):
            state.pop(key, None)

    @staticmethod
    def clear_conversation_state(state: dict[str, Any]) -> None:
        for key in ("session_id", "native_session_id", "snapshot_sha256"):
            state.pop(key, None)

    @staticmethod
    def build_argv(
        *, model: str | None, system_prompt: str,
        permission_mode: str = "acceptEdits",
    ) -> list[str]:
        argv = ["claude"]
        if model:
            argv += ["--model", _PINNED_MODEL_IDS.get(model, model)]
        if system_prompt.strip():
            argv += ["--append-system-prompt", system_prompt]
        mode = (permission_mode or "acceptEdits").strip()
        if mode not in _VALID_PERMISSION_MODES:
            raise RuntimeError(f"Unsupported permission_mode: {mode!r}")
        if mode != "default":
            argv += ["--permission-mode", mode]
        return argv

    @staticmethod
    def session_argv(argv: list[str], state: dict[str, Any]) -> list[str]:
        session_id = state.get("session_id")
        if not session_id or not argv:
            return list(argv)
        cwd = state.get("cwd") or "/workspace/agent"
        native = str(state.get("native_session_id") or session_id)
        flag = (
            ["--resume", native]
            if session_store.has_transcript(cwd, native)
            else ["--session-id", str(session_id)]
        )
        return [argv[0], *flag, *argv[1:]]

    @staticmethod
    def detect_onboarding(text: str) -> bool:
        lower = text.lower()
        return any(marker in lower for marker in _ONBOARDING_MARKERS)

    def is_running(self, handle: RuntimeHandle) -> bool:
        output = self.runtime.inspect(handle).output.lower()
        return not self.detect_onboarding(output) and any(
            marker in output for marker in _UI_MARKERS
        )

    def wait_for_ready(
        self,
        handle: RuntimeHandle,
        *,
        requested_model: str | None = None,
        timeout_sec: float = CLAUDE_STARTUP_SEC,
    ) -> None:
        deadline = time.time() + timeout_sec
        started = time.time()
        last = ""
        while time.time() < deadline:
            status = self.runtime.inspect(handle)
            last = status.output
            lower = last.lower()
            if self.detect_onboarding(last):
                raise RuntimeError(
                    "Claude Code is not pre-configured. Run `sh41 claude-auth` first. "
                    "The prompt was not submitted."
                )
            error = next(
                (marker for marker in _LAUNCH_ERROR_MARKERS if marker in lower),
                None,
            )
            if error:
                raise RuntimeError(
                    f"Claude Code failed to start with model {requested_model!r}: "
                    f"{error}. The prompt was not submitted."
                )
            if any(marker in lower for marker in _UI_MARKERS):
                logger.info(
                    "Claude startup composer ready (model=%s)", requested_model,
                )
                return
            if time.time() - started >= 0.5 and status.exists and not status.active:
                raise RuntimeError(
                    "Claude Code exited before its composer became ready for model "
                    f"{requested_model!r}. The prompt was not submitted. "
                    f"output={_preview(last, limit=300)!r}"
                )
            time.sleep(0.1)
        raise RuntimeError(
            "Claude Code startup timed out before its composer became ready for model "
            f"{requested_model!r}. The prompt was not submitted. "
            f"output={_preview(last, limit=300)!r}"
        )

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
        if oauth_token:
            self.runtime.set_environment(
                handle, {"CLAUDE_CODE_OAUTH_TOKEN": oauth_token},
            )
        stored_mode = state.get("permission_mode")
        stored_model = state.get("claude_model")
        if state.get("claude_started"):
            if stored_mode == permission_mode and stored_model == requested_model:
                return state
            logger.info(
                "Restarting Claude (permission_mode %s -> %s, model %s -> %s)",
                stored_mode,
                permission_mode,
                stored_model,
                requested_model,
            )
            self.interrupt(handle)
            time.sleep(0.5)
            self.clear_process_state(state)

        launch_argv = self.session_argv(argv, state)
        logger.info(
            "Starting Claude execution %s (session=%s)",
            handle.key,
            state.get("session_id") or "none",
        )
        self.runtime.start(handle, ProcessSpec(argv=launch_argv))
        state["claude_started"] = True
        state["claude_start_mtime"] = time.time()
        state["permission_mode"] = permission_mode
        state["claude_model"] = requested_model
        state["claude_argv"] = list(argv)
        if not skip_startup_wait:
            try:
                self.wait_for_ready(handle, requested_model=requested_model)
            except RuntimeError:
                self.clear_process_state(state)
                raise
        return state

    @staticmethod
    def prompt_pending_in_composer(output: str, prompt: str) -> bool:
        lowered = output.lower()
        if any(marker in lowered for marker in _BUSY_MARKERS):
            return False
        needle = " ".join(prompt.split())[:_COMPOSER_MATCH_CHARS]
        if not needle:
            return False
        lines = output.splitlines()
        for index in range(len(lines) - 1, -1, -1):
            if lines[index].lstrip().startswith(("❯", ">")):
                tail = " ".join(" ".join(lines[index:]).split())
                return needle in tail
        return False

    def send_message(self, handle: RuntimeHandle, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            return
        self.runtime.write_text(handle, prompt)
        time.sleep(0.3)
        self.runtime.press(handle, InputKey.ESCAPE)
        time.sleep(0.1)
        self.runtime.press(handle, InputKey.ENTER)
        for attempt in range(1, PROMPT_SUBMIT_ATTEMPTS):
            time.sleep(PROMPT_CONFIRM_SEC)
            output = self.runtime.inspect(handle).output
            if not self.prompt_pending_in_composer(output, prompt):
                return
            logger.warning(
                "Prompt still in composer after attempt %d/%d; pressing Enter",
                attempt,
                PROMPT_SUBMIT_ATTEMPTS,
            )
            self.runtime.press(handle, InputKey.ENTER)
        time.sleep(PROMPT_CONFIRM_SEC)
        output = self.runtime.inspect(handle).output
        if self.prompt_pending_in_composer(output, prompt):
            raise RuntimeError(
                "Claude Code did not accept the prompt: it is still in the composer "
                f"after {PROMPT_SUBMIT_ATTEMPTS} attempts. "
                f"output={_preview(output, limit=300)!r}"
            )

    def interrupt(self, handle: RuntimeHandle) -> None:
        self.runtime.press(handle, InputKey.CTRL_C)

    @staticmethod
    def resolve_transcript(
        state: dict[str, Any], *, cwd: str | None = None,
        after_mtime: float | None = None,
    ) -> Path | None:
        existing = state.get("jsonl_path")
        if existing:
            path = Path(existing)
            if path.is_file():
                return path
        native = state.get("native_session_id") or state.get("session_id")
        if native:
            expected = session_store.transcript_path(cwd or state.get("cwd") or "/workspace/agent", native)
            return expected if expected.is_file() else None
        project_dir = project_dir_for_cwd(cwd or state.get("cwd") or "/workspace/agent")
        mtime_filter = after_mtime if after_mtime is not None else state.get(
            "claude_start_mtime",
        )
        return discover_newest_jsonl(
            project_dir,
            after_mtime=float(mtime_filter) if mtime_filter else None,
        )

    @staticmethod
    def transcript_offset(path: Path | None) -> int:
        return path.stat().st_size if path is not None and path.is_file() else 0

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
        return tail_jsonl_until_idle(
            path,
            offset,
            emit,
            turn_idle_sec=turn_idle_sec,
            cancel_check=cancel_check,
            stall_check=stall_check,
            idle_debug=idle_debug,
            first_activity_sec=first_activity_sec,
        )

    def submit_answers(
        self,
        handle: RuntimeHandle,
        questions: list[dict[str, Any]],
        answers: dict[str, Any],
    ) -> None:
        for question in questions:
            question_text = (question.get("question") or "").strip()
            raw = answers.get(question_text)
            labels = self._option_labels(question)
            if not question_text or raw is None or not labels:
                continue
            selected = [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]
            for choice in selected:
                try:
                    index = labels.index(choice)
                except ValueError:
                    index = 0
                self.runtime.write_text(handle, str(index + 1))
                time.sleep(0.2)
                self.runtime.press(handle, InputKey.ENTER)
                time.sleep(0.35)
        time.sleep(0.2)
        self.runtime.press(handle, InputKey.ENTER)

    @staticmethod
    def _option_labels(question: dict[str, Any]) -> list[str]:
        labels: list[str] = []
        for option in question.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else option
            if isinstance(label, str) and label.strip():
                labels.append(label.strip())
        return labels

    def reconcile(
        self, handle: RuntimeHandle | None, state: dict[str, Any],
    ) -> None:
        if state.get("claude_started") and (
            handle is None or not self.runtime.inspect(handle).exists
        ):
            self.clear_process_state(state)

    def maintain_process(
        self,
        handle: RuntimeHandle | None,
        state: dict[str, Any],
        respawns: dict[str, dict[str, Any]],
        *,
        execution_key: str,
        now: float | None = None,
    ) -> None:
        """Apply Claude's bounded respawn policy to one logical execution."""
        argv = state.get("claude_argv")
        if not state.get("claude_started") or not argv or handle is None:
            respawns.pop(execution_key, None)
            return
        if self.runtime.inspect(handle).active:
            respawns.pop(execution_key, None)
            return

        current = time.time() if now is None else now
        track = respawns.setdefault(
            execution_key,
            {"dead_since": current, "last": 0.0, "count": 0},
        )
        if current - track["dead_since"] < CLAUDE_DEAD_CONFIRM_SEC:
            return
        if current - track["last"] < CLAUDE_RESPAWN_SEC:
            return
        if track["count"] >= CLAUDE_RESPAWN_MAX_ATTEMPTS:
            if not track.get("gave_up"):
                logger.error(
                    "Claude exited %d times in %s; leaving the execution stopped. "
                    "Last output: %r",
                    track["count"],
                    handle.key,
                    _preview(self.runtime.inspect(handle).output, limit=300),
                )
                track["gave_up"] = True
            return

        track["count"] += 1
        track["last"] = current
        launch = list(argv)
        if track["count"] == 1:
            launch = self.session_argv(launch, state)
        logger.warning(
            "Claude gone from %s (attempt %d/%d); respawning",
            handle.key,
            track["count"],
            CLAUDE_RESPAWN_MAX_ATTEMPTS,
        )
        self.runtime.start(handle, ProcessSpec(argv=launch))

    @staticmethod
    def restore_transcript(
        data: bytes, state: dict[str, Any], *, cwd: str,
    ) -> bool:
        session_id = str(state.get("session_id") or "")
        native = str(state.get("native_session_id") or session_id)
        restored = session_store.unpack(data, cwd)
        if native in restored:
            selected = native
        elif restored:
            selected = restored[0]
            state["native_session_id"] = selected
        else:
            return False
        state["jsonl_path"] = str(session_store.transcript_path(cwd, selected))
        return True

    @staticmethod
    def snapshot_transcript(
        state: dict[str, Any], *, cwd: str, since: float | None = None,
    ) -> NativeSnapshot | None:
        session_id = str(state.get("session_id") or "")
        if not session_id:
            return None
        expected = str(state.get("native_session_id") or session_id)
        native = session_store.newest_session_id(cwd, after_mtime=since) or expected
        data = session_store.pack(cwd, [native, expected, session_id])
        return NativeSnapshot(data, native) if data else None
