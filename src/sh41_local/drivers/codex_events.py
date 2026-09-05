# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Normalize ``codex exec --json`` JSONL into Hatchery's conversation shape."""
from __future__ import annotations

import json
import re
from typing import Any


AUTH_FAILURE_MESSAGE = (
    "Codex authentication failed or expired. Run `sh41 codex-auth`, "
    "then start a new run."
)

_AUTH_RE = re.compile(
    r"(auth|login|oauth|token|credential).*(expired|invalid|missing|failed|unauthori[sz]ed)"
    r"|(expired|invalid|missing|failed|unauthori[sz]ed).*(auth|login|oauth|token|credential)",
    re.I,
)


def is_auth_failure_text(text: str | None) -> bool:
    return bool(text and _AUTH_RE.search(text))


def normalize_line(line: str) -> tuple[list[dict[str, Any]], bool]:
    """Parse one Codex JSONL line.

    Returns ``(payloads, auth_failed)``. Payloads are already shaped as
    ``claude_event`` payloads so the existing UI renderer can display them.
    Malformed JSON and unknown event types are ignored.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return [], False
    if not isinstance(event, dict):
        return [], False
    return normalize_event(event)


def normalize_event(event: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    event_type = str(event.get("type") or event.get("event") or "")
    lower_type = event_type.lower()
    auth_failed = _event_auth_failed(event)
    item = event.get("item")
    if isinstance(item, dict):
        payloads = _normalize_item_event(lower_type, item)
        if payloads:
            return payloads, auth_failed

    if _is_agent_message(lower_type, event):
        text = _first_text(event, "message", "text", "content", "delta")
        if text:
            return [_assistant_text(text, _event_id(event, "codex-msg"))], auth_failed

    if _is_tool_start(lower_type):
        tool_id = _event_id(event, "codex-tool")
        name = _first_text(event, "name", "tool", "command") or "command"
        input_payload = event.get("input")
        if not isinstance(input_payload, dict):
            input_payload = {}
            command = _first_text(event, "command", "cmd")
            if command:
                input_payload["command"] = command
        return [_tool_use(tool_id, name, input_payload)], auth_failed

    if _is_tool_result(lower_type):
        tool_id = _event_id(event, "codex-tool")
        output = _first_text(event, "output", "stdout", "stderr", "content", "message") or ""
        error = bool(event.get("error") or event.get("is_error") or event.get("failed"))
        return [_tool_result(tool_id, output, error)], auth_failed

    if _is_turn_completed(lower_type):
        text = _first_text(event, "result", "summary", "message", "text", "content") or ""
        return [_result(text, is_error=False)], auth_failed

    if _is_turn_failed(lower_type) or auth_failed:
        text = (
            AUTH_FAILURE_MESSAGE
            if auth_failed
            else _first_text(event, "error", "message", "reason", "text") or "Codex run failed."
        )
        return [_result(text, is_error=True)], auth_failed

    return [], auth_failed


def _normalize_item_event(event_type: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    item_type = str(item.get("type") or "")
    if item_type == "agent_message":
        text = _first_text(item, "text", "message", "content")
        return [_assistant_text(text, _event_id(item, "codex-msg"))] if text else []

    if item_type == "command_execution":
        tool_id = _event_id(item, "codex-tool")
        command = _first_text(item, "command") or "command"
        if event_type == "item.started" or item.get("status") == "in_progress":
            return [_tool_use(tool_id, "command_execution", {"command": command})]
        output = _first_text(item, "aggregated_output", "output", "stdout", "stderr") or ""
        error = bool(
            item.get("status") == "failed"
            or item.get("exit_code") not in (None, 0)
        )
        return [_tool_result(tool_id, output, error)]

    return []


def failed_result_from_process(exit_code: int, stderr: str) -> tuple[dict[str, Any], bool]:
    auth_failed = is_auth_failure_text(stderr)
    text = AUTH_FAILURE_MESSAGE if auth_failed else (
        stderr.strip() or f"Codex exited with status {exit_code}."
    )
    return _result(text[:2000], is_error=True), auth_failed


def _event_auth_failed(event: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(v) for v in (
            event.get("type"),
            event.get("error"),
            event.get("message"),
            event.get("reason"),
            event.get("stderr"),
        )
        if v
    )
    return is_auth_failure_text(haystack)


def _first_text(event: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = event.get(key)
        text = _coerce_text(value)
        if text:
            return text
    return None


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "message", "output", "stdout", "stderr"):
            text = _coerce_text(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        parts = [_coerce_text(item) for item in value]
        text = "\n".join(part for part in parts if part)
        return text or None
    return None


def _event_id(event: dict[str, Any], prefix: str) -> str:
    raw = event.get("id") or event.get("call_id") or event.get("tool_call_id")
    if isinstance(raw, str) and raw:
        return raw
    return f"{prefix}-{abs(hash(json.dumps(event, sort_keys=True, default=str)))}"


def _assistant_text(text: str, msg_id: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"id": msg_id, "content": [{"type": "text", "text": text}]},
    }


def _tool_use(tool_id: str, name: str, input_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": f"{tool_id}-message",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": input_payload}],
        },
    }


def _tool_result(tool_id: str, output: str, error: bool) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": output,
                "is_error": error,
            }],
        },
    }


def _result(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"type": "result", "result": text, "is_error": is_error}


def _usage(usage: dict[str, int]) -> dict[str, Any]:
    """A metrics-only event that the conversation renderer intentionally skips."""
    return {"type": "usage", "usage": usage}


def _is_agent_message(event_type: str, event: dict[str, Any]) -> bool:
    return (
        "agent_message" in event_type
        or event_type in {"message", "assistant_message", "response.output_text.delta"}
        or event.get("role") == "assistant"
    )


def _is_tool_start(event_type: str) -> bool:
    return (
        "tool" in event_type and any(word in event_type for word in ("start", "begin", "call"))
    ) or event_type in {"exec_command_begin", "command_start", "command.started"}


def _is_tool_result(event_type: str) -> bool:
    return (
        "tool" in event_type and any(word in event_type for word in ("result", "end", "output"))
    ) or event_type in {"exec_command_end", "command_end", "command.completed"}


def _is_turn_completed(event_type: str) -> bool:
    return event_type in {"turn.completed", "turn_complete", "completed", "done", "result"}


def _is_turn_failed(event_type: str) -> bool:
    return event_type in {"turn.failed", "turn_error", "failed", "error"}


# ---------------------------------------------------------------------------
# Rollout-file normalization
#
# The interactive `codex` TUI persists a session transcript as JSONL under
# $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<iso>-<uuid>.jsonl. Each line is a
# wrapper record: {"timestamp": ..., "type": ..., "payload": {...}} where
# top-level ``type`` is one of session_meta | response_item | event_msg |
# turn_context. This is a DIFFERENT schema from the `codex exec --json` event
# stream handled above, so it gets its own normalizer. We reuse the same
# claude_event shaping helpers so the UI renderer is unchanged.
# ---------------------------------------------------------------------------

# Tool call/output record subtypes that carry a stable ``call_id`` pairing.
_ROLLOUT_TOOL_CALL_TYPES = {"function_call", "custom_tool_call", "local_shell_call"}
_ROLLOUT_TOOL_OUTPUT_TYPES = {
    "function_call_output",
    "custom_tool_call_output",
    "local_shell_call_output",
}


def normalize_rollout_line(line: str) -> tuple[list[dict[str, Any]], bool]:
    """Parse one rollout JSONL line → (claude_event payloads, auth_failed)."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return [], False
    if not isinstance(record, dict):
        return [], False
    return normalize_rollout_record(record)


