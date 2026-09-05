# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Tmux implementation of the agent manager's local Runtime contract.

No caller needs to know tmux session names, commands, panes, or key spelling.
The internal naming deliberately matches the historical manager so executions
survive an in-place manager upgrade.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path

from .runtime import (
    EnsureResult,
    InputKey,
    ProcessSpec,
    RuntimeHandle,
    RuntimeStatus,
)

logger = logging.getLogger("sh41.runtime.tmux")

_MANAGER_TOKEN_ENV = ("SANDBOX_TOKEN",)
_SHELL_COMMANDS = frozenset({"bash", "sh", "zsh", "fish", "dash", "-bash"})
_KEYS = {
    InputKey.ENTER: "Enter",
    InputKey.ESCAPE: "Escape",
    InputKey.CTRL_C: "C-c",
}


class TmuxRuntime:
    def __init__(self) -> None:
        self._tracked: set[RuntimeHandle] = set()
        self._working_directories: dict[RuntimeHandle, str] = {}
        self._environments: dict[RuntimeHandle, dict[str, str]] = {}

    @staticmethod
    def _session_name(key: str) -> str:
        safe = "".join(
            char if char.isalnum() or char in "-_" else "-" for char in key
        ).strip("-") or "default"
        return f"hatchery-{safe}"[:64]

    @staticmethod
    def _scrubbed_subprocess_env() -> dict[str, str]:
        env = dict(os.environ)
        for key in _MANAGER_TOKEN_ENV:
            env.pop(key, None)
        return env

    def _run(
        self, *args: str, check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=check,
            env=self._scrubbed_subprocess_env(),
        )

    def require(self) -> None:
        if subprocess.run(["which", "tmux"], capture_output=True).returncode != 0:
            raise RuntimeError(
                "tmux is unavailable. Rebuild the sh41 Local runner image."
            )

    def _exists(self, handle: RuntimeHandle) -> bool:
        return self._run(
            "has-session", "-t", self._session_name(handle.key),
        ).returncode == 0

    def ensure(self, key: str, cwd: str) -> EnsureResult:
        self.require()
        handle = RuntimeHandle(key)
        session = self._session_name(key)
        exists = self._exists(handle)

        for env_key in _MANAGER_TOKEN_ENV:
            self._run("set-environment", "-g", "-u", env_key)
        if not exists:
            logger.info("Creating persistent execution %s in %s", key, cwd)
            created = self._run("new-session", "-d", "-s", session, "-c", cwd)
            if created.returncode != 0:
                raise RuntimeError(
                    "Unable to create persistent execution: "
                    f"{created.stderr.strip() or created.stdout}"
                )
        # A finished child leaves a dead pane that can be inspected and
        # respawned.  This is the runtime-level persistence primitive used by
        # readiness checks and Claude's bounded respawn policy.
        self._run("set-option", "-t", session, "remain-on-exit", "on")
        for env_key in _MANAGER_TOKEN_ENV:
            self._run("set-environment", "-t", session, "-u", env_key)
        self._tracked.add(handle)
        self._working_directories[handle] = cwd
        self._environments.setdefault(handle, {})
        return EnsureResult(handle=handle, created=not exists)

    def lookup(self, key: str) -> RuntimeHandle | None:
        handle = RuntimeHandle(key)
        if not self._exists(handle):
            return None
        self._tracked.add(handle)
        self._environments.setdefault(handle, {})
        return handle

    def set_environment(
        self, handle: RuntimeHandle, values: Mapping[str, str | None],
    ) -> None:
        session = self._session_name(handle.key)
        environment = self._environments.setdefault(handle, {})
        for key, value in values.items():
            if value is None:
                environment.pop(key, None)
                self._run("set-environment", "-t", session, "-u", key)
            else:
                environment[key] = value
                self._run("set-environment", "-t", session, key, value)

    def start(self, handle: RuntimeHandle, spec: ProcessSpec) -> None:
        if not spec.argv:
            raise ValueError("ProcessSpec.argv must not be empty")
        command = " ".join(shlex.quote(arg) for arg in spec.argv)
        if spec.output_path is not None:
            Path(spec.output_path).parent.mkdir(parents=True, exist_ok=True)
            command = f"{command} >> {shlex.quote(str(spec.output_path))} 2>&1"
        # Replace tmux's non-interactive wrapper shell with the requested child.
        # Without ``exec``, pane_current_command remains ``bash`` even while the
        # manager/harness is alive and every status check reports it stopped.
        # ``remain-on-exit`` (set by ensure) keeps the execution adoptable after
        # the child exits without introducing a shell as a false liveness signal.
        command = f"exec {command}"
        environment = dict(self._environments.get(handle, {}))
        environment.update(spec.env)
        args = ["respawn-pane", "-k", "-t", self._session_name(handle.key)]
        cwd = self._working_directories.get(handle)
        if cwd:
            args += ["-c", cwd]
        for key, value in environment.items():
            args += ["-e", f"{key}={value}"]
        args += ["--", command]
        result = self._run(*args)
        if result.returncode != 0:
            raise RuntimeError(
                "Unable to start persistent process: "
                f"{result.stderr.strip() or result.stdout}"
            )

    def write_text(self, handle: RuntimeHandle, text: str) -> None:
        self._run(
            "send-keys", "-t", self._session_name(handle.key), "-l", "--", text,
        )

    def attachment_argv(self, handle: RuntimeHandle, *, readonly: bool = False) -> list[str]:
        return ["tmux", "attach-session", *( ["-r"] if readonly else []),
                "-t", self._session_name(handle.key)]

    def has_writer(self, handle: RuntimeHandle) -> bool:
        result = self._run("list-clients", "-t", self._session_name(handle.key),
                           "-F", "#{client_readonly}")
        return result.returncode == 0 and "0" in result.stdout.splitlines()

    def press(self, handle: RuntimeHandle, key: InputKey) -> None:
        self._run(
            "send-keys", "-t", self._session_name(handle.key), _KEYS[key],
        )

    def inspect(self, handle: RuntimeHandle) -> RuntimeStatus:
        session = self._session_name(handle.key)
        if not self._exists(handle):
            return RuntimeStatus(exists=False, active=False)
        command_result = self._run(
            "display-message", "-p", "-t", session,
            "#{pane_current_command}\t#{pane_dead}",
        )
        raw_status = (command_result.stdout or "").strip()
        foreground_raw, _, dead_raw = raw_status.partition("\t")
        foreground = foreground_raw or None
        dead = dead_raw == "1"
        output_result = self._run("capture-pane", "-t", session, "-p")
        output = output_result.stdout or ""
        active = bool(
            not dead
            and foreground
            and foreground.lower() not in _SHELL_COMMANDS
        )
        return RuntimeStatus(
            exists=True,
            active=active,
            foreground_process=foreground,
            output=output,
        )

    def terminate(self, handle: RuntimeHandle) -> None:
        self._run("kill-session", "-t", self._session_name(handle.key))
        self._tracked.discard(handle)
        self._working_directories.pop(handle, None)
        self._environments.pop(handle, None)

    def terminate_all(self) -> None:
        for handle in list(self._tracked):
            self.terminate(handle)
