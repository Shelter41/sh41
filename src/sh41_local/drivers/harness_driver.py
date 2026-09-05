# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Contracts shared by sh41-agent-mgr and harness-specific drivers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from .runtime import Runtime, RuntimeHandle


@dataclass(frozen=True)
class NativeSnapshot:
    data: bytes
    native_session_id: str | None = None


@dataclass(frozen=True)
class DriverServices:
    """Manager-owned filesystem services invoked by harness-owned setup flows."""

    prepare_claude_home: Callable[..., str | None]
    prepare_codex_home: Callable[[str | None], None]
    prepare_kimi_home: Callable[[str | None], None]
    seed_codex_trust: Callable[[str], None]
    git_project_root: Callable[[str], str]
    write_global_instructions: Callable[[str, str], None]
    configure_memory: Callable[..., None]
    ensure_generated_excludes: Callable[[str], None]


@runtime_checkable
class HarnessDriver(Protocol):
    """The common harness/runtime and structured-output boundary.

    Launch configuration and native snapshot metadata remain harness-owned;
    control-plane download/upload callbacks stay in sh41-agent-mgr.
    """

    kind: str
    runtime: Runtime

    def configure(
        self, run: dict[str, Any], cwd: str, services: DriverServices,
    ) -> str | None: ...

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
    ) -> dict[str, Any]: ...

    def send_message(self, handle: RuntimeHandle, prompt: str) -> None: ...

    def interrupt(self, handle: RuntimeHandle) -> None: ...

    def is_running(self, handle: RuntimeHandle) -> bool: ...

    def resolve_transcript(
        self,
        state: dict[str, Any],
        *,
        cwd: str | None = None,
        after_mtime: float | None = None,
    ) -> Path | None: ...

    def transcript_offset(self, path: Path | None) -> int: ...

    def consume_transcript(
        self,
        path: Path,
        offset: int,
        emit: Callable[[dict[str, Any]], None],
        *,
        turn_idle_sec: float,
        cancel_check: Callable[[], bool],
        stall_check: Callable[[], bool] | None = None,
        idle_debug: Callable[[dict[str, Any]], None] | None = None,
        first_activity_sec: float | None = None,
    ) -> int: ...

    def reconcile(
        self, handle: RuntimeHandle | None, state: dict[str, Any],
    ) -> None: ...

    def restore_transcript(
        self, data: bytes, state: dict[str, Any], *, cwd: str,
    ) -> bool: ...

    def snapshot_transcript(
        self, state: dict[str, Any], *, cwd: str, since: float | None = None,
    ) -> NativeSnapshot | None: ...
