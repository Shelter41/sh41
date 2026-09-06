"""Container-local manager. Journals survive client and manager disconnects."""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

from .drivers.claude_driver import ClaudeDriver
from .drivers.claude_jsonl import is_turn_complete
from .drivers.codex_driver import CodexDriver
from .drivers.runtime import ProcessSpec
from .drivers.tmux_runtime import TmuxRuntime
from .harness_config import configure, redact
from .paths import atomic_json, private_dir
from .spec import AgentSpec

CONTROL = Path("/state/control")
SOCKET = "/tmp/sh41-manager.sock"
CWD = "/workspace/agent"


def identifier(value):
    return str(uuid.UUID(value))


class Manager:
    def __init__(self, control=CONTROL):
        self.control = private_dir(control)
        self.runtime = TmuxRuntime()
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.running = None
        self.lease = None
        self.spec = None
        self.environment = {}
        self.secrets = []
        self.state = {}
        self.driver = None
        config = control / "config.json"
        if config.exists():
            self.load_config(json.loads(config.read_text()))
        for path in (control / "runs").glob("*/status.json"):
            data = json.loads(path.read_text())
            if data["status"] in {"pending", "running"}:
                data["status"] = "interrupted"
                atomic_json(path, data)

    def load_config(self, payload):
        self.spec = AgentSpec.model_validate(payload["spec"])
        self.environment = configure(Path(os.environ["HOME"]), self.control, payload)
        self.secrets = list((payload.get("secrets") or {}).values())

        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, str) and any(s in key.lower() for s in ("token", "key", "secret")):
                        self.secrets.append(item)
                    else:
                        collect(item)
        collect(payload.get("native") or {})
        driver = {"claude-code": ClaudeDriver, "codex": CodexDriver}.get(self.spec.harness)
        self.driver = driver(self.runtime) if driver else None

    def check_inference(self, payload):
        spec = AgentSpec.model_validate(payload["spec"])
        if spec.harness != "opencode":
            return
        url = payload.get("inference_url") or spec.inference.base_url
        key = (payload.get("secrets") or {}).get(spec.inference.api_key.env) if spec.inference.api_key else None
        try:
            response = httpx.get(url + "/models", headers={"Authorization": f"Bearer {key}"} if key else {},
                                 timeout=10, trust_env=False)
            if response.status_code in {401, 403}:
                raise ValueError("Inference endpoint rejected credentials")
            response.raise_for_status()
        except httpx.HTTPError:
            raise ValueError("Inference endpoint is unreachable from the sandbox; verify URL, network binding and /v1/models support") from None

    def state_path(self, session):
        return self.control / "sessions" / f"{identifier(session)}.json"

    def save_state(self):
        if self.state.get("session_id"):
            if self.driver and self.spec.harness == "codex" and not self.native_id():
                path = self.driver.resolve_transcript(self.state, cwd=CWD)
                if path:
                    self.state["native_session_id"] = self.driver.transcript_session_id(path)
                    self.state["codex_session_id"] = self.state["native_session_id"]
                    self.state["rollout_path"] = str(path)
            atomic_json(self.state_path(self.state["session_id"]), self.state)

    def native_id(self):
        return self.state.get("native_session_id") or self.state.get("codex_session_id")

    def writer(self):
        handle = self.runtime.lookup("agent")
        if handle and self.runtime.has_writer(handle):
            return True
        if self.lease and time.monotonic() - self.lease[1] < 15:
            return True
        self.lease = None
        return False

    def busy(self):
        if self.running:
            return True
        if not self.spec:
            return False
        handle = self.runtime.lookup("agent")
        if not handle:
            return False
        inspected = self.runtime.inspect(handle)
        if not inspected.active:
            return False
        if self.spec.harness == "opencode" and self.native_id():
            try:
                status = self.oc("GET", "/session/status", timeout=2)
                return status.get(self.native_id(), {}).get("type") in {"busy", "retry"}
            except httpx.HTTPError:
                raise ValueError("Cannot determine native turn status; inspect the agent terminal") from None
        if self.spec.harness in {"claude-code", "codex"}:
            path = self.driver.resolve_transcript(self.state, cwd=CWD)
            if path:
                for line in reversed(path.read_text(errors="replace").splitlines()):
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if self.spec.harness == "claude-code":
                        if is_turn_complete(row) or row.get("subtype") == "turn_duration":
                            return False
                        if row.get("type") in {"user", "assistant"}:
                            content = (row.get("message") or {}).get("content", "")
                            return "[Request interrupted by user" not in str(content)
                    if row.get("type") == "event_msg":
                        kind = (row.get("payload") or {}).get("type")
                        if kind in {"task_complete", "turn_aborted"}:
                            return False
                        if kind == "task_started":
                            return True
        return False

    def ensure_session(self, session, native=None):
        if self.spec is None:
            raise ValueError("Agent is not configured")
        if self.state.get("session_id") != session:
            handle = self.runtime.lookup("agent")
            if handle:
                self.runtime.terminate(handle)
            path = self.state_path(session)
            self.state = json.loads(path.read_text()) if path.exists() else {"session_id": session, "cwd": CWD}
            if path.exists():
                # Recover native IDs created by direct TUI turns, even if the
                # attachment or container died before its release callback.
                self.save_state()
            if self.spec.harness == "claude-code":
                self.state.setdefault("native_session_id", session)
            if native:
                self.state["native_session_id"] = native
                if self.spec.harness == "codex":
                    self.state["codex_session_id"] = native
            if self.driver:
                self.driver.clear_process_state(self.state)
        handle = self.runtime.ensure("agent", CWD).handle
        self.runtime.set_environment(handle, self.environment)
        if self.spec.harness == "opencode":
            self.ensure_opencode(handle)
        else:
            if not self.runtime.inspect(handle).active:
                self.driver.clear_process_state(self.state)
            if self.spec.harness == "claude-code":
                prompt = self.control / "instructions.md"
                argv = self.driver.build_argv(model=self.spec.model,
                    system_prompt=prompt.read_text() if prompt.exists() else "",
                    permission_mode="bypassPermissions")
            else:
                if not self.native_id() and "rollout_baseline" not in self.state:
                    self.state["rollout_baseline"] = [str(p) for p in self.driver.transcript_root().glob("**/rollout-*.jsonl")]
                argv = self.driver.build_interactive_argv(model=self.spec.model,
                    permission_mode="bypassPermissions", resume_session_id=self.native_id())
            self.driver.ensure_started(handle, argv, state=self.state,
                                       permission_mode="bypassPermissions", requested_model=self.spec.model)
        self.save_state()
        return handle

    def oc(self, method, path, **kwargs):
        with httpx.Client(base_url="http://127.0.0.1:4096", timeout=kwargs.pop("timeout", 30),
                          trust_env=False) as client:
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None

    def ensure_opencode(self, handle):
        backend = self.runtime.ensure("opencode-server", CWD).handle
        self.runtime.set_environment(backend, self.environment)
        if not self.runtime.inspect(backend).active:
            self.runtime.start(backend, ProcessSpec(argv=["opencode", "serve", "--hostname", "127.0.0.1",
                                                        "--port", "4096"]))
        deadline = time.monotonic() + 90
        while True:
            try:
                self.oc("GET", "/global/health", timeout=2)
                break
            except (httpx.HTTPError, ValueError):
                if time.monotonic() > deadline:
                    raise RuntimeError("OpenCode server did not become ready; inspect its configuration") from None
                time.sleep(0.25)
        if not self.native_id():
            self.state["native_session_id"] = self.oc("POST", "/session", json={})["id"]
        else:
            self.oc("GET", f"/session/{self.native_id()}")
        if not self.runtime.inspect(handle).active:
            self.runtime.start(handle, ProcessSpec(argv=["opencode", "attach", "http://127.0.0.1:4096",
                                                        "--session", self.native_id()]))

    def run_dir(self, ident):
        return private_dir(self.control / "runs" / identifier(ident))

    def emit(self, ident, event):
        event = redact(event, self.secrets)
        with (self.run_dir(ident) / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def run_status(self, ident, status):
        atomic_json(self.run_dir(ident) / "status.json",
                    {"id": ident, "status": status, "native_id": self.native_id()})

    def execute(self, payload):
        ident = payload["run_id"]
        status = "failed"
        try:
            handle = self.ensure_session(payload["session_id"], payload.get("native_id"))
            self.run_status(ident, "running")
            self.emit(ident, {"type": "user", "message": payload["message"]})
            if self.spec.harness == "opencode":
                before = {row["info"]["id"] for row in self.oc("GET", f"/session/{self.native_id()}/message")}
                body = {"parts": [{"type": "text", "text": payload["message"]}]}
                instructions = self.control / "instructions.md"
                if instructions.exists():
                    body["system"] = instructions.read_text()
                result = self.oc("POST", f"/session/{self.native_id()}/message", json=body, timeout=3600)
                rows = self.oc("GET", f"/session/{self.native_id()}/message")
                for row in rows:
                    if row["info"]["id"] not in before and row["info"]["role"] == "assistant":
                        self.emit(ident, {"type": "assistant", "message": row})
                status = "failed" if result.get("info", {}).get("error") else "completed"
                if status == "failed":
                    self.emit(ident, {"type": "error", "message": json.dumps(result["info"]["error"])})
            else:
                path = self.driver.resolve_transcript(self.state, cwd=CWD)
                offset = self.driver.transcript_offset(path)
                started = time.time()
                self.driver.send_message(handle, payload["message"])
                deadline = time.monotonic() + 90
                while path is None and time.monotonic() < deadline and not self.cancel.is_set():
                    if self.spec.harness == "claude-code":
                        self.driver.check_authentication(self.runtime.inspect(handle).output)
                    path = self.driver.resolve_transcript(self.state, cwd=CWD, after_mtime=started)
                    time.sleep(0.25)
                if path is None:
                    detail = redact(self.runtime.inspect(handle).output[-2500:], self.secrets)
                    raise RuntimeError("Harness produced no transcript. Terminal diagnostic: " + detail)
                if self.spec.harness == "codex":
                    self.state["codex_session_id"] = self.driver.transcript_session_id(path)
                    self.state["native_session_id"] = self.state["codex_session_id"]
                    self.state["rollout_path"] = str(path)
                else:
                    self.state["native_session_id"] = path.stem
                    self.state["jsonl_path"] = str(path)
                self.save_state()
                terminal = []
                output_seen = False

                def emit(event):
                    nonlocal output_seen
                    self.emit(ident, event)
                    if event.get("type") == "assistant":
                        output_seen = True
                    if event.get("type") == "result" or is_turn_complete(event):
                        terminal.append(event)

                self.driver.consume_transcript(path, offset, emit, turn_idle_sec=120,
                                               first_activity_sec=90, cancel_check=self.cancel.is_set)
                if terminal:
                    failed = any(e.get("is_error") or e.get("isApiErrorMessage") or
                        (e.get("message") or {}).get("stop_reason") in {"refusal", "max_tokens"}
                        for e in terminal)
                    status = "failed" if failed else "completed"
                    if status == "completed" and not output_seen and not any(e.get("result") for e in terminal):
                        detail = redact(self.runtime.inspect(handle).output[-2500:], self.secrets)
                        auth_failed = any(marker in detail.lower() for marker in (
                            "401", "unauthorized", "incorrect api key", "invalid api key", "authentication",
                        ))
                        status = "failed" if auth_failed else "interrupted"
                        message = "Harness authentication failed. " if auth_failed else "No response or tool execution confirmed. "
                        self.emit(ident, {"type": "error", "message": message + "Terminal diagnostic: " + detail})
                else:
                    self.emit(ident, {"type": "error", "message": "No confirmed turn completion. Terminal diagnostic: " +
                        redact(self.runtime.inspect(handle).output[-2500:], self.secrets)})
                    status = "interrupted"
        except Exception as exc:
            message = str(exc).split("output=")[0]
            self.emit(ident, {"type": "error", "message": redact(message, self.secrets)})
        finally:
            if self.cancel.is_set():
                status = "cancelled"
            self.save_state()
            self.run_status(ident, status)
            with self.lock:
                self.running = None

    def dispatch(self, payload):
        operation = payload["op"]
        with self.lock:
            if operation == "configure":
                if self.busy() or self.writer():
                    raise ValueError("Agent has an active writer")
                path = self.control / "config.json"
                if path.exists() and json.loads(path.read_text()) == payload:
                    return {"configured": True}
                self.check_inference(payload)
                for key in ("agent", "opencode-server"):
                    handle = self.runtime.lookup(key)
                    if handle:
                        self.runtime.terminate(handle)
                if self.driver:
                    self.driver.clear_process_state(self.state)
                self.load_config(payload)
                atomic_json(self.control / "config.json", payload)
                return {"configured": True}
            if operation == "status":
                handle = self.runtime.lookup("agent")
                return {"running": self.running or ("native" if self.busy() else None),
                        "writer": self.writer(), "native_id": self.native_id(),
                        "harness_state": "ready" if handle and self.runtime.inspect(handle).active
                        else "stopped"}
            if operation == "start":
                handle = self.runtime.lookup("agent")
                if self.busy() or self.writer():
                    return {"started": bool(handle), "native_id": self.native_id()}
                self.ensure_session(payload["session_id"], payload.get("native_id"))
                self.save_state()
                return {"started": True, "native_id": self.native_id()}
            if operation == "run":
                if self.busy() or self.writer():
                    raise ValueError("Agent already has an active run or writable attachment")
                ident = identifier(payload["run_id"])
                identifier(payload["session_id"])
                if (self.run_dir(ident) / "status.json").exists():
                    raise ValueError("Run already exists; it will not be replayed")
                self.running = ident
                self.cancel.clear()
                self.run_status(ident, "pending")
                threading.Thread(target=self.execute, args=(payload,), daemon=True).start()
                return {"id": ident}
            if operation == "run-status":
                directory = self.run_dir(payload["run_id"])
                path = directory / "status.json"
                if not path.exists():
                    return {"status": "interrupted", "events": [], "more": False}
                status = json.loads(path.read_text())
                rows = []
                events = directory / "events.jsonl"
                if events.exists():
                    for line in events.read_text().splitlines():
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            break
                start = int(payload.get("offset", 0))
                status.update(events=rows[start:start + 100], more=len(rows) > start + 100)
                return status
            if operation == "interrupt":
                self.cancel.set()
                handle = self.runtime.lookup("agent")
                if handle and self.driver:
                    self.driver.interrupt(handle)
                elif self.spec and self.spec.harness == "opencode" and self.native_id():
                    self.oc("POST", f"/session/{self.native_id()}/abort")
                return {"interrupted": True}
            if operation == "attach":
                readonly = bool(payload.get("readonly") or self.busy() or self.writer())
                if self.running:
                    handle = self.runtime.lookup("agent")
                    if not handle:
                        raise ValueError("Agent is starting; attach again shortly")
                else:
                    handle = self.ensure_session(payload["session_id"], payload.get("native_id"))
                token = str(uuid.uuid4())
                if not readonly:
                    self.lease = (token, time.monotonic())
                return {"argv": self.runtime.attachment_argv(handle, readonly=readonly),
                        "token": token, "readonly": readonly, "native_id": self.native_id()}
            if operation == "release":
                if self.lease and self.lease[0] == payload["token"]:
                    self.lease = None
                self.save_state()
                return {"released": True}
            if operation == "switch-session":
                if self.busy() or self.writer():
                    raise ValueError("Detach or finish the run before switching sessions")
                handle = self.runtime.lookup("agent")
                if handle:
                    self.runtime.terminate(handle)
                self.state = {}
                return {"switched": True}
            raise ValueError("Unknown manager operation")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw = self.rfile.readline(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Manager request too large")
            result = self.server.manager.dispatch(json.loads(raw))
            response = {"result": result}
        except Exception as exc:
            response = {"error": redact(str(exc).split("output=")[0], self.server.manager.secrets)}
        self.wfile.write(json.dumps(response).encode() + b"\n")


def rpc(payload):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(180)
        sock.connect(SOCKET)
        sock.sendall(json.dumps(payload).encode() + b"\n")
        with sock.makefile("r") as stream:
            return json.loads(stream.readline())


def main():
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    command = sys.argv[1]
    if command == "rpc":
        print(json.dumps(rpc(json.load(sys.stdin))))
    elif command == "attach":
        token = sys.argv[2]
        argv = json.loads(sys.argv[3])
        try:
            raise SystemExit(subprocess.call(argv))
        finally:
            rpc({"op": "release", "token": token})
    else:
        Path(SOCKET).unlink(missing_ok=True)
        with socketserver.ThreadingUnixStreamServer(SOCKET, Handler) as server:
            server.daemon_threads = True
            server.manager = Manager()
            Path(SOCKET).chmod(0o600)
            server.serve_forever()


if __name__ == "__main__":
    main()
