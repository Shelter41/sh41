"""Paid-key-free acceptance against actual local weights, opt-in due to downloads."""
import os
import json
import shutil
import subprocess

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
