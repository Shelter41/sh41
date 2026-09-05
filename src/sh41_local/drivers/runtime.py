# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Provider-neutral local process/session execution contract.

Harness drivers and the agent manager use only these types.  Runtime-specific
identifiers and commands stay inside the concrete implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class RuntimeHandle:
    """Opaque handle for a persistent local execution."""

    key: str


@dataclass(frozen=True)
class EnsureResult:
    handle: RuntimeHandle
    created: bool


@dataclass(frozen=True)
class ProcessSpec:
    argv: Sequence[str]
    env: Mapping[str, str] = field(default_factory=dict)
    output_path: Path | None = None


@dataclass(frozen=True)
class RuntimeStatus:
    exists: bool
    active: bool
    foreground_process: str | None = None
    output: str = ""


class InputKey(str, Enum):
    ENTER = "enter"
    ESCAPE = "escape"
    CTRL_C = "ctrl-c"


@runtime_checkable
class Runtime(Protocol):
    def require(self) -> None: ...

    def ensure(self, key: str, cwd: str) -> EnsureResult: ...

    def lookup(self, key: str) -> RuntimeHandle | None: ...

    def set_environment(
        self, handle: RuntimeHandle, values: Mapping[str, str | None],
    ) -> None: ...

    def start(self, handle: RuntimeHandle, spec: ProcessSpec) -> None: ...

    def write_text(self, handle: RuntimeHandle, text: str) -> None: ...

    def press(self, handle: RuntimeHandle, key: InputKey) -> None: ...

    def inspect(self, handle: RuntimeHandle) -> RuntimeStatus: ...

    def terminate(self, handle: RuntimeHandle) -> None: ...

    def terminate_all(self) -> None: ...


def create_runtime() -> Runtime:
    """Composition root for the only local runtime implemented today."""
    from .tmux_runtime import TmuxRuntime

    return TmuxRuntime()
