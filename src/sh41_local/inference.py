from __future__ import annotations

import fcntl
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .db import Store
from .paths import atomic_json, private_dir
from .spec import parse_yaml


def ollama_binary():
    candidates = [os.environ.get("SH41_OLLAMA_BIN"), shutil.which("ollama"),
                  str(Path.home() / ".local/bin/ollama"),
                  "/Applications/Ollama.app/Contents/Resources/ollama"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise ValueError("Ollama is not installed; install it or set SH41_OLLAMA_BIN to its executable")


def signature(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


class Ollama:
    def __init__(self, root: Path, docker):
        self.root = root
        self.directory = private_dir(root / "inference")
        self.state_file = self.directory / "ollama.json"
        self.docker = docker

    @contextmanager
    def locked(self):
        with (self.directory / "ollama.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def saved(self):
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def client(self, url, timeout=10):
        return httpx.Client(base_url=url, timeout=timeout, trust_env=False)

    def healthy(self, url):
        try:
            with self.client(url, timeout=2) as client:
                response = client.get("/api/version")
                return response.is_success and bool(response.json().get("version"))
        except (httpx.HTTPError, ValueError):
            return False

    def ensure(self):
        with self.locked():
            return self._ensure()

    def _ensure(self):
        state = self.saved()
        external = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        if not external.startswith(("http://", "https://")):
            external = "http://" + external
        parsed = urlsplit(external)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("OLLAMA_HOST must be an HTTP(S) origin without credentials or a path")
        if state.get("url") and ("OLLAMA_HOST" not in os.environ or state["url"] == external.rstrip("/")):
            if self.healthy(state["url"]):
                if state.get("owned") and signature(state["pid"]) != state["signature"]:
                    state = {"url": state["url"], "owned": False, "network": state.get("network")}
                    atomic_json(self.state_file, state)
                return state
        if state.get("owned") and "OLLAMA_HOST" in os.environ:
            raise ValueError("Stop the sh41-owned Ollama service before changing OLLAMA_HOST")
        if self.healthy(external):
            state = {"url": external.rstrip("/"), "owned": False}
            atomic_json(self.state_file, state)
            return state
        if "OLLAMA_HOST" in os.environ:
            raise ValueError("Configured OLLAMA_HOST is unreachable; start that service or unset it")
        binary = ollama_binary()
        host = "127.0.0.1"
        network = None
        if platform.system() == "Linux":
            network, host = self.docker.inference_network()
        with socket.socket() as reservation:
            reservation.bind((host, 0))
            port = reservation.getsockname()[1]
        url = f"http://{host}:{port}"
        model_directory = private_dir(self.directory / "models")
        env = dict(os.environ, OLLAMA_HOST=f"{host}:{port}", OLLAMA_MODELS=str(model_directory),
                   OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1",
                   OLLAMA_CONTEXT_LENGTH="16384", OLLAMA_NO_CLOUD="1")
        with (self.directory / "ollama.log").open("ab") as log:
            child = subprocess.Popen([binary, "serve"], env=env, stdout=log, stderr=log,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        state = {"url": url, "owned": True, "pid": child.pid, "signature": signature(child.pid),
                 "binary": binary, "network": network}
        atomic_json(self.state_file, state)
        for _ in range(120):
            if self.healthy(url):
                return state
            if child.poll() is not None:
                raise RuntimeError("Ollama exited during startup; inspect inference/ollama.log")
            time.sleep(0.25)
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
        raise RuntimeError("Ollama did not become ready within 30 seconds")

    def pull(self, model, progress=None):
        if not model or "cloud" in model.lower() or any(c.isspace() for c in model):
            raise ValueError("Choose a local Ollama model tag, not a cloud model")
        with self.locked():
            state = self._ensure()
            with self.client(state["url"], timeout=httpx.Timeout(60, read=300)) as client:
                tags = client.get("/api/tags")
                tags.raise_for_status()
                existing = {row["name"] for row in tags.json().get("models", [])}
                if model not in existing and f"{model}:latest" not in existing:
                    started = time.monotonic()
                    with client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as response:
                        if not response.is_success:
                            raise ValueError("Model download failed; verify the model tag and network access")
                        for line in response.iter_lines():
                            if time.monotonic() - started > 1800:
                                raise RuntimeError("Model download exceeded 30 minutes; retry models pull to reuse cached parts")
                            if not line:
                                continue
                            item = json.loads(line)
                            if item.get("error"):
                                raise RuntimeError("Model download failed; verify model availability and free disk space")
                            public = {key: item[key] for key in ("status", "completed", "total") if key in item}
                            atomic_json(self.directory / "progress.json", public)
                            if progress:
                                progress(public)
                shown = client.post("/api/show", json={"model": model})
                shown.raise_for_status()
                details = shown.json()
                if details.get("remote_host") or details.get("remote_model"):
                    raise ValueError("This model delegates inference to a remote service; choose local weights")
                if "tools" not in details.get("capabilities", []):
                    raise ValueError("The selected Ollama model does not advertise tool support")
            return state

    def models(self):
        state = self.saved()
        if not state.get("url") or not self.healthy(state["url"]):
            return []
        with self.client(state["url"]) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            return response.json().get("models", [])

    def container_url(self, state):
        parts = urlsplit(state["url"])
        host = parts.hostname
        if platform.system() == "Darwin" and host in {"localhost", "127.0.0.1", "::1"}:
            host = "host.docker.internal"
        elif platform.system() == "Linux" and host in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Existing Ollama is loopback-only; expose it on a Docker-reachable interface or let sh41 start its own service")
        if ":" in host:
            host = f"[{host}]"
        return f"{parts.scheme}://{host}:{parts.port or 11434}/v1"

    def stop(self):
        with self.locked():
            state = self.saved()
            if not state.get("owned"):
                raise ValueError("Ollama is externally managed; sh41 will not stop it")
            store = Store(self.root)
            for agent in store.agents():
                if agent["state"] in {"deployed", "provisioning"}:
                    deployment = store.deployment(agent["slug"])
                    if parse_yaml(deployment["spec"]).inference.provider == "ollama":
                        raise ValueError("Pause or park all Ollama-backed agents before stopping the server")
            current = signature(state["pid"])
            if current and current != state["signature"]:
                raise ValueError("Ollama process identity changed; refusing to signal a reused PID")
            if current:
                os.killpg(state["pid"], signal.SIGTERM)
            self.state_file.unlink(missing_ok=True)
            return {"stopped": True, "models_retained": True}
