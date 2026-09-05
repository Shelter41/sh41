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

    def lifecycle(self, slug, action):
        deployment = self.store.deployment(slug)
        if action == "pause":
            self.provider.stop(deployment)
            self.store.set_deployment(deployment["id"], "paused")
        elif action == "resume":
            self.provider.start(deployment)
            self.store.set_deployment(deployment["id"], "deployed")
        elif action == "park":
            self.provider.remove(deployment)
            self.store.set_deployment(deployment["id"], "parked", end=True)
        return {"agent": slug, "state": {"pause": "paused", "resume": "deployed", "park": "parked"}[action]}

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
            return self.store.history(request["agent"])
        raise ValueError("Unknown supervisor operation")
