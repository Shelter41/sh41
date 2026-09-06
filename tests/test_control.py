import threading
import time
import uuid

import httpx
import pytest

from sh41_local.control import ControlPlane
from sh41_local.db import Store
from sh41_local.inference import Ollama
from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec
from test_spec_state import FakeProvider


def wait_for(callback):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = callback()
        if result:
            return result
        time.sleep(0.01)
    pytest.fail("Operation did not settle")


def test_upgrade_preserves_identity_and_interrupts_only_active_jobs(tmp_path):
    store = Store(tmp_path)
    store.reserve(AgentSpec(agent="a", harness="codex"))
    session = store.session("a")
    with store.connect() as conn:
        conn.execute("DROP TABLE operations")
        conn.execute("PRAGMA user_version=1")
    store = Store(tmp_path)
    assert store.session("a") == session
    store.add_operation("old", "start", "a")
    store.add_operation("done", "start", "b")
    store.update_operation("done", "completed")
    store.interrupt_operations()
    assert store.operation("old")["status"] == "interrupted"
    assert store.operation("done")["status"] == "completed"
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_job_outlives_submit_and_is_not_replayed(tmp_path, monkeypatch):
    service = AgentService(tmp_path, FakeProvider())
    service.deploy(AgentSpec(agent="a", harness="codex"))
    control = ControlPlane(service)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def dispatch(payload):
        calls.append(payload["op"])
        entered.set()
        release.wait(5)

    monkeypatch.setattr(service, "dispatch", dispatch)
    payload = {"op": "submit", "kind": "start", "agent": "a", "id": str(uuid.uuid4()),
               "secrets": {"KEY": "private-value"}}
    try:
        assert control.dispatch(payload)["status"] in {"pending", "running"}
        assert entered.wait(2)
        assert control.dispatch(payload)["id"] == payload["id"]
        with pytest.raises(ValueError, match="busy"):
            control.dispatch(dict(payload, id=str(uuid.uuid4())))
        with pytest.raises(ValueError, match="busy"):
            control.dispatch({"op": "pause", "agent": "a"})
        assert control.dispatch({"op": "snapshot"})["agents"][0]["state"] == "unknown"
        release.set()
        wait_for(lambda: service.store.operation(payload["id"])["status"] == "completed")
        assert control.dispatch(payload)["status"] == "completed"
        assert calls == ["start"]
        assert b"private-value" not in service.store.path.read_bytes()
    finally:
        release.set()
        control.close()


def test_snapshots_do_not_mutate_lifecycle_and_slow_probe_is_nonblocking(tmp_path, monkeypatch):
    provider = FakeProvider()
    service = AgentService(tmp_path, provider)
    service.deploy(AgentSpec(agent="a", harness="codex"))
    control = ControlPlane(service)
    monkeypatch.setattr(provider, "require", lambda: None, raising=False)
    release = threading.Event()

    def inspect(*args, **kwargs):
        release.wait(2)
        raise RuntimeError("Docker unavailable")

    monkeypatch.setattr(provider, "inspect", inspect, raising=False)
    try:
        wait_for(lambda: control.snapshot()["docker"].get("state") == "ready")
        started = time.monotonic()
        for _ in range(5):
            row = control.snapshot()["agents"][0]
            assert row["recorded_state"] == "deployed"
        assert time.monotonic() - started < 1
        release.set()
        wait_for(lambda: control.snapshot()["agents"][0].get("observed_at"))
        assert control.snapshot()["agents"][0]["state"] == "unknown"
        assert service.store.deployment("a")["status"] == "deployed"
    finally:
        release.set()
        control.close()


def test_ollama_observation_discovers_without_start_or_state_write(tmp_path, monkeypatch):
    ollama = Ollama(tmp_path, None)
    monkeypatch.setenv("OLLAMA_HOST", "localhost:11434")

    def handle(request):
        assert request.method == "GET"
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:4b", "size": 100}]})
        return httpx.Response(200, json={"models": []})

    monkeypatch.setattr(ollama, "client", lambda url, timeout=2:
                        httpx.Client(base_url=url, transport=httpx.MockTransport(handle)))
    result = ollama.status()
    assert result["state"] == "ready" and not result["owned"]
    assert result["models"][0]["name"] == "local:4b"
    assert result["loaded"] == []
    assert not ollama.state_file.exists()


def test_ollama_partial_failure_is_not_empty_inventory(tmp_path, monkeypatch):
    ollama = Ollama(tmp_path, None)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    def handle(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        return httpx.Response(503)

    monkeypatch.setattr(ollama, "client", lambda url, timeout=2:
                        httpx.Client(base_url=url, transport=httpx.MockTransport(handle)))
    result = ollama.status()
    assert result["models"] is None and result["loaded"] is None
    assert len(result["errors"]) == 2
