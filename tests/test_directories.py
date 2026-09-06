import concurrent.futures
import os
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from sh41_local.cli import main
from sh41_local.db import Store
from sh41_local.directories import inspect_directory, materialize, validate_worktree
from sh41_local.spec import AgentSpec, Source, parse_yaml


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    (path / "tracked").write_text("committed")
    (path / "sub").mkdir()
    (path / "sub/file").write_text("nested")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    return path


def spec(name, path, mode="worktree"):
    return AgentSpec(agent=name, harness="codex", source=Source(path=str(path), mode=mode))


def provision(store, name, path, mode="worktree", **kwargs):
    deployment, _ = store.reserve(spec(name, path, mode), **kwargs)
    binding = materialize(store, deployment["agent_id"], name)
    return deployment, binding


def test_inspection_nested_linked_dirty_and_alias(repo, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    (repo / "tracked").write_text("dirty")
    info = inspect_directory(alias / "sub")
    assert info["repo_root"] == str(repo)
    assert info["dirty"] and info["head"] == git(repo, "rev-parse", "HEAD")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "linked", str(linked))
    other = inspect_directory(linked)
    assert other["common_dir"] == info["common_dir"]
    assert other["branch"] == "linked" and not other["dirty"]
    with pytest.raises(ValueError, match="existing"):
        inspect_directory(tmp_path / "missing")
    with pytest.raises(ValueError, match="metadata"):
        inspect_directory(repo / ".git")


def test_worktrees_committed_only_shared_metadata_and_persistence(repo, tmp_path):
    store = Store(tmp_path / "state")
    original_config = (repo / ".git/config").read_bytes()
    (repo / "tracked").write_text("dirty")
    (repo / "untracked").write_text("local only")
    first, one = provision(store, "one", repo)
    _, two = provision(store, "two", repo / "sub")
    work = Path(one["work"])
    assert (work / "tracked").read_text() == "committed"
    assert not (work / "untracked").exists()
    assert git(work, "branch", "--show-current") == "agent/one"
    assert (repo / ".git/config").read_bytes() == original_config
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 3
    (work / "tracked").write_text("agent changes")
    materialize(Store(store.root), first["agent_id"], "one")
    assert (work / "tracked").read_text() == "agent changes"
    assert (Path(two["work"]) / "tracked").read_text() == "committed"
    assert (repo / "tracked").read_text() == "dirty"
    peers = store.inspect_source(repo)["agents"]
    assert {p["agent"] for p in peers} == {"one", "two"}
    assert not any(p["shared"] for p in peers)
    with pytest.raises(ValueError, match="immutable"):
        store.reserve(spec("one", repo, "copy"))
    git(repo, "worktree", "move", str(work), str(tmp_path / "moved"))
    with pytest.raises(ValueError, match="repair"):
        materialize(store, first["agent_id"], "one")


def test_direct_and_copy_non_git_no_git_initialization(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "file").write_text("original")
    (folder / ".env").write_text("secret")
    before = folder.stat().st_mode
    store = Store(tmp_path / "state")
    _, direct = provision(store, "direct", folder, "direct")
    _, copied = provision(store, "copy", folder, "copy")
    assert Path(direct["work"]) == folder
    assert not (Path(copied["work"]) / ".git").exists()
    assert not (Path(copied["work"]) / ".env").exists()
    (Path(direct["work"]) / "file").write_text("changed")
    assert (folder / "file").read_text() == "changed"
    assert (Path(copied["work"]) / "file").read_text() == "original"
    assert folder.stat().st_mode == before
    assert not (folder / ".git").exists()