def normalize_rollout_record(record: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    kind = str(record.get("type") or "")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return [], False

    if kind == "response_item":
        return _normalize_rollout_response_item(payload), False

    if kind == "event_msg":
        return _normalize_rollout_event_msg(payload)

    # session_meta / turn_context and anything else: no UI payload.
    return [], False


def _normalize_rollout_response_item(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ptype = str(payload.get("type") or "")

    if ptype == "message":
        # Only surface assistant text; developer (system) and user (prompt echo)
        # messages are shown from run metadata, not the transcript.
        if payload.get("role") != "assistant":
            return []
        text = _content_text(payload.get("content"))
        if not text:
            return []
        return [_assistant_text(text, _event_id(payload, "codex-msg"))]

    if ptype in _ROLLOUT_TOOL_CALL_TYPES:
        tool_id = _rollout_tool_id(payload)
        name = _first_text(payload, "name", "tool") or "command"
        return [_tool_use(tool_id, name, _rollout_tool_args(payload))]

    if ptype in _ROLLOUT_TOOL_OUTPUT_TYPES:
        tool_id = _rollout_tool_id(payload)
        output = _coerce_text(payload.get("output")) or ""
        return [_tool_result(tool_id, output, _rollout_output_is_error(payload))]

    # reasoning / web_search_call / tool_search_call / etc.: skipped for v1.
    return []


def _normalize_rollout_event_msg(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    ptype = str(payload.get("type") or "")

    if ptype == "token_count":
        usage = _rollout_token_usage(payload)
        return ([_usage(usage)] if usage else []), False

    if ptype in {"task_complete", "turn_aborted"}:
        # Turn boundary — emit a (empty) result so the tail loop stops.
        return [_result("", is_error=False)], False

    if ptype in {"error", "stream_error", "task_failed", "turn_failed"}:
        auth_failed = _rollout_auth_failed(payload)
        text = (
            AUTH_FAILURE_MESSAGE
            if auth_failed
            else _first_text(payload, "message", "error", "reason", "text") or "Codex run failed."
        )
        return [_result(text, is_error=True)], auth_failed

    # agent_message duplicates the response_item assistant message; task_started,
    # user_message, patch_apply_end, web_search_end, item_completed,
    # context_compacted, thread_* … carry no distinct UI payload.
    return [], False


def _rollout_token_usage(payload: dict[str, Any]) -> dict[str, int] | None:
    """Return one Codex turn's usage, never the conversation-wide total.

    Codex writes both total and last token counts to every rollout record. The
    total spans the durable Codex session, which may contain many Shelter41
    runs. ``last_token_usage`` is the incremental value for this transcript
    event and is therefore the only safe value to add to a run summary.
    """
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    raw = info.get("last_token_usage")
    if not isinstance(raw, dict):
        return None
    mappings = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cached_input_tokens": "cache_read_input_tokens",
    }
    usage: dict[str, int] = {}
    for source, target in mappings.items():
        value = raw.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            usage[target] = int(value)
    return usage or None


def _content_text(content: Any) -> str | None:
    """Join text from a response message ``content`` block list."""
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("output_text", "text", "input_text"):
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts) or None


def _rollout_tool_id(payload: dict[str, Any]) -> str:
    """Pair a tool call with its output on ``call_id``.

    A ``function_call`` carries both ``id`` (``fc_…``) and ``call_id`` (``call_…``);
    the matching ``function_call_output`` carries only ``call_id``. So we must key
    on ``call_id`` first for the pair to line up in the UI.
    """
    call_id = payload.get("call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return _event_id(payload, "codex-tool")


def _rollout_tool_args(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"arguments": raw}
        return parsed if isinstance(parsed, dict) else {"arguments": parsed}
    inp = payload.get("input")
    if isinstance(inp, dict):
        return inp
    return {}


def _rollout_output_is_error(payload: dict[str, Any]) -> bool:
    if payload.get("success") is False:
        return True
    exit_code = payload.get("exit_code")
    return exit_code not in (None, 0)


def _rollout_auth_failed(payload: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(payload.get(k))
        for k in ("type", "message", "error", "reason", "text")
        if payload.get(k)
    )
    return is_auth_failure_text(haystack)
