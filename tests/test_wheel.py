"""Exercise the installed CLI and supervisor outside the source checkout."""
import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sh41_local.service import AgentService
from test_opencode import CompletionHandler


@pytest.mark.docker
@pytest.mark.skipif(not os.environ.get("SH41_TEST_WHEEL_PYTHON"), reason="Set SH41_TEST_WHEEL_PYTHON")
def test_installed_wheel_cli_and_supervisor():
    python = os.environ["SH41_TEST_WHEEL_PYTHON"]
    cli = str(Path(python).with_name("sh41"))
    server = ThreadingHTTPServer((os.environ.get("SH41_TEST_BIND", "127.0.0.1"), 0), CompletionHandler)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="s41-wheel-", dir="/tmp") as directory:
            root = Path(directory) / "state"
            env = dict(os.environ, SH41_LOCAL_HOME=str(root))
            env.pop("PYTHONPATH", None)
            service = AgentService(root)
            daemon = subprocess.Popen([python, "-m", "sh41_local.supervisor"], cwd=directory, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if (root / "supervisor.sock").exists():
                        break
                    assert daemon.poll() is None
                    time.sleep(0.05)

                def invoke(*args):
                    result = subprocess.run([cli, *args], cwd=directory, env=env, timeout=600,
                                            capture_output=True, text=True)
                    assert result.returncode == 0, result.stdout + result.stderr
                    return result.stdout

                host = os.environ.get("SH41_TEST_HOST", "host.docker.internal")
                invoke("launch", "wheel", "--harness", "opencode", "--model", "fixture-model",
                       "--base-url", f"http://{host}:{server.server_port}/v1")
                assert (Path(directory) / "wheel.yaml").exists()
                assert json.loads(invoke("agents", "--json"))[0]["slug"] == "wheel"
                assert "SH41_FIXTURE_OK" in invoke("run", "wheel", "--message", "Hello from the wheel")
                assert json.loads(invoke("history", "wheel", "--json"))[0]["status"] == "completed"
                invoke("park", "wheel")
            finally:
                daemon.terminate()
                daemon.wait(timeout=10)
                for agent in service.store.agents():
                    service.provider.remove(service.store.deployment(agent["slug"], latest=True))
    finally:
        server.shutdown()
        server.server_close()
