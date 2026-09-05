"""Paid-key-free acceptance against actual local weights, opt-in due to downloads."""
import os
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from sh41_local.inference import Ollama
from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, Inference, Source
from test_live import finish


@pytest.mark.live
@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_OLLAMA") != "1", reason="Set SH41_TEST_OLLAMA=1")
def test_local_model_edits_and_tests_private_copy(tmp_path, monkeypatch):
    if os.environ.get("SH41_TEST_OLLAMA_URL"):
        monkeypatch.setenv("OLLAMA_HOST", os.environ["SH41_TEST_OLLAMA_URL"])
    source = tmp_path / "source"
    source.mkdir()
    (source / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (source / "test_calculator.py").write_text(
        "import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        "if __name__ == '__main__':\n    unittest.main()\n")
    service = AgentService(tmp_path / "runtime")
    deployments = []
    try:
        spec = AgentSpec(agent="local-edit", model=os.environ.get("SH41_TEST_MODEL", "qwen3:4b-instruct"),
            source=Source(provider="local", path=str(source)), inference=Inference(provider="ollama"))
        deployments.append(service.deploy(spec))
        result = finish(service, spec.agent,
            "Fix calculator.py: add(a, b) must return a + b instead of a - b. "
            "Use your file-editing tool to make this exact change, then use bash to run "
            "python -m unittest -v. Do not only describe the change. /no_think", timeout=600)
        work = service.root / "agents" / service.store.agent(spec.agent)["id"] / "work"
        assert subprocess.run(["python3", "-m", "unittest", "-v"], cwd=work,
                              capture_output=True).returncode == 0, json.dumps(result["events"])
        assert "a - b" in (source / "calculator.py").read_text()
        assert any(part.get("type") == "tool" for event in result["events"]
                   for part in (event.get("message", {}).get("parts", [])
                                if isinstance(event.get("message"), dict) else []))
        before = Ollama(service.root, service.provider).saved()
        service.lifecycle(spec.agent, "pause")
        service.lifecycle(spec.agent, "resume")
        assert Ollama(service.root, service.provider).saved() == before
        if os.environ.get("SH41_TEST_OFFLINE") == "1":
            verify_offline(service, spec, deployments, monkeypatch)
    finally:
        for deployment in deployments:
            service.provider.remove(deployment)
        ollama = Ollama(service.root, service.provider)
        if ollama.saved().get("owned"):
            # Removal above ended compute; reflect that before ownership-aware stop.
            for deployment in deployments:
                service.store.set_deployment(deployment["id"], "parked", end=True)
            ollama.stop()
        shutil.rmtree(service.root, ignore_errors=True)


def verify_offline(service, spec, deployments, monkeypatch):
    provider = service.provider
    command = provider.command
    network = "sh41-test-offline-" + uuid.uuid4().hex[:10]
    proxy = network + "-model"
    original_session = service.store.session(spec.agent)["native_id"]
    upstream = Ollama(service.root, provider).container_url(Ollama(service.root, provider).saved())
    command(["network", "create", "--internal", "--label", f"sh41.owner={provider.owner}", network])
    try:
        command(["run", "-d", "--name", proxy, "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--add-host=host.docker.internal:host-gateway", "--label", f"sh41.owner={provider.owner}",
            "-e", f"MODEL_UPSTREAM={upstream}", provider.ensure_image("opencode"),
            "python", "-c", Path(__file__).with_name("offline_proxy.py").read_text()])
        command(["network", "connect", network, proxy])
        info = json.loads(command(["inspect", proxy]).stdout)[0]
        address = info["NetworkSettings"]["Networks"][network]["IPAddress"]
        service.lifecycle(spec.agent, "park")

        def isolated(args, **kwargs):
            if args[0] == "run":
                args = [args[0], "--network", network, *args[1:]]
            return command(args, **kwargs)

        monkeypatch.setattr(provider, "command", isolated)
        offline = spec.model_copy(update={"inference": Inference(
            provider="openai-compatible", base_url=f"http://{address}:8080/v1")})
        deployments.append(service.deploy(offline))
        deployment = deployments[-1]
        probe = command(["exec", provider.name(deployment), "python", "-c",
            "import httpx; httpx.get('https://example.com', timeout=3, trust_env=False)"], check=False)
        assert probe.returncode != 0, "Test agent unexpectedly has internet access"
        work = service.root / "agents" / service.store.agent(spec.agent)["id"] / "work"
        (work / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        finish(service, spec.agent,
            "calculator.py has regressed to subtraction. Edit it to return a + b, "
            "then run python -m unittest -v with bash. Apply the change, do not only describe it.", timeout=600)
        assert subprocess.run(["python3", "-m", "unittest", "-v"], cwd=work,
                              capture_output=True).returncode == 0
        assert service.store.session(spec.agent)["native_id"] == original_session
    finally:
        monkeypatch.setattr(provider, "command", command)
        for deployment in deployments:
            provider.remove(deployment)
        command(["rm", "-f", proxy], check=False)
        command(["network", "rm", network], check=False)
