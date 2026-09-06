"""Supervisor-owned jobs and bounded, non-mutating dashboard observations."""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .inference import Ollama
from .spec import AgentSpec, parse_yaml


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ControlPlane:
    JOBS = {"deploy-start", "start", "pause", "resume", "park", "redeploy",
            "interrupt", "new-session", "continue-session", "export",
            "models-start", "models-pull", "models-stop"}

    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.ollama = Ollama(service.root, service.provider)
        self.guard = threading.RLock()
        self.locks = {}
        self.cache = {}
        self.pending = set()
        self.probes = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sh41-probe")

    def lock(self, resource):
        with self.guard:
            return self.locks.setdefault(resource, threading.Lock())

    def observe(self, key, callback, interval):
        with self.guard:
            cached = self.cache.get(key, {})
            age = time.monotonic() - cached.get("clock", 0)
            if age >= interval and key not in self.pending:
                self.pending.add(key)
                self.probes.submit(self._probe, key, callback)
            return dict(cached.get("value", {}), observed_at=cached.get("observed_at"),
                        stale=age > interval * 3)

    def _probe(self, key, callback):
        try:
            try:
                value = callback()
            except Exception:
                value = {"state": "unknown", "error": "Status unavailable"}
            with self.guard:
                self.cache[key] = {"value": value, "clock": time.monotonic(),
                                   "observed_at": timestamp()}
        finally:
            with self.guard:
                self.pending.discard(key)

    def docker_status(self):
        self.service.provider.require()
        return {"state": "ready"}

    def agent_status(self, slug, deployment):
        lock = self.lock(slug)
        if not lock.acquire(blocking=False):
            return {"state": "unknown", "activity": "operation in progress"}
        try:
            if deployment["ended_at"]:
                return {"state": deployment["status"], "harness_state": "stopped", "activity": "idle"}
            info = self.service.provider.inspect(deployment, timeout=3)
            if not info:
                # Inspect failures do not establish that a container is absent.
                return {"state": "unknown", "activity": "unknown"}
            if not info["State"]["Running"]:
                return {"state": "paused", "harness_state": "stopped", "activity": "idle"}
            status = self.service.provider.rpc(deployment, {"op": "status"}, timeout=4)
            return {"state": "deployed", "harness_state": status.get("harness_state", "unknown"),
                    "activity": "running" if status["running"] else "attached" if status["writer"] else "idle",
                    "writer": status["writer"], "run_id": status["running"]}
        finally:
            lock.release()

    def snapshot(self):
        docker = self.observe("_docker", self.docker_status, 2)
        rows = []
        for agent in self.store.agents():
            deployment = self.store.deployment(agent["slug"], latest=True)
            spec = parse_yaml(deployment["spec"])
            row = dict(agent, workspace=spec.workspace, model=spec.model or "default",
                       provider=spec.inference.provider, recorded_state=deployment["status"])
            if docker.get("state") == "ready":
                row.update(self.observe(agent["slug"],
                    lambda a=agent, d=deployment: self.agent_status(a["slug"], d), 2))
                if not row.get("observed_at"):
                    row["state"] = "unknown"
            else:
                row.update(state="unknown", activity="unknown", stale=True, observed_at=None)
            history = self.store.history(agent["slug"])
            row["last_run"] = history[-1] if history else None
            rows.append(row)
        return {"agents": rows, "docker": docker, "operations": self.store.operations()}

    def submit(self, payload):
        kind = payload.get("kind")
        if kind not in self.JOBS:
            raise ValueError("Unknown background operation")
        ident = str(uuid.UUID(payload["id"]))
        if kind == "deploy-start":
            spec = AgentSpec.model_validate(payload["spec"])
            resource = spec.agent
        elif kind.startswith("models-"):
            resource = "_models"
        else:
            resource = self.store.agent(payload["agent"])["slug"]
        with self.guard:
            previous = self.store.operation(ident)
            if previous:
                if previous["kind"] != kind or previous["resource"] != resource:
                    raise ValueError("Operation ID already used for another action")
                return previous
            lock = self.lock(resource)
            if not lock.acquire(blocking=False):
                raise ValueError("Agent is busy; wait for its current operation")
            try:
                self.store.add_operation(ident, kind, resource)
                threading.Thread(target=self._run, args=(ident, kind, payload, lock),
                                 daemon=True, name="sh41-operation").start()
            except Exception:
                lock.release()
                raise
            return self.store.operation(ident)

    def _run(self, ident, kind, payload, lock):
        try:
            self.store.update_operation(ident, "running", "Preparing")
            if kind == "models-pull":
                def progress(event):
                    percent = (int(100 * event.get("completed", 0) / event["total"])
                               if event.get("total") else None)
                    self.store.update_operation(ident, "running", "Downloading model" +
                                                (f" {percent}%" if percent is not None else ""))
                self.ollama.pull(payload["model"], progress=progress)
            elif kind == "models-start":
                self.ollama.ensure()
            elif kind == "models-stop":
                self.ollama.stop()
            else:
                request = dict(payload)
                request.pop("kind", None)
                request.pop("id", None)
                if kind == "deploy-start":
                    spec = AgentSpec.model_validate(payload["spec"])
                    self.service.deploy(spec, payload.get("secrets"))
                    self.store.update_operation(ident, "running", "Starting native terminal")
                    request.update(agent=spec.agent, op="start")
                else:
                    request["op"] = kind
                self.service.dispatch(request)
                if kind in {"resume", "redeploy", "new-session", "continue-session"}:
                    self.store.update_operation(ident, "running", "Starting native terminal")
                    self.service.dispatch(dict(request, op="start"))
            self.store.update_operation(ident, "completed", "Completed")
        except (ValueError, RuntimeError) as exc:
            from .harness_config import redact
            message = redact(str(exc), list((payload.get("secrets") or {}).values()))
            self.store.update_operation(ident, "failed", message[:500])
        except Exception:
            self.store.update_operation(ident, "failed", "Operation failed; inspect agent state before retrying")
        finally:
            lock.release()
            with self.guard:
                self.cache.clear()

    def dispatch(self, payload):
        operation = payload.get("op")
        if operation == "snapshot":
            return self.snapshot()
        if operation == "models-snapshot":
            return self.observe("_ollama", self.ollama.status, 5)
        if operation == "submit":
            return self.submit(payload)
        if operation == "operations":
            return self.store.operations()
        if operation == "operation":
            return self.store.operation(payload["id"])
        resource = payload.get("agent") or (payload.get("spec") or {}).get("agent") or "_global"
        lock = self.lock(resource)
        if not lock.acquire(blocking=False):
            raise ValueError("Agent is busy; wait for its current operation")
        try:
            return self.service.dispatch(payload)
        finally:
            lock.release()

    def close(self):
        self.probes.shutdown(wait=True, cancel_futures=True)
