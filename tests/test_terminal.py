"""Real Docker/tmux PTY behavior, without a paid model account."""
import fcntl
import json
import os
import pty
import shutil
import struct
import subprocess
import termios
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from sh41_local.docker import docker_binary
from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, Inference
from test_live import finish
from test_opencode import CompletionHandler


@contextmanager
def terminal(attachment):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    child = subprocess.Popen([docker_binary(), "exec", "-it", "-e", "TERM=xterm-256color",
        attachment["container"], "python", "-m", "sh41_local.worker", "attach",
        attachment["token"], json.dumps(attachment["argv"])], stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    received = bytearray()

    def drain():
        try:
            while data := os.read(master, 65536):
                received.extend(data)
        except OSError:
            pass

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        yield child, master, received
    finally:
        if child.poll() is None:
            os.write(master, b"\x02d")
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
        os.close(master)
        reader.join(timeout=2)


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    pytest.fail("Terminal state did not settle")


@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_DOCKER") != "1", reason="Set SH41_TEST_DOCKER=1")
def test_attach_detach_writer_exclusion_and_interrupt(tmp_path):
    server = ThreadingHTTPServer((os.environ.get("SH41_TEST_BIND", "127.0.0.1"), 0), CompletionHandler)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = AgentService(tmp_path / "runtime")
    deployment = None
    try:
        host = os.environ.get("SH41_TEST_HOST", "host.docker.internal")
        spec = AgentSpec(agent="terminal", model="fixture-model", inference=Inference(
            provider="openai-compatible", base_url=f"http://{host}:{server.server_port}/v1"))
        deployment = service.deploy(spec)
        attachment = service.dispatch({"op": "attach", "agent": spec.agent})
        assert not attachment["readonly"]
        with terminal(attachment) as (child, master, received):
            wait_for(lambda: len(received) > 100)
            assert child.poll() is None
            with pytest.raises(RuntimeError, match="active run|writable attachment"):
                service.dispatch({"op": "run", "agent": spec.agent, "message": "blocked"})
            second = service.dispatch({"op": "attach", "agent": spec.agent})
            assert second["readonly"]
            os.write(master, b"\x02d")
            assert child.wait(timeout=10) == 0
        wait_for(lambda: not service.provider.rpc(deployment, {"op": "status"})["writer"])
        finish(service, spec.agent, "Still running after detach?")
        server.delay = 15
        run = service.dispatch({"op": "run", "agent": spec.agent, "message": "slow turn"})
        observer = service.dispatch({"op": "attach", "agent": spec.agent})
        assert observer["readonly"]
        with terminal(observer) as (_, _, received):
            wait_for(lambda: len(received) > 100)
            service.dispatch({"op": "interrupt", "agent": spec.agent})
            wait_for(lambda: service.sync_run(spec.agent, run["id"])["status"] == "cancelled", timeout=30)
        assert service.provider.inspect(deployment)["State"]["Running"]
        original = service.store.session(spec.agent)["native_id"]
        run = service.dispatch({"op": "run", "agent": spec.agent, "message": "restart during turn"})
        service.provider.command(["restart", "--time", "1", service.provider.name(deployment)])
        service.provider.wait_ready(deployment)
        assert service.sync_run(spec.agent, run["id"])["status"] == "interrupted"
        server.delay = 0
        finish(service, spec.agent, "Continue after restart.")
        assert service.store.session(spec.agent)["native_id"] == original
    finally:
        if deployment:
            service.provider.remove(deployment)
        server.shutdown()
        server.server_close()
        shutil.rmtree(service.root, ignore_errors=True)
