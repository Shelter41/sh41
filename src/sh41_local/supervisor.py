from __future__ import annotations

import fcntl
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

from .paths import private_dir, state_home

MAX_REQUEST = 2 * 1024 * 1024


def socket_path(root: Path) -> str:
    path = str(root / "supervisor.sock")
    if len(path.encode()) > 100:
        raise ValueError("SH41_LOCAL_HOME is too long for a Unix socket; choose a shorter path")
    return path


def connect(root: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(5)
    try:
        sock.connect(socket_path(root))
    except Exception:
        sock.close()
        raise
    return sock


def ensure(root: Path) -> None:
    private_dir(root)
    with (root / "startup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with connect(root):
                return
        except OSError:
            pass
        env = dict(os.environ, SH41_LOCAL_HOME=str(root))
        with (root / "supervisor.log").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-m", "sh41_local.supervisor"],
                                     env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True)
        for _ in range(100):
            try:
                with connect(root):
                    return
            except OSError:
                if child.poll() is not None:
                    break
                time.sleep(0.05)
        raise RuntimeError("Local supervisor failed to start; inspect supervisor.log")


def request(payload: dict, *, root: Path | None = None):
    root = root or state_home()
    ensure(root)
    with connect(root) as sock:
        sock.settimeout(None)
        sock.sendall(json.dumps(payload).encode() + b"\n")
        with sock.makefile("r") as stream:
            line = stream.readline()
        if not line:
            raise RuntimeError("Supervisor disconnected; inspect agent state before retrying")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, root: Path, dispatch):
        self.dispatch = dispatch
        self.agent_locks: dict[str, threading.Lock] = {}
        self.lock_guard = threading.Lock()
        super().__init__(socket_path(root), Handler)

    def agent_lock(self, key):
        with self.lock_guard:
            return self.agent_locks.setdefault(key, threading.Lock())


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        raw = self.rfile.readline(MAX_REQUEST + 1)
        if not raw:
            return
        try:
            if len(raw) > MAX_REQUEST:
                raise ValueError("Request too large")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Expected a request object")
            key = payload.get("agent") or (payload.get("spec") or {}).get("agent") or "_global"
            # Reject concurrent writes instead of invisibly enqueueing another turn.
            lock = self.server.agent_lock(key)
            if not lock.acquire(blocking=False):
                raise ValueError("Agent is busy; wait for its current operation")
            try:
                result = self.server.dispatch(payload)
            finally:
                lock.release()
            response = {"result": result}
        except (ValueError, RuntimeError) as exc:
            response = {"error": str(exc)}
        except Exception:
            response = {"error": "Local operation failed; run sh41 doctor and inspect agent state"}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve():
    from .service import AgentService

    os.umask(0o077)
    root = private_dir(state_home())
    with (root / "supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        path = Path(socket_path(root))
        path.unlink(missing_ok=True)
        service = AgentService(root)
        try:
            service.recover_startup()
        except RuntimeError:
            # Metadata remains accessible while Docker is unavailable.
            pass
        with Server(root, service.dispatch) as server:
            path.chmod(0o600)
            server.serve_forever()


if __name__ == "__main__":
    serve()
