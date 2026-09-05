import json
import tomllib
import uuid

import pytest

from sh41_local.credentials import import_native
from sh41_local.drivers.claude_jsonl import read_new_lines, tail_until_idle
from sh41_local.drivers.codex_driver import build_interactive_argv
from sh41_local.drivers.claude_driver import ClaudeDriver
from sh41_local.drivers.claude_jsonl import project_dir_for_cwd
from sh41_local.harness_config import configure, redact
from sh41_local.paths import atomic_json
from sh41_local.spec import AgentSpec, Inference, MCP, SecretRef
from sh41_local.worker import Manager


@pytest.mark.parametrize("harness", ["claude-code", "codex", "opencode"])
def test_native_config_mcp_and_secret_locations(tmp_path, harness):
    spec = AgentSpec(agent="test", harness=harness, model="test-model",
        inference=Inference(provider="openai-compatible" if harness == "opencode" else "native",
                            base_url="http://example.com/v1" if harness == "opencode" else None,
                            api_key=SecretRef(env="MODEL_KEY")),
        mcp={"local": MCP(transport="stdio", command=["node", "server.js"],
                          env={"TOKEN": SecretRef(env="TOOL_KEY")}),
             "remote": MCP(transport="http", url="http://example.com/mcp",
                           headers={"Authorization": SecretRef(env="TOOL_KEY")})})
    env = configure(tmp_path / "home", tmp_path / "control", {"spec": spec.model_dump(),
                    "secrets": {"MODEL_KEY": "model-secret", "TOOL_KEY": "tool-secret"}})
    assert "model-secret" not in spec.as_yaml()
    if harness == "codex":
        config = tomllib.loads((tmp_path / "home/.codex/config.toml").read_text())
        assert config["mcp_servers"]["local"]["args"] == ["server.js"]
        assert config["mcp_servers"]["remote"]["http_headers"]["Authorization"] == "tool-secret"
        assert env["OPENAI_API_KEY"] == "model-secret"
    elif harness == "claude-code":
        config = json.loads((tmp_path / "home/.claude/.claude.json").read_text())
        assert config["mcpServers"]["local"]["command"] == "node"
        assert env["ANTHROPIC_API_KEY"] == "model-secret"
    else:
        config = json.loads((tmp_path / "home/.config/opencode/opencode.json").read_text())
        assert config["mcp"]["remote"]["oauth"] is False
        assert config["provider"]["local"]["options"]["apiKey"] == "model-secret"


def test_import_is_explicit_restricted_and_rejects_invalid(tmp_path):
    file = tmp_path / "native.json"
    file.write_text(json.dumps({"tokens": {"access_token": "private"}}))
    import_native(tmp_path / "state", "codex", file)
    dest = tmp_path / "state/credentials/codex.json"
    assert dest.stat().st_mode & 0o777 == 0o600
    file.write_text("{secret: broken}")
    with pytest.raises(ValueError, match="Invalid native"):
        import_native(tmp_path / "state", "codex", file)


def test_refresh_does_not_overwrite_native_rotated_credentials(tmp_path):
    spec = AgentSpec(agent="a", harness="codex")
    payload = {"spec": spec.model_dump(), "native": {"tokens": {"access_token": "original"}}}
    configure(tmp_path / "home", tmp_path / "control", payload)
    path = tmp_path / "home/.codex/auth.json"
    path.write_text(json.dumps({"tokens": {"access_token": "rotated"}}))
    configure(tmp_path / "home", tmp_path / "control", payload)
    assert "rotated" in path.read_text()
    payload["native"] = {"tokens": {"access_token": "reimported"}}
    configure(tmp_path / "home", tmp_path / "control", payload)
    assert "reimported" in path.read_text()


def test_redaction():
    value = {"message": "password-value https://user:pass@example.com/x sk-abcdefghijklmnop"}
    result = redact(value, ["password-value"])
    assert "password-value" not in result["message"]
    assert "user:pass" not in result["message"]
    assert "sk-" not in result["message"]


def test_partial_transcript_is_not_emitted_twice(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"text":"\xc3')
    assert read_new_lines(path, 0) == ([], 0)
    with path.open("ab") as stream:
        stream.write(b'\xa9"}\n')
    rows, offset = read_new_lines(path, 0)
    assert json.loads(rows[0])["text"] == "\u00e9"
    assert read_new_lines(path, offset) == ([], offset)


def test_idle_is_not_success(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text('{"type":"assistant"}\n')
    events = []
    tail_until_idle(path, 0, events.append, parse_line=lambda line: [json.loads(line)],
                    is_complete=lambda event: False, turn_idle_sec=0, max_wait_sec=1)
    assert events[-1]["uncertain"]
    assert events[-1]["is_error"]


def test_new_claude_session_never_tails_previous_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    directory = project_dir_for_cwd("/workspace/agent")
    directory.mkdir(parents=True)
    old = directory / "previous.jsonl"
    old.write_text('{"type":"assistant"}\n')
    state = {"session_id": "new-session", "cwd": "/workspace/agent"}
    assert ClaudeDriver.resolve_transcript(state) is None
    expected = directory / "new-session.jsonl"
    expected.write_text("")
    assert ClaudeDriver.resolve_transcript(state) == expected


def test_codex_resume_uses_exact_session():
    argv = build_interactive_argv(permission_mode="bypassPermissions", model="test",
                                  resume_session_id="session-123")
    assert argv[-2:] == ["resume", "session-123"]
    assert "--last" not in argv


def test_worker_restart_marks_unfinished_run_interrupted(tmp_path):
    ident = str(uuid.uuid4())
    path = tmp_path / "runs" / ident / "status.json"
    atomic_json(path, {"id": ident, "status": "running", "native_id": "persisted"})
    manager = Manager(tmp_path)
    result = manager.dispatch({"op": "run-status", "run_id": ident})
    assert result["status"] == "interrupted"
    assert result["native_id"] == "persisted"


def test_worker_rejects_duplicate_submission(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    monkeypatch.setattr(manager, "writer", lambda: False)
    ident = str(uuid.uuid4())
    atomic_json(manager.run_dir(ident) / "status.json", {"status": "completed"})
    with pytest.raises(ValueError, match="not be replayed"):
        manager.dispatch({"op": "run", "run_id": ident, "session_id": str(uuid.uuid4())})


def test_provider_refusal_and_auth_errors_are_terminal():
    from sh41_local.drivers.claude_jsonl import is_turn_complete
    assert is_turn_complete({"type": "assistant", "message": {"stop_reason": "refusal"}})
    assert is_turn_complete({"type": "assistant", "isApiErrorMessage": True, "message": {}})


def test_detached_codex_native_turn_is_busy(tmp_path):
    from types import SimpleNamespace
    manager = Manager(tmp_path)
    manager.spec = AgentSpec(agent="test", harness="codex")
    manager.runtime = SimpleNamespace(lookup=lambda key: "handle",
                                     inspect=lambda handle: SimpleNamespace(active=True, output=""))
    path = tmp_path / "rollout.jsonl"
    manager.driver = SimpleNamespace(resolve_transcript=lambda state, cwd: path)
    path.write_text('{"type":"event_msg","payload":{"type":"task_started"}}\n')
    assert manager.busy()
    with path.open("a") as stream:
        stream.write('{"type":"event_msg","payload":{"type":"task_complete"}}\n')
    assert not manager.busy()
