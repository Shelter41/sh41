import os
import subprocess

import pytest

from sh41_local.docker import DockerProvider
from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, Source
from sh41_local.workspace import export, prepare


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)


def test_git_snapshot_is_private_and_preserves_dirty_files(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    (source / "tracked").write_text("original")
    (source / "deleted").write_text("remove")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    (source / "tracked").write_text("dirty")
    (source / "new").write_text("untracked")
    (source / "deleted").unlink()
    (source / ".env").write_text("SECRET=hidden")
    work = prepare(tmp_path / "state", "id", "atlas", source)
    assert (work / "tracked").read_text() == "dirty"
    assert (work / "new").read_text() == "untracked"
    assert not (work / "deleted").exists()
    assert not (work / ".env").exists()
    assert git(work, "branch", "--show-current").stdout.strip() == "agent/atlas"
    assert git(work, "remote").stdout == ""
    (work / "tracked").write_text("agent changes")
    prepare(tmp_path / "state", "id", "atlas", source)
    assert (work / "tracked").read_text() == "agent changes"
    assert (source / "tracked").read_text() == "dirty"
    assert not (work / ".git/objects/info/alternates").exists()


def test_copy_and_export_do_not_follow_external_symlinks(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    outside = tmp_path / "private"
    outside.write_text("secret")
    (source / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        prepare(tmp_path / "state", "id", "atlas", source)
    assert not (tmp_path / "state/agents/id/work").exists()
    with pytest.raises(ValueError, match="symlink"):
        export(source, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_export_private_copy_no_overwrite(tmp_path):
    work = prepare(tmp_path / "state", "id", "atlas", None)
    (work / "result.txt").write_text("agent output")
    (work / ".env").write_text("secret")
    export(work, tmp_path / "export")
    assert (tmp_path / "export/result.txt").read_text() == "agent output"
    assert not (tmp_path / "export/.git").exists()
    assert not (tmp_path / "export/.env").exists()
    with pytest.raises(ValueError, match="exists"):
        export(work, tmp_path / "export")


@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_DOCKER") != "1", reason="Set SH41_TEST_DOCKER=1")
def test_real_docker_isolation_and_recreation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("source")
    root = tmp_path / "state"
    service = AgentService(root)
    provider: DockerProvider = service.provider
    deployments = []
    try:
        for name in ("one", "two"):
            spec = AgentSpec(agent=name, harness="codex", source=Source(path=str(source)))
            deployments.append(service.deploy(spec))
        one, two = deployments
        assert service.deploy(AgentSpec(agent="one", harness="codex", source=Source(path=str(source))))["id"] == one["id"]
        info = provider.inspect(one)
        assert info["Config"]["User"] != "0:0"
        assert info["HostConfig"]["CapDrop"] == ["ALL"]
        assert len(info["Mounts"]) == 3
        provider.command(["exec", provider.name(one), "python", "-c",
                          "from pathlib import Path; Path('/workspace/agent/a.txt').write_text('changed')"])
        result = provider.command(["exec", provider.name(two), "cat", "/workspace/agent/a.txt"])
        assert result.stdout == "source"
        assert (source / "a.txt").read_text() == "source"
        service.lifecycle("one", "pause")
        assert not provider.inspect(one)["State"]["Running"]
        service.lifecycle("one", "resume")
        assert provider.inspect(one)["State"]["Running"]
        service.lifecycle("one", "park")
        assert provider.inspect(one) is None
        spec = AgentSpec(agent="one", harness="codex", source=Source(path=str(source)))
        recreated = service.deploy(spec)
        deployments.append(recreated)
        assert recreated["id"] != one["id"]
        assert recreated["agent_id"] == one["agent_id"]
        assert provider.command(["exec", provider.name(recreated), "cat", "/workspace/agent/a.txt"]).stdout == "changed"
        provider.command(["stop", provider.name(recreated)])
        AgentService(root).reconcile()
        assert service.store.deployment("one")["status"] == "paused"
        service.dispatch({"op": "export", "agent": "one", "output": str(tmp_path / "output")})
        assert (tmp_path / "output/a.txt").read_text() == "changed"
    finally:
        for deployment in deployments:
            provider.remove(deployment)
