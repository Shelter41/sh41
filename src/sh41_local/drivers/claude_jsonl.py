# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Tail Claude Code session JSONL and map lines to claude_event SSE payloads."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterator

# The manager supplies its idle timeout when tailing (import avoided at module load).
DEFAULT_TURN_IDLE_SEC = 3.0
DEFAULT_POLL_INTERVAL = 0.25


def claude_config_dir(home: str | Path | None = None) -> Path:
    """The directory Claude Code keeps its state in (``~/.claude`` by default)."""
    if home is not None:
        return Path(home) / ".claude"
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured)
    return Path(os.environ.get("HOME", "/root")) / ".claude"


def project_dir_for_cwd(cwd: str | Path, *, home: str | Path | None = None) -> Path:
    """Mirror ~/.claude/projects/<encoded-cwd> path rules.

    Claude Code slugifies the cwd by replacing EVERY non-alphanumeric character
    with '-' (its rule is `cwd.replace(/[^a-zA-Z0-9]/g, '-')`), not just the path
    separator. This matters for our repo clones at /workspace/repos/<owner>__<name>:
    the '__' becomes '--', so a separator-only encoding would watch the wrong
    directory and never find the session jsonl (agent hangs "thinking").

    ``home`` overrides the config directory (tests); otherwise CLAUDE_CONFIG_DIR
    wins over $HOME, matching the CLI.
    """
    config_dir = claude_config_dir(home)
    path = str(Path(cwd).resolve())
    encoded = re.sub(r"[^A-Za-z0-9]", "-", path)
    if not encoded.startswith("-"):
        encoded = "-" + encoded
    return config_dir / "projects" / encoded


def discover_newest_jsonl(project_dir: Path, *, after_mtime: float | None = None) -> Path | None:
    """Return the newest .jsonl under project_dir (flat or sessions/)."""
    if not project_dir.is_dir():
        return None
    candidates: list[Path] = []
    for pattern in ("*.jsonl", "sessions/*.jsonl"):
        candidates.extend(project_dir.glob(pattern))
    if after_mtime is not None:
        candidates = [p for p in candidates if p.stat().st_mtime >= after_mtime - 1.0]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_jsonl_line(line: str) -> dict[str, Any] | None:
    """Map a JSONL record to a claude_event payload, or None if skipped."""
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None

    entry_type = record.get("type")
    if entry_type == "assistant":
        msg = record.get("message")
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return record
        return None

    if entry_type == "user":
        msg = record.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "user":
            return None
        content = msg.get("content")
        # Tool results only — user prompts are shown from run metadata in the UI.
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            return record
        return None

    if entry_type == "result":
        return record

    return None


