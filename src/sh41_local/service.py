from __future__ import annotations

from pathlib import Path

from .db import Store
from .docker import DockerProvider
from .spec import AgentSpec, parse_yaml
from .workspace import export


class AgentService:
    def __init__(self, root: Path, provider=None):
        self.root = root
        self.store = Store(root)
        self.provider = provider if provider is not None else DockerProvider(root)

    def deploy(self, spec: AgentSpec, secrets: dict | None = None):
        deployment, created = self.store.reserve(spec)
        if not created:
            return deployment
        try:
            container = self.provider.create(spec, deployment, secrets or {})
            self.store.set_deployment(deployment["id"], "deployed", container=container)
        except Exception:
            try:
                self.provider.remove(deployment)
            finally:
                self.store.set_deployment(deployment["id"], "failed", end=True,
                                          error="Provisioning failed; retry after checking dependencies")
            raise
        return self.store.deployment(spec.agent)

    def recover_startup(self):
        # No concurrent provisioning exists before this supervisor accepts clients.
        for agent in self.store.agents():
            if agent["state"] == "provisioning":
                deployment = self.store.deployment(agent["slug"])
                self.provider.remove(deployment)
                self.store.set_deployment(deployment["id"], "failed", end=True,
                    error="Supervisor stopped during provisioning; redeploy to retry")

    def lifecycle(self, slug, action):
        deployment = self.store.deployment(slug)
        info = self.provider.inspect(deployment)
        if info and info["State"]["Running"]:
            status = self.provider.rpc(deployment, {"op": "status"})
            if status["running"] or status["writer"]:
                raise ValueError("Detach or interrupt the active run before changing lifecycle")
        if action == "pause":
            self.provider.stop(deployment)
            self.store.set_deployment(deployment["id"], "paused")
        elif action == "resume":
            self.provider.start(deployment)
            spec = parse_yaml(deployment["spec"])
            try:
                if spec.inference.provider == "ollama":
                    self.provider.configure(deployment, spec, {})
            except Exception:
                self.provider.stop(deployment)
                self.store.set_deployment(deployment["id"], "paused")
                raise
            self.store.set_deployment(deployment["id"], "deployed")
        elif action == "park":
            self.provider.remove(deployment)
            self.store.set_deployment(deployment["id"], "parked", end=True)
        return {"agent": slug, "state": {"pause": "paused", "resume": "deployed", "park": "parked"}[action]}

    def ready(self, slug, secrets=None):
        deployment = self.store.deployment(slug)
        if deployment["status"] == "paused":
            self.lifecycle(slug, "resume")
        spec = parse_yaml(deployment["spec"])
        status = self.provider.rpc(deployment, {"op": "status"})
        if not status["running"] and not status["writer"]:
            self.provider.configure(deployment, spec, secrets or {})
        return deployment

    def sync_run(self, slug, ident, offset=0):
        run = self.store.run(slug, ident)
        if run["status"] in {"pending", "running"}:
            deployment = self.store.deployment(slug, latest=True)
            info = self.provider.inspect(deployment)
            if info and info["State"]["Running"]:
                saved = len(self.store.events(ident))
                result = self.provider.rpc(deployment, {"op": "run-status", "run_id": ident, "offset": saved})
                for index, event in enumerate(result["events"], start=saved):
                    self.store.event(ident, index, event)
                self.store.native_session(run["session_id"], result.get("native_id"))
                if result["status"] not in {"pending", "running"} and not result["more"]:
                    self.store.finish_run(ident, result["status"], result.get("native_id"))
            else:
                self.store.finish_run(ident, "interrupted")
        current = self.store.run(slug, ident)
        return {"id": ident, "status": current["status"], "events": self.store.events(ident, offset)}

    def sync_pending(self, slug):
        for run in self.store.history(slug):
            if run["status"] in {"pending", "running"}:
                self.sync_run(slug, run["id"])

    def reconcile(self):
        for agent in self.store.agents():
            if agent["state"] in {"parked", "provisioning"}:
                continue
            deployment = self.store.deployment(agent["slug"])
            info = self.provider.inspect(deployment)
            if info is None:
                self.store.set_deployment(deployment["id"], "parked", end=True)
            else:
                self.store.set_deployment(deployment["id"], "deployed" if info["State"]["Running"] else "paused")

    def dispatch(self, request: dict):
        operation = request.get("op")
        if operation == "ping":
            return {"version": 1}
        if operation == "deploy":
            return self.deploy(AgentSpec.model_validate(request["spec"]), request.get("secrets"))
        if operation == "spec":
            return parse_yaml(self.store.deployment(request["agent"], latest=True)["spec"]).model_dump()
        if operation == "agents":
            # Listing cached metadata remains useful while Docker is unavailable.
            try:
                self.provider.require()
                self.reconcile()
            except RuntimeError:
                pass
            return self.store.agents()
        if operation in {"pause", "resume", "park"}:
            return self.lifecycle(request["agent"], operation)
        if operation == "redeploy":
            spec = parse_yaml(self.store.deployment(request["agent"], latest=True)["spec"])
            return self.deploy(spec, request.get("secrets"))
        if operation == "run":
            slug = request["agent"]
            if not request.get("message", "").strip():
                raise ValueError("Message must not be empty")
            self.sync_pending(slug)
            deployment = self.ready(slug, request.get("secrets"))
            status = self.provider.rpc(deployment, {"op": "status"})
            if status["running"] or status["writer"]:
                raise RuntimeError("Agent already has an active run or writable attachment")
            run = self.store.start_run(slug)
            try:
                self.provider.rpc(deployment, {"op": "run", "run_id": run["id"],
                    "session_id": run["session_id"], "native_id": run["native_id"], "message": request["message"]})
            except Exception:
                # Delivery may have succeeded. Reconcile its journal on the next
                # status/history request; never replay an uncertain submission.
                raise
            return run
        if operation == "run-status":
            return self.sync_run(request["agent"], request["run_id"], request.get("offset", 0))
        if operation == "interrupt":
            return self.provider.rpc(self.store.deployment(request["agent"]), {"op": "interrupt"})
        if operation == "attach":
            slug = request["agent"]
            self.sync_pending(slug)
            deployment = self.ready(slug, request.get("secrets"))
            session = self.store.session(slug)
            result = self.provider.rpc(deployment, {"op": "attach", "session_id": session["id"],
                "native_id": session["native_id"], "readonly": request.get("readonly", False)}, timeout=180)
            self.store.native_session(session["id"], result.get("native_id"))
            return dict(result, container=self.provider.name(deployment))
        if operation in {"new-session", "continue-session"}:
            slug = request["agent"]
            self.sync_pending(slug)
            deployment = self.store.deployment(slug, latest=True)
            info = self.provider.inspect(deployment) if deployment["ended_at"] is None else None
            if info and info["State"]["Running"]:
                self.provider.rpc(deployment, {"op": "switch-session"})
            return self.store.session(slug, new=operation == "new-session", resume=request.get("session_id"))
        if operation == "export":
            agent = self.store.agent(request["agent"])
            latest = self.store.deployment(request["agent"], latest=True)
            if latest["ended_at"] is None and latest["status"] != "paused":
                raise ValueError("Pause the agent before exporting a consistent copy")
            work = self.root / "agents" / agent["id"] / "work"
            export(work, Path(request["output"]))
            return {"output": request["output"]}
        if operation == "sessions":
            return self.store.sessions(request["agent"])
        if operation == "history":
            self.sync_pending(request["agent"])
            return self.store.history(request["agent"])
        raise ValueError("Unknown supervisor operation")
