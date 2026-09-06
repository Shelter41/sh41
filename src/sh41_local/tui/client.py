import json
import subprocess
import asyncio
import threading
import uuid

from ..credentials import required_secrets
from ..docker import docker_binary
from ..spec import AgentSpec
from ..supervisor import request


async def background(callback, *args, **kwargs):
    """A disconnected UI must not wait for a blocking socket or terminal client."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def finish(result, error):
        if not future.done():
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

    def execute():
        try:
            result, error = callback(*args, **kwargs), None
        except Exception as exc:
            result, error = None, exc
        try:
            loop.call_soon_threadsafe(finish, result, error)
        except RuntimeError:
            pass

    threading.Thread(target=execute, daemon=True, name="sh41-ui-request").start()
    return await future


class Client:
    def call(self, payload):
        return request(payload, timeout=8)

    def secrets(self, agent):
        spec = AgentSpec.model_validate(self.call({"op": "spec", "agent": agent}))
        return required_secrets(spec)

    def submit(self, kind, *, agent=None, spec=None, ident=None, **fields):
        payload = {"op": "submit", "kind": kind, "id": ident or str(uuid.uuid4()), **fields}
        if agent:
            payload["agent"] = agent
            if kind in {"start", "resume", "redeploy", "new-session", "continue-session"}:
                payload["secrets"] = self.secrets(agent)
        if spec is not None:
            payload["spec"] = spec.model_dump()
            payload["secrets"] = required_secrets(spec)
        return self.call(payload)

    def attachment(self, agent, readonly=False):
        return request({"op": "attach", "agent": agent, "readonly": readonly,
                        "secrets": self.secrets(agent)}, timeout=200)

    def attach(self, attachment):
        return subprocess.call([docker_binary(), "exec", "-it", "-e", "TERM=xterm-256color",
            attachment["container"], "python", "-m", "sh41_local.worker", "attach",
            attachment["token"], json.dumps(attachment["argv"])])
