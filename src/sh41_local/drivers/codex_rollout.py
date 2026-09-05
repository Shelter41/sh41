# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Discover and tail Codex interactive session rollout files.

The interactive ``codex`` TUI persists each session as JSONL under
``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<iso>-<uuid>.jsonl``. This module is
the Codex analog of ``claude_jsonl``: it locates the active rollout file and
streams it incrementally, delegating per-line shaping to
``codex_events.normalize_rollout_line`` and the byte-offset tail loop to the
shared ``claude_jsonl.tail_until_idle``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from .claude_jsonl import read_new_lines, tail_until_idle  # noqa: F401 (shared helpers)
from .codex_events import normalize_rollout_line

_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)


def codex_sessions_root() -> Path:
    """Return $CODEX_HOME/sessions (or ~/.codex/sessions) — the rollout tree root."""
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home) if codex_home else Path(os.environ.get("HOME", "/root")) / ".codex"
    return base / "sessions"


def extract_session_id_from_name(path: Path | str) -> str | None:
    """Pull the session UUID out of a ``rollout-<iso>-<uuid>.jsonl`` filename."""
    match = _UUID_RE.search(Path(path).name)
    return match.group(1) if match else None


def discover_newest_rollout(
    root: Path,
    *,
    after_mtime: float | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Return the active rollout .jsonl under the date-sharded sessions tree.

    Prefer a file whose name contains ``session_id`` (exact session); otherwise
    the newest by mtime. ``after_mtime`` filters out rollouts from prior runs.
    """
    if not root.is_dir():
        return None
    candidates = list(root.glob("**/rollout-*.jsonl"))
    if after_mtime is not None:
        candidates = [p for p in candidates if p.stat().st_mtime >= after_mtime - 1.0]
    if not candidates:
        return None
    if session_id:
        for path in candidates:
            if session_id in path.name:
                return path
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_rollout_line(line: str) -> list[dict[str, Any]]:
    """Map one rollout line to zero or more claude_event payloads."""
    return normalize_rollout_line(line)[0]


def rollout_turn_complete(payload: dict[str, Any]) -> bool:
    """Turn boundary marker: the normalizer emits a ``result`` on task_complete."""
    return payload.get("type") == "result"


def tail_rollout_until_idle(
    path: Path,
    offset: int,
    emit: Callable[[dict[str, Any]], None],
    **kwargs: Any,
) -> int:
    """Codex binding of the shared tail loop (see claude_jsonl.tail_until_idle)."""
    return tail_until_idle(
        path,
        offset,
        emit,
        parse_line=parse_rollout_line,
        is_complete=rollout_turn_complete,
        **kwargs,
    )


def extract_codex_approval(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalized approval request if this rollout record is one.

    NOTE: codex-cli 0.142.5 does not persist approval prompts to the rollout
    (they are TUI-only), so in practice this returns None and interactive
    approval HITL is handled by pane detection. Kept defensively for builds that
    do surface an ``*_approval_request`` event so we light up automatically.
    """
    if str(record.get("type")) != "event_msg":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    ptype = str(payload.get("type") or "")
    if "approval_request" not in ptype:
        return None
    command = payload.get("command") or payload.get("cmd") or payload.get("patch")
    return {
        "kind": ptype,
        "call_id": payload.get("call_id") or payload.get("id"),
        "command": command,
        "prompt": _approval_prompt(ptype, command),
    }


def _approval_prompt(ptype: str, command: Any) -> str:
    verb = "apply this patch" if "patch" in ptype else "run this command"
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    detail = f": {command}" if command else ""
    return f"Codex wants to {verb}{detail} — approve?"