def test_shared_ack_is_transactional_including_parent_aliases_and_parked(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    child = folder / "child"
    child.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(folder, target_is_directory=True)
    store = Store(tmp_path / "state")
    first, _ = provision(store, "one", folder, "direct")
    store.set_deployment(first["id"], "parked", end=True)
    peers = store.inspect_source(alias / "child")["agents"]
    assert peers[0]["state"] == "parked" and peers[0]["shared"]
    with pytest.raises(ValueError, match="confirmation"):
        store.reserve(spec("two", child, "direct"))
    ack = [p["id"] for p in peers]
    provision(store, "two", child, "direct", shared_ack=ack)
    with pytest.raises(ValueError, match="confirmation"):
        store.reserve(spec("three", folder, "direct"), shared_ack=ack)
    provision(store, "three", folder, "direct", allow_shared=True)
    assert len(store.agents()) == 3


def test_concurrent_direct_reservations_require_new_confirmation(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    store = Store(tmp_path / "state")

    def reserve(name):
        try:
            return store.reserve(spec(name, folder, "direct"))[0]
        except ValueError as exc:
            assert "confirmation" in str(exc)
            return None

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(reserve, ["one", "two"]))
    assert sum(row is not None for row in rows) == 1


def test_concurrent_worktrees_and_source_privacy_guards(repo, tmp_path):
    store = Store(tmp_path / "state")
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(lambda name: provision(store, name, repo), ["one", "two"]))
    assert len({binding["work"] for _, binding in rows}) == 2
    with pytest.raises(ValueError, match="SH41_LOCAL_HOME"):
        store.inspect_source(tmp_path)
    internal = Store(repo / ".git/sh41-state")
    with pytest.raises(ValueError, match="metadata"):
        internal.inspect_source(repo / "sub")