def extract_ask_user_questions(record: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Return questions from an assistant AskUserQuestion tool_use, if present."""
    if record.get("type") != "assistant":
        return None
    msg = record.get("message") or {}
    content = msg.get("content") or []
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
            inp = block.get("input") or {}
            questions = inp.get("questions")
            if isinstance(questions, list) and questions:
                return questions
    return None


def is_turn_complete(record: dict[str, Any]) -> bool:
    """Heuristic: assistant finished or explicit result line."""
    if record.get("type") == "result":
        return True
    if record.get("type") != "assistant":
        return False
    msg = record.get("message") or {}
    stop = msg.get("stop_reason")
    return bool(record.get("isApiErrorMessage")) or stop in (
        "end_turn", "stop_sequence", "max_tokens", "refusal",
    )


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Read bytes from offset; return complete lines and new offset."""
    if not path.is_file():
        return [], offset
    size = path.stat().st_size
    if size <= offset:
        return [], offset
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    complete, separator, _ = data.rpartition(b"\n")
    if not separator:
        return [], offset
    return complete.decode("utf-8", errors="replace").splitlines(), offset + len(complete) + 1


def tail_until_idle(
    path: Path,
    offset: int,
    emit: Callable[[dict[str, Any]], None],
    *,
    parse_line: Callable[[str], list[dict[str, Any]]],
    is_complete: Callable[[dict[str, Any]], bool],
    turn_idle_sec: float = DEFAULT_TURN_IDLE_SEC,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_wait_sec: float = 3600.0,
    first_activity_sec: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    stall_check: Callable[[], bool] | None = None,
    idle_debug: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Agent-neutral JSONL tail loop shared by Claude Code and Codex.

    Poll ``path`` from byte ``offset``; for each new line, ``parse_line`` yields
    zero or more claude_event payloads (Codex maps one rollout line to several;
    Claude maps to at most one). Emit each, and stop on turn complete
    (``is_complete``), idle after activity, cancel, or timeout. Returns the final
    byte offset. ``stall_check`` pauses the idle timeout (e.g. while awaiting a
    user answer / approval).

    ``first_activity_sec`` bounds the wait for the *first* payload. The idle
    timeout below only applies once something has been seen, so a turn that never
    starts — a prompt the harness never accepted — otherwise sits here for the
    full ``max_wait_sec``, an hour by default, and its caller then cannot tell
    "produced nothing" from "finished". Callers that can retry a submission set
    this and check whether anything was emitted.
    """
    current_offset = offset
    started = time.monotonic()
    last_activity = started
    saw_activity = False
    deadline = started + max_wait_sec
    last_payload_summary: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        if cancel_check and cancel_check():
            break

        lines, current_offset = read_new_lines(path, current_offset)
        turn_done = False
        for line in lines:
            for payload in parse_line(line):
                saw_activity = True
                last_activity = time.monotonic()
                emit(payload)
                last_payload_summary = summarize_payload(payload)
                if is_complete(payload):
                    turn_done = True

        if turn_done:
            break

        if (
            not saw_activity
            and first_activity_sec is not None
            and (time.monotonic() - started) >= first_activity_sec
        ):
            break

        if saw_activity and (time.monotonic() - last_activity) >= turn_idle_sec:
            if stall_check and stall_check():
                last_activity = time.monotonic()
                time.sleep(poll_interval)
                continue
            if idle_debug is not None:
                idle_debug({
                    "reason": "idle_timeout_synthetic_result",
                    "turn_idle_sec": turn_idle_sec,
                    "current_offset": current_offset,
                    "last_payload": last_payload_summary,
                })
            emit({"type": "result", "result": "No confirmed completion before idle timeout",
                  "is_error": True, "uncertain": True})
            break

        time.sleep(poll_interval)

    return current_offset


def _parse_jsonl_line_list(line: str) -> list[dict[str, Any]]:
    payload = parse_jsonl_line(line)
    return [payload] if payload is not None else []


def tail_jsonl_until_idle(
    jsonl_path: Path,
    offset: int,
    emit: Callable[[dict[str, Any]], None],
    *,
    turn_idle_sec: float = DEFAULT_TURN_IDLE_SEC,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_wait_sec: float = 3600.0,
    first_activity_sec: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    stall_check: Callable[[], bool] | None = None,
    idle_debug: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Claude Code binding of the shared tail loop (see ``tail_until_idle``)."""
    return tail_until_idle(
        jsonl_path,
        offset,
        emit,
        parse_line=_parse_jsonl_line_list,
        is_complete=is_turn_complete,
        turn_idle_sec=turn_idle_sec,
        poll_interval=poll_interval,
        max_wait_sec=max_wait_sec,
        first_activity_sec=first_activity_sec,
        cancel_check=cancel_check,
        stall_check=stall_check,
        idle_debug=idle_debug,
    )


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    entry_type = payload.get("type")
    summary: dict[str, Any] = {"type": entry_type}
    if entry_type == "assistant":
        msg = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        content = msg.get("content")
        summary["stop_reason"] = msg.get("stop_reason")
        if isinstance(content, list):
            summary["block_types"] = [
                block.get("type") for block in content if isinstance(block, dict)
            ]
            summary["text_lengths"] = [
                len(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
        else:
            summary["content_shape"] = type(content).__name__
    elif entry_type == "user":
        msg = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, list):
            summary["block_types"] = [
                block.get("type") for block in content if isinstance(block, dict)
            ]
    elif entry_type == "result":
        summary["result_length"] = len(str(payload.get("result") or ""))
        summary["is_error"] = bool(payload.get("is_error"))
    return summary


def iter_jsonl_events(path: Path, offset: int = 0) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield (payload, new_offset) for each parseable line from offset (one-shot read)."""
    lines, new_offset = read_new_lines(path, offset)
    off = offset
    for line in lines:
        payload = parse_jsonl_line(line)
        if payload is not None:
            yield payload, new_offset
        # approximate per-line offset not needed for tests
    if lines:
        _, off = read_new_lines(path, offset)
