"""Real macOS/Linux PTYs and Docker, with a deterministic local model endpoint."""
import fcntl
import os
import pty
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, Inference
from sh41_local.supervisor import request
from test_opencode import CompletionHandler


def until(callback, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(0.1)
    pytest.fail("Shell acceptance condition timed out")


@contextmanager
def shell_terminal(env, directory, *, cli=None, bare=False):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    child = subprocess.Popen([cli or str(Path(sys.executable).with_name("sh41")), *([] if bare else ["shell"])],
        cwd=directory, env=dict(env, TERM="xterm-256color"), stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    output = bytearray()

    def read():
        try:
            while data := os.read(master, 65536):
                output.extend(data)
        except OSError:
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        yield child, master, output
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        os.close(master)
        reader.join(timeout=2)
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            (Path(os.environ["SH41_TEST_SCREENSHOTS"]) / "shell-pty.log").write_bytes(output)


@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_DOCKER") != "1", reason="Set SH41_TEST_DOCKER=1")
def test_shell_kill_reopen_native_attach_and_two_agent_persistence():
    http = ThreadingHTTPServer((os.environ.get("SH41_TEST_BIND", "127.0.0.1"), 0), CompletionHandler)
    http.requests = []
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="s41-shell-", dir="/tmp") as directory:
            root = (Path(directory) / "state").resolve()
            env = dict(os.environ, SH41_LOCAL_HOME=str(root))
            service = AgentService(root)
            with (Path(directory) / "daemon.log").open("w") as log:
                daemon = subprocess.Popen([sys.executable, "-m", "sh41_local.supervisor"],
                    cwd=directory, env=env, stdout=log, stderr=log)
                try:
                    until(lambda: (root / "supervisor.sock").exists())

                    def call(payload):
                        return request(payload, root=root, timeout=200)

                    def submit(kind, **fields):
                        ident = str(uuid.uuid4())
                        call({"op": "submit", "id": ident, "kind": kind, **fields})
                        job = until(lambda: (j if (j := call({"op": "operation", "id": ident}))["status"]
                                             not in {"pending", "running"} else None), timeout=600)
                        assert job["status"] == "completed", job

                    host = os.environ.get("SH41_TEST_HOST", "host.docker.internal")
                    for name in ("alpha", "beta"):
                        spec = AgentSpec(agent=name, model="fixture-model", inference=Inference(
                            provider="openai-compatible", base_url=f"http://{host}:{http.server_port}/v1"))
                        submit("deploy-start", spec=spec.model_dump())
                        status = service.provider.rpc(service.store.deployment(name), {"op": "status"})
                        assert status["harness_state"] == "ready" and not status["writer"]
                        assert not status["running"]
                    original_sessions = {name: service.store.session(name)["id"] for name in ("alpha", "beta")}
                    assert not http.requests, "Starting terminals must not submit model prompts"

                    with shell_terminal(env, directory, bare=True) as (child, master, output):
                        until(lambda: b"alpha" in output and b"beta" in output)
                        os.write(master, b"\r")
                        deployment = service.store.deployment("alpha")
                        until(lambda: service.provider.command(["exec", service.provider.name(deployment),
                            "tmux", "list-clients", "-F", "#{client_readonly}"], check=False).stdout.strip() == "0")
                        os.write(master, b"\x02d")
                        until(lambda: b"Detached from alpha" in output)
                        until(lambda: not service.provider.rpc(deployment, {"op": "status"})["writer"])
                        # Resize the real terminal before closing it cleanly.
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
                        os.write(master, b"\x11")
                        assert child.wait(timeout=15) == 0

                    http.delay = 8
                    run = call({"op": "run", "agent": "alpha", "message": "A turn survives shell exit"})
                    with shell_terminal(env, directory) as (child, _, output):
                        until(lambda: b"alpha" in output)
                        child.kill()
                        child.wait(timeout=10)
                    final = until(lambda: (r if (r := call({"op": "run-status", "agent": "alpha",
                        "run_id": run["id"]}))["status"] not in {"pending", "running"} else None))
                    assert final["status"] == "completed"
                    for name in ("alpha", "beta"):
                        assert service.provider.rpc(service.store.deployment(name), {"op": "status"})["harness_state"] == "ready"
                        assert service.store.session(name)["id"] == original_sessions[name]
                    with shell_terminal(env, directory) as (child, master, output):
                        until(lambda: b"alpha" in output and b"beta" in output)
                        os.write(master, b"\x11")
                        assert child.wait(timeout=15) == 0
                    submit("pause", agent="beta")
                    submit("resume", agent="beta")
                    assert service.provider.rpc(service.store.deployment("beta"), {"op": "status"})["harness_state"] == "ready"
                finally:
                    daemon.terminate()
                    daemon.wait(timeout=10)
                    for agent in service.store.agents():
                        service.provider.remove(service.store.deployment(agent["slug"], latest=True))
    finally:
        http.shutdown()
        http.server_close()