def test_direct_path_cannot_be_replaced_with_private_state_symlink(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    store = Store(tmp_path / "state")
    deployment, _ = provision(store, "direct", folder, "direct")
    folder.rmdir()
    folder.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        materialize(store, deployment["agent_id"], "direct")


def test_host_worktree_setup_disables_hooks_and_rejects_filters(repo, tmp_path):
    store = Store(tmp_path / "state")
    marker = tmp_path / "hook-ran"
    hook = repo / ".git/hooks/post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o700)
    provision(store, "safe", repo)
    assert not marker.exists()
    git(repo, "config", "filter.custom.smudge", "cat")
    with pytest.raises(ValueError, match="filters"):
        store.reserve(spec("filtered", repo))


def test_branch_collision_and_interrupted_setup_never_reset(repo, tmp_path):
    store = Store(tmp_path / "state")
    git(repo, "branch", "agent/collision")
    deployment, _ = store.reserve(spec("collision", repo))
    with pytest.raises(ValueError, match="already exists"):
        materialize(store, deployment["agent_id"], "collision")
    deployment, _ = store.reserve(spec("interrupted", repo))
    binding = store.binding(deployment["agent_id"])
    git(repo, "worktree", "add", "-b", binding["branch"], binding["work"], binding["start_commit"])
    (Path(binding["work"]) / "tracked").write_text("must survive")
    store.binding_status(deployment["agent_id"], "creating")
    with pytest.raises(ValueError, match="repair"):
        materialize(store, deployment["agent_id"], "interrupted")
    assert (Path(binding["work"]) / "tracked").read_text() == "must survive"


def test_clean_or_not_started_worktree_preparation_recovers(repo, tmp_path):
    store = Store(tmp_path / "state")
    deployment, _ = store.reserve(spec("not-started", repo))
    store.binding_status(deployment["agent_id"], "creating")
    first = materialize(store, deployment["agent_id"], "not-started")
    assert first["status"] == "ready"
    store.binding_status(deployment["agent_id"], "creating")
    recovered = materialize(store, deployment["agent_id"], "not-started")
    assert recovered["status"] == "ready"
    assert git(Path(recovered["work"]), "rev-parse", "HEAD") == first["start_commit"]


def test_legacy_migration_preserves_spec_fingerprint_and_work(repo, tmp_path):
    store = Store(tmp_path / "state")
    deployment, binding = provision(store, "legacy", repo, "copy")
    work = Path(binding["work"])
    (work / "tracked").write_text("agent edit")
    with store.connect() as conn:
        legacy = parse_yaml(deployment["spec"]).model_dump()
        legacy["source"].pop("mode")
        import yaml
        conn.execute("UPDATE deployments SET spec=?,fingerprint='old-format'", (yaml.safe_dump(legacy),))
        conn.execute("DROP TABLE directory_bindings")
        conn.execute("PRAGMA user_version=2")
    reopened = Store(store.root)
    assert reopened.binding(deployment["agent_id"])["status"] == "legacy"
    assert reopened.reserve(spec("legacy", repo, "copy"))[1] is False
    materialize(Store(store.root), deployment["agent_id"], "legacy")
    assert (work / "tracked").read_text() == "agent edit"


def test_unborn_bare_and_submodule_rejection(repo, tmp_path):
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    git(unborn, "init")
    with pytest.raises(ValueError, match="initial commit"):
        validate_worktree(inspect_directory(unborn))
    bare = tmp_path / "bare"
    git(tmp_path, "clone", "--bare", str(repo), str(bare))
    with pytest.raises(ValueError, match="bare"):
        inspect_directory(bare)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{git(repo, 'rev-parse', 'HEAD')},module")
    git(repo, "commit", "-m", "submodule")
    with pytest.raises(ValueError, match="Submodule"):
        validate_worktree(inspect_directory(repo))


def test_flags_choose_repo_worktree_and_require_non_git_mode(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SH41_LOCAL_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("sh41_local.cli.call", lambda p: dict(inspect_directory(p["path"]), agents=[]))
    runner = CliRunner()
    result = runner.invoke(main, ["launch", "coding", "--harness", "codex", "--source", str(repo), "--write-only"])
    assert result.exit_code == 0, result.output
    assert "New worktree" in result.output
    assert parse_yaml((tmp_path / "coding.yaml").read_text()).source.mode == "worktree"
    assert not (tmp_path / "state").exists()
    assert "agent/coding" not in git(repo, "branch", "--list")
    folder = tmp_path / "plain"
    folder.mkdir()
    result = runner.invoke(main, ["launch", "plain", "--harness", "codex", "--source", str(folder), "--write-only"])
    assert result.exit_code != 0 and "--source-mode" in result.output
    assert not (tmp_path / "plain.yaml").exists()
    result = runner.invoke(main, ["launch", "plain", "--harness", "codex", "--source", str(folder),
                                  "--source-mode", "direct", "--write-only"])
    assert result.exit_code == 0, result.output
    assert parse_yaml((tmp_path / "plain.yaml").read_text()).source.mode == "direct"


@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_DOCKER") != "1", reason="Set SH41_TEST_DOCKER=1")
def test_docker_worktrees_and_shared_direct_folders(repo, tmp_path):
    from sh41_local.service import AgentService
    from sh41_local.spec import Inference, SecretRef
    service = AgentService(tmp_path / "state")
    provider = service.provider
    deployments = []
    (repo / "tracked").write_text("host dirty")
    plain = tmp_path / "plain, files"
    plain.mkdir()
    try:
        for name, source, mode in (("one", repo, "worktree"), ("two", repo, "worktree"),
                                   ("direct-one", plain, "direct"), ("direct-two", plain, "direct")):
            agent = spec(name, source, mode)
            agent.inference = Inference(api_key=SecretRef(env="TEST_KEY"))
            deployments.append(service.deploy(agent, {"TEST_KEY": "test-not-a-real-key"}, allow_shared=True))
        one, two, direct_one, direct_two = deployments
        provider.command(["exec", provider.name(one), "sh", "-c",
                          "cd /workspace/agent && printf changed > tracked && git add tracked && git commit -m agent-change"])
        assert provider.command(["exec", provider.name(two), "cat", "/workspace/agent/tracked"]).stdout == "committed"
        assert (repo / "tracked").read_text() == "host dirty"
        assert git(repo, "show", "agent/one:tracked") == "changed"
        mounts = provider.inspect(one)["Mounts"]
        assert not any(m["Source"] == str(repo) for m in mounts)
        service.lifecycle("one", "park")
        reopened = service.dispatch({"op": "redeploy", "agent": "one", "secrets": {"TEST_KEY": "test-not-a-real-key"}})
        deployments.append(reopened)
        assert provider.command(["exec", provider.name(reopened), "cat", "/workspace/agent/tracked"]).stdout == "changed"
        provider.command(["exec", provider.name(direct_one), "sh", "-c", "printf shared > /workspace/agent/file"])
        assert (plain / "file").read_text() == "shared"
        assert provider.command(["exec", provider.name(direct_two), "cat", "/workspace/agent/file"]).stdout == "shared"
        with pytest.raises(ValueError, match="already reside"):
            service.dispatch({"op": "export", "agent": "direct-one", "output": str(tmp_path / "export")})
    finally:
        for deployment in deployments:
            provider.remove(deployment)
