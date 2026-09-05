from __future__ import annotations

from pathlib import Path

from .db import Store
from .spec import AgentSpec


class AgentService:
    def __init__(self, root: Path, provider=None):
        self.root = root
        self.store = Store(root)
        self.provider = provider

    def deploy(self, spec: AgentSpec, secrets: dict | None = None):
        if self.provider is None:
            raise RuntimeError("Docker execution is not implemented yet")
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

    def dispatch(self, request: dict):
        operation = request.get("op")
        if operation == "ping":
            return {"version": 1}
        if operation == "deploy":
            return self.deploy(AgentSpec.model_validate(request["spec"]), request.get("secrets"))
        if operation == "agents":
            return self.store.agents()
        if operation == "sessions":
            return self.store.sessions(request["agent"])
        if operation == "history":
            return self.store.history(request["agent"])
        raise ValueError("Unknown supervisor operation")
