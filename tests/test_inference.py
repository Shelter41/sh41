import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from sh41_local.db import Store
from sh41_local.inference import Ollama
from sh41_local.paths import atomic_json
from sh41_local.spec import AgentSpec, Inference


def fake_ollama(tmp_path, monkeypatch, *, capabilities=None, remote=False):
    service = Ollama(tmp_path, None)
    state = {"url": "http://127.0.0.1:12345", "owned": False}
    monkeypatch.setattr(service, "_ensure", lambda: state)
    calls, installed = [], set()

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in installed]})
        if request.url.path == "/api/pull":
            installed.add(json.loads(request.content)["model"])
            return httpx.Response(200, text='{"status":"downloading","completed":10,"total":10}\n{"status":"success"}\n')
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": capabilities if capabilities is not None else ["tools"],
                                            "remote_host": "https://example.com" if remote else None})
        raise AssertionError(request.url.path)

    monkeypatch.setattr(service, "client", lambda url, timeout=10: httpx.Client(base_url=url, transport=httpx.MockTransport(handle)))
    return service, calls


def test_model_pulls_are_serialized_and_cached(tmp_path, monkeypatch):
    service, calls = fake_ollama(tmp_path, monkeypatch)
    progress = []
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda _: service.pull("test:4b", progress.append), range(3)))
    assert len(results) == 3
    assert calls.count("/api/pull") == 1
    assert progress[-1]["status"] == "success"
    assert (tmp_path / "inference/progress.json").exists()


@pytest.mark.parametrize("capabilities,remote", [([], False), (["tools"], True)])
def test_rejects_non_agent_or_remote_models(tmp_path, monkeypatch, capabilities, remote):
    service, _ = fake_ollama(tmp_path, monkeypatch, capabilities=capabilities, remote=remote)
    with pytest.raises(ValueError):
        service.pull("model")


def test_stop_does_not_signal_external_or_reused_pid(tmp_path, monkeypatch):
    service = Ollama(tmp_path, None)
    atomic_json(service.state_file, {"owned": False, "url": "http://localhost:11434"})
    with pytest.raises(ValueError, match="externally managed"):
        service.stop()
    atomic_json(service.state_file, {"owned": True, "pid": 123, "signature": "original"})
    monkeypatch.setattr("sh41_local.inference.signature", lambda pid: "different")
    with pytest.raises(ValueError, match="reused PID"):
        service.stop()


def test_stop_refuses_active_agent_and_retains_models(tmp_path, monkeypatch):
    service = Ollama(tmp_path, None)
    atomic_json(service.state_file, {"owned": True, "pid": 123, "signature": "original"})
    store = Store(tmp_path)
    spec = AgentSpec(agent="local", model="model", inference=Inference(provider="ollama"))
    row, _ = store.reserve(spec)
    with pytest.raises(ValueError, match="Pause or park"):
        service.stop()
    store.set_deployment(row["id"], "paused")
    cache = tmp_path / "inference/models"
    cache.mkdir()
    (cache / "weights").write_text("cached")
    monkeypatch.setattr("sh41_local.inference.signature", lambda pid: "")
    assert service.stop()["models_retained"]
    assert (cache / "weights").read_text() == "cached"


def test_container_host_routing(tmp_path, monkeypatch):
    service = Ollama(tmp_path, None)
    monkeypatch.setattr("sh41_local.inference.platform.system", lambda: "Darwin")
    assert service.container_url({"url": "http://127.0.0.1:1234"}) == "http://host.docker.internal:1234/v1"
    monkeypatch.setattr("sh41_local.inference.platform.system", lambda: "Linux")
    with pytest.raises(ValueError, match="loopback-only"):
        service.container_url({"url": "http://127.0.0.1:1234"})
    assert service.container_url({"url": "http://172.30.0.1:1234"}) == "http://172.30.0.1:1234/v1"
    assert service.container_url({"url": "https://models.example.com"}) == "https://models.example.com:443/v1"


def test_model_list_discovers_external_server_without_saved_state(tmp_path, monkeypatch):
    service = Ollama(tmp_path, None)
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    def handle(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        return httpx.Response(200, json={"models": [{"name": "local:4b"}]})

    monkeypatch.setattr(service, "client", lambda url, timeout=10: httpx.Client(base_url=url, transport=httpx.MockTransport(handle)))
    assert service.models() == [{"name": "local:4b"}]
    assert not service.state_file.exists()


@pytest.mark.parametrize("state,match", [("unreachable", "unreachable"), ("ready", "inventory")])
def test_model_list_does_not_disguise_unavailable_inventory_as_empty(tmp_path, monkeypatch, state, match):
    service = Ollama(tmp_path, None)
    monkeypatch.setattr(service, "status", lambda: {"state": state, "url": "http://localhost:11434", "models": None})
    with pytest.raises(ValueError, match=match):
        service.models()
