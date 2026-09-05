import concurrent.futures
import json
import socket
import threading

import pytest
import yaml
from click.testing import CliRunner

from sh41_local.cli import main
from sh41_local.db import Store
from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, parse_yaml
from sh41_local.supervisor import Server, socket_path


def native(name="atlas"):
    return AgentSpec(agent=name, harness="claude-code")


def test_flags_roundtrip_and_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MY_KEY", "never-write-this")
    result = CliRunner().invoke(main, ["launch", "reviewer", "--base-url", "https://example.com/v1",
                                     "--model", "test", "--api-key-env", "MY_KEY", "--write-only"])
    assert result.exit_code == 0, result.output
    text = (tmp_path / "reviewer.yaml").read_text()
    assert "never-write-this" not in text
    parsed = parse_yaml(text)
    assert parsed == parse_yaml(parsed.as_yaml())
    assert parsed.inference.api_key.env == "MY_KEY"
    result = CliRunner().invoke(main, ["launch", "reviewer", "--harness", "codex", "--write-only"])
    assert result.exit_code != 0
    assert (tmp_path / "reviewer.yaml").read_text() == text


@pytest.mark.parametrize("text", [
    "agent: a\nagent: b\nharness: codex",
    "agent: ../bad\nharness: codex",
    "agent: a\nharness: codex\nextra: true",
    "agent: a\nharness: opencode",
    "agent: a\nharness: codex\nsandbox: {provider: e2b}",
    "agent: a\nharness: codex\ninference: {base_url: 'https://user:SECRET@foo/v1'}",
    "!!python/object/apply:os.system ['false']",
])
def test_invalid_manifests(text):
    with pytest.raises(ValueError) as exc:
        parse_yaml(text)
    assert "SECRET" not in str(exc.value)


def test_invalid_flags_have_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["launch", "x", "--ollama", "--base-url", "https://foo"])
    assert result.exit_code == 2
    assert list(tmp_path.iterdir()) == []


def test_relative_paths(tmp_path):
    (tmp_path / "repo").mkdir()
    spec = parse_yaml("agent: a\nharness: codex\nsource: {provider: local, path: repo}")
    assert spec.resolved(tmp_path).source.path == str(tmp_path / "repo")


def test_concurrent_reservation_and_reopen(tmp_path):
    store = Store(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: store.reserve(native()), range(16)))
    assert sum(created for _, created in results) == 1
    assert len({row["id"] for row, _ in results}) == 1
    reopened = Store(tmp_path)
    assert len(reopened.agents()) == 1
    with pytest.raises(ValueError, match="immutable"):
        reopened.reserve(AgentSpec(agent="atlas", harness="codex"))
    with pytest.raises(ValueError, match="another spec"):
        reopened.reserve(native().model_copy(update={"model": "different"}))


def test_sessions_and_single_run(tmp_path):
    store = Store(tmp_path)
    store.reserve(native())
    first = store.session("atlas")
    run = store.start_run("atlas")
    with pytest.raises(ValueError, match="active run"):
        store.start_run("atlas")
    with pytest.raises(ValueError, match="during a run"):
        store.session("atlas", new=True)
    store.finish_run(run["id"], "completed", "native-1")
    second = store.session("atlas", new=True)
    assert first["id"] != second["id"]
    assert store.session("atlas", resume=first["id"])["native_id"] == "native-1"
    store.event(run["id"], 0, {"type": "done"})
    store.event(run["id"], 0, {"type": "duplicate"})
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


class FakeProvider:
    def __init__(self, fail=False):
        self.created = []
        self.removed = []
        self.fail = fail

    def create(self, spec, deployment, secrets):
        self.created.append(deployment["id"])
        if self.fail:
            raise RuntimeError("Fake failed")
        return "fake-container"

    def remove(self, deployment):
        self.removed.append(deployment["id"])


def test_service_idempotency_and_failure(tmp_path):
    provider = FakeProvider()
    service = AgentService(tmp_path, provider)
    assert service.deploy(native())["status"] == "deployed"
    service.deploy(native())
    assert len(provider.created) == 1
    provider.fail = True
    with pytest.raises(RuntimeError):
        service.deploy(native("other"))
    assert len(provider.removed) == 1
    assert service.store.deployment("other", latest=True)["ended_at"]
    assert service.store.deployment("atlas")["status"] == "deployed"


def test_supervisor_socket_and_busy(tmp_path):
    # macOS sockaddr_un has a small path limit; pytest tmp paths can exceed it.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="s41-", dir="/tmp") as temporary:
        root = Path(temporary)
        entered, release = threading.Event(), threading.Event()

        def dispatch(payload):
            entered.set()
            release.wait(3)
            return "ok"

        with Server(root, dispatch) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with socket.socket(socket.AF_UNIX) as first, socket.socket(socket.AF_UNIX) as second:
                first.connect(socket_path(root))
                first.sendall(b'{"agent":"x"}\n')
                assert entered.wait(2)
                second.connect(socket_path(root))
                second.sendall(b'{"agent":"x"}\n')
                assert "busy" in json.loads(second.recv(4096))["error"]
                release.set()
                assert json.loads(first.recv(4096))["result"] == "ok"
            server.shutdown()
            thread.join()


def test_mcp_transport_and_secrets():
    data = native().model_dump()
    data["mcp"] = {"tools": {"transport": "http", "url": "https://tools.example/mcp",
                              "headers": {"Authorization": {"env": "TOOLS_TOKEN"}}}}
    spec = parse_yaml(yaml.safe_dump(data))
    assert spec.secret_names() == {"TOOLS_TOKEN"}
    data["mcp"]["tools"]["command"] = ["bad"]
    with pytest.raises(ValueError):
        parse_yaml(yaml.safe_dump(data))
