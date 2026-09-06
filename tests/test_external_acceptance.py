"""Opt-in release gates for credentials/endpoints supplied by the operator."""
import json
import os
import shutil
import time

import pytest

from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, Inference, SecretRef, Source


def required(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"Enabled acceptance test requires {name}; set it locally, not in test output")
    return value


def turn(service, agent, message, secrets, expected="completed", timeout=300):
    run = service.dispatch({"op": "run", "agent": agent, "message": message, "secrets": secrets})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = service.sync_run(agent, run["id"])
        if result["status"] not in {"pending", "running"}:
            # Do not put provider payloads or real keys into assertion diagnostics.
            if expected is not None:
                assert result["status"] == expected
            return result
        time.sleep(0.5)
    service.dispatch({"op": "interrupt", "agent": agent})
    pytest.fail("External-provider turn exceeded the acceptance deadline")


@pytest.mark.live
@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_KEY_REJECTION") != "1", reason="Set SH41_TEST_KEY_REJECTION=1")
@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_invalid_api_key_is_rejected_clearly(tmp_path, harness):
    service = AgentService(tmp_path / "runtime")
    deployment = None
    try:
        spec = AgentSpec(agent="invalid-key", harness=harness,
                         inference=Inference(api_key=SecretRef(env="INVALID_TEST_KEY")))
        secrets = {"INVALID_TEST_KEY": "sh41-invalid-acceptance-key"}
        deployment = service.deploy(spec, secrets)
        result = turn(service, spec.agent, "Reply with a brief greeting.", secrets, expected=None)
        errors = json.dumps(result["events"]).lower()
        assert result["status"] == "failed", errors
        assert any(word in errors for word in ("401", "invalid api key", "invalid_api_key", "authentication")), errors
    finally:
        if deployment:
            service.provider.remove(deployment)
        shutil.rmtree(service.root, ignore_errors=True)


@pytest.mark.live
@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_API_KEYS") != "1", reason="Set SH41_TEST_API_KEYS=1")
@pytest.mark.parametrize("harness,key_name,model_name", [
    ("codex", "OPENAI_API_KEY", "SH41_TEST_CODEX_MODEL"),
    ("claude-code", "ANTHROPIC_API_KEY", "SH41_TEST_CLAUDE_MODEL"),
])
def test_api_key_authentication_and_recovery(tmp_path, harness, key_name, model_name):
    secret = required(key_name)
    service = AgentService(tmp_path / "runtime")
    deployment = None
    try:
        spec = AgentSpec(agent="api-key", harness=harness, model=os.environ.get(model_name),
                         inference=Inference(api_key=SecretRef(env=key_name)))
        deployment = service.deploy(spec, {key_name: secret})
        assert not (service.root / "credentials").exists()
        first = turn(service, spec.agent, "Reply with a brief greeting. No tools needed.", {key_name: secret})
        rejected = turn(service, spec.agent, "Reply with a brief greeting. No tools needed.",
                        {key_name: "sh41-invalid-acceptance-key"}, expected="failed")
        errors = json.dumps(rejected["events"]).lower()
        assert any(word in errors for word in ("auth", "credential", "api key", "401", "unauthorized"))
        recovered = turn(service, spec.agent, "Reply with a brief greeting. No tools needed.", {key_name: secret})
        public = spec.as_yaml() + json.dumps([first["events"], rejected["events"], recovered["events"]])
        if secret in public:
            pytest.fail("API credential leaked into manifest or normalized events")
    finally:
        if deployment:
            service.provider.remove(deployment)
        shutil.rmtree(service.root, ignore_errors=True)


@pytest.mark.live
@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_REMOTE") != "1", reason="Set SH41_TEST_REMOTE=1")
def test_remote_compatible_model_edits_and_runs_tests(tmp_path):
    url, model = required("SH41_TEST_REMOTE_URL"), required("SH41_TEST_REMOTE_MODEL")
    key_name = os.environ.get("SH41_TEST_REMOTE_KEY_ENV")
    secrets = {key_name: required(key_name)} if key_name else {}
    source = tmp_path / "source"
    source.mkdir()
    (source / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (source / "test_calculator.py").write_text(
        "import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
    service = AgentService(tmp_path / "runtime")
    deployment = None
    try:
        spec = AgentSpec(agent="remote-edit", model=model, source=Source(path=str(source)),
            inference=Inference(provider="openai-compatible", base_url=url,
                                api_key=SecretRef(env=key_name) if key_name else None))
        deployment = service.deploy(spec, secrets)
        result = turn(service, spec.agent,
            "Fix calculator.py so add(a, b) returns a + b. Apply the file edit, then use bash "
            "to run python -m unittest -v. Do not only describe what to do.", secrets, timeout=600)
        commands = [part for event in result["events"]
                    if isinstance(event.get("message"), dict)
                    for part in event["message"].get("parts", [])
                    if part.get("type") == "tool" and part.get("tool") == "bash"]
        assert any(part.get("state", {}).get("status") == "completed" and
                   "unittest" in part.get("state", {}).get("input", {}).get("command", "")
                   for part in commands)
        checked = service.provider.command(["exec", service.provider.name(deployment),
                                             "python", "-m", "unittest", "-v"], check=False)
        assert checked.returncode == 0
        assert "a - b" in (source / "calculator.py").read_text()
        public = spec.as_yaml() + json.dumps(result["events"])
        if any(secret in public for secret in secrets.values()):
            pytest.fail("Provider credential leaked into manifest or normalized events")
    finally:
        if deployment:
            service.provider.remove(deployment)
        shutil.rmtree(service.root, ignore_errors=True)
