from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import private_dir
from .workspace import prepare


def docker_binary() -> str:
    found = shutil.which("docker")
    desktop = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
    if found:
        return found
    if desktop.exists():
        return str(desktop)
    raise RuntimeError("Docker is missing; install Docker Desktop or Docker Engine")


class DockerProvider:
    def __init__(self, root: Path):
        self.root = root
        self.owner = hashlib.sha256(str(root).encode()).hexdigest()[:16]

    def command(self, args, *, data=None, timeout=60, check=True):
        try:
            result = subprocess.run([docker_binary(), *args], input=data, capture_output=True,
                                    text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Docker operation timed out; inspect agent state before retrying") from None
        if check and result.returncode:
            # Docker stderr may include command/env arguments. Never relay them.
            raise RuntimeError(f"Docker {args[0]} failed; check Docker availability with sh41 doctor")
        return result

    def require(self):
        self.command(["info", "--format", "{{.ServerVersion}}"], timeout=10)

    def name(self, deployment: dict) -> str:
        return f"sh41-{self.owner}-{deployment['id']}"

    def ensure_image(self, harness: str) -> str:
        package = Path(__file__).parent
        digest = hashlib.sha256(harness.encode())
        files = sorted(p for p in package.rglob("*") if p.suffix in {".py"} or p.name == "Dockerfile")
        for path in files:
            digest.update(str(path.relative_to(package)).encode())
            digest.update(path.read_bytes())
        tag = f"sh41-local/runner:{harness}-{digest.hexdigest()[:12]}"
        if self.command(["image", "inspect", tag], check=False).returncode == 0:
            return tag
        with tempfile.TemporaryDirectory(prefix="sh41-build-") as temporary:
            context = Path(temporary)
            shutil.copytree(package, context / "sh41_local", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(package / "container" / "Dockerfile", context / "Dockerfile")
            result = self.command(["build", "--build-arg", f"HARNESS={harness}", "-t", tag, str(context)],
                                  timeout=1200, check=False)
            if result.returncode:
                log = self.root / "image-build.log"
                log.write_text(result.stderr)
                log.chmod(0o600)
                raise RuntimeError(f"Runner image build failed; inspect {log}")
        return tag

    def create(self, spec, deployment, secrets):
        self.require()
        image = self.ensure_image(spec.harness)
        base = private_dir(self.root / "agents" / deployment["agent_id"])
        work = prepare(self.root, deployment["agent_id"], spec.agent,
                       Path(spec.source.path) if spec.source else None)
        home = private_dir(base / "home")
        control = private_dir(base / "control")
        if spec.instructions:
            (control / "instructions.md").write_text(Path(spec.instructions).read_text())
        name = self.name(deployment)
        self.command([
            "run", "-d", "--name", name,
            "--label", f"sh41.owner={self.owner}", "--label", f"sh41.deployment={deployment['id']}",
            "--user", f"{os.getuid() or 1000}:{os.getgid() or 1000}",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=256",
            "--cpus=2", "--memory=4g", "--init", "--restart=unless-stopped",
            "--add-host=host.docker.internal:host-gateway",
            "--mount", f"type=bind,src={work},dst=/workspace/agent",
            "--mount", f"type=bind,src={home},dst=/state/home",
            "--mount", f"type=bind,src={control},dst=/state/control",
            image,
        ], timeout=60)
        return name

    def inspect(self, deployment: dict):
        result = self.command(["inspect", self.name(deployment)], check=False)
        if result.returncode:
            return None
        item = json.loads(result.stdout)[0]
        if item["Config"].get("Labels", {}).get("sh41.owner") != self.owner:
            raise RuntimeError("Container ownership mismatch")
        return item

    def stop(self, deployment):
        if self.inspect(deployment):
            self.command(["stop", "--time", "10", self.name(deployment)])

    def start(self, deployment):
        if not self.inspect(deployment):
            raise RuntimeError("Container is missing; park and redeploy to recreate it")
        self.command(["start", self.name(deployment)])

    def remove(self, deployment):
        if self.inspect(deployment):
            self.command(["rm", "-f", self.name(deployment)])

    def rpc(self, deployment, payload, *, timeout=60):
        result = self.command(["exec", "-i", self.name(deployment), "python", "-m", "sh41_local.worker", "rpc"],
                              data=json.dumps(payload), timeout=timeout)
        response = json.loads(result.stdout)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]
