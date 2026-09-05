"""Opt-in real harness acceptance. No credentials or model access needed by default."""
import json
import os
import shutil
import time
import uuid

import pytest

from sh41_local.credentials import import_native
from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec


def finish(service, agent, message, timeout=240):
    run = service.dispatch({"op": "run", "agent": agent, "message": message})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = service.dispatch({"op": "run-status", "agent": agent, "run_id": run["id"]})
        if result["status"] not in {"pending", "running"}:
            assert result["status"] == "completed", json.dumps(result)
            return result
        time.sleep(0.5)
    service.dispatch({"op": "interrupt", "agent": agent})
    pytest.fail("Harness run did not complete before the acceptance deadline")


@pytest.mark.live
@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_NATIVE") != "1", reason="Set SH41_TEST_NATIVE=1")
@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_native_account_and_session_recovery(tmp_path, harness):
    root = tmp_path / "runtime"
    service = AgentService(root)
    deployments = []
    try:
        import_native(root, harness)
        spec = AgentSpec(agent="acceptance", harness=harness)
        deployments.append(service.deploy(spec))
        marker = "release_" + uuid.uuid4().hex[:8]
        finish(service, "acceptance", f"Our project's release codename is {marker}. Please acknowledge this project detail briefly. No tools are needed.")
        session = service.store.session("acceptance")
        assert session["native_id"]
        service.lifecycle("acceptance", "park")
        deployments.append(service.deploy(spec))
        result = finish(service, "acceptance", "What is our project's release codename? No tools are needed.")
        assert marker in json.dumps(result["events"])
        assert service.store.session("acceptance")["id"] == session["id"]
        service.dispatch({"op": "new-session", "agent": "acceptance"})
        finish(service, "acceptance", "Reply only FRESH. Do not use tools.")
        fresh = service.store.session("acceptance")
        assert fresh["id"] != session["id"]
        assert fresh["native_id"] != session["native_id"]
        service.dispatch({"op": "continue-session", "agent": "acceptance", "session_id": session["id"]})
        result = finish(service, "acceptance", "What is our project's release codename? No tools are needed.")
        assert marker in json.dumps(result["events"])
    finally:
        for deployment in deployments:
            service.provider.remove(deployment)
        shutil.rmtree(root, ignore_errors=True)
