"""Host directory inspection and durable working-directory materialization."""
from __future__ import annotations

import fcntl
import hashlib
import os
import subprocess
from pathlib import Path

from .paths import private_dir
from .workspace import prepare


def git(path, *args, check=True):
    # Never inherit a caller's repository override or run checkout hooks on the host.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    try:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                                 "-C", str(path), *args], env=env, capture_output=True,
                                text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise ValueError("Repository inspection/setup timed out; inspect the repository before retrying") from None
    if check and result.returncode:
        raise ValueError("Git directory operation failed; inspect repository permissions and state")
    return result


def overlap(left, right):
    if not left or not right:
        return False
    a, b = Path(left).resolve(), Path(right).resolve()
    if a.is_relative_to(b) or b.is_relative_to(a):
        return True
    # samefile catches case aliases on case-insensitive filesystems.
    try:
        return any(a.samefile(p) for p in (b, *b.parents)) or any(b.samefile(p) for p in a.parents)
    except OSError:
        return False


def inspect_directory(value):
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Source must be an existing local directory")
    result = dict(path=str(path), kind="directory", repo_root=None, common_dir=None,
                  branch=None, head=None, dirty=False)
    probe = git(path, "rev-parse", "--is-inside-work-tree", check=False)
    if probe.returncode:
        if (path / ".git").exists() or "not a git repository" not in probe.stderr.lower():
            raise ValueError("Cannot inspect repository; check Git ownership and permissions")
        return result
    if probe.stdout.strip() != "true":
        raise ValueError("Select a working checkout, not a bare repository or Git metadata directory")
    root = Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    common = Path(git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()).resolve()
    head = git(path, "rev-parse", "--verify", "HEAD", check=False)
    result.update(kind="repository", repo_root=str(root), common_dir=str(common),
                  head=head.stdout.strip() if head.returncode == 0 else None,
                  branch=git(path, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip() or "detached",
                  dirty=bool(git(path, "status", "--porcelain", "--untracked-files=normal").stdout))
    return result


def validate_worktree(info):
    if info["kind"] != "repository":
        raise ValueError("Worktree mode requires a Git working checkout")
    if not info["head"]:
        raise ValueError("Repository has no commits; make an initial commit before creating a worktree")
    entries = git(info["repo_root"], "ls-tree", "-r", info["head"]).stdout.splitlines()
    if any(line.startswith("160000 ") for line in entries):
        raise ValueError("Submodule repositories are not supported for agent worktrees yet")
    # Checkout filters can execute repository-configured commands on the host.
    filters = git(info["repo_root"], "config", "--get-regexp", r"^filter\..*\.(smudge|process)$", check=False)
    if filters.stdout.strip():
        raise ValueError("Host checkout filters are not supported for agent worktrees; use a checkout without filters")


def describe(info, name, root):
    if info["kind"] == "repository":
        warning = " Uncommitted changes stay in the original folder." if info["dirty"] else ""
        return (f"Repository: {info['repo_root']}\nNew worktree: {root / 'worktrees' / name}\n"
                f"Branch: agent/{name}; committed HEAD: {info['head'] or 'no commits'}." + warning +
                "\nWorking files are separate; Git metadata and tracked secrets remain shared/accessible.")
    return "Original folder edits affect host files, including hidden files. A private copy leaves originals unchanged."


def associations(info, rows, exclude=None):
    result = []
    for row in rows:
        if row["slug"] == exclude:
            continue
        same_repo = info.get("common_dir") and row.get("common_dir") == info["common_dir"]
        shared = row["mode"] == "direct" and overlap(info["path"], row["work"])
        if same_repo or shared or overlap(info["path"], row["source"]):
            result.append(dict(id=row["agent_id"], agent=row["slug"], mode=row["mode"],
                               state=row["state"], shared=shared, work=row["work"]))
    return result


def materialize(store, agent_id, slug):
    binding = store.binding(agent_id)
    work = Path(binding["work"])
    if work.resolve() != work or work.is_symlink():
        raise ValueError("Working directory was replaced by a symlink; restore its original path before retrying")
    if binding["mode"] == "direct":
        if not work.is_dir():
            raise ValueError("Original folder is missing; restore it before restarting the agent")
        store.binding_status(agent_id, "ready")
        return binding
    if binding["mode"] == "copy":
        if binding["status"] in {"ready", "legacy"} and not work.is_dir():
            raise ValueError("Agent working directory is missing; repair it rather than recreating its files")
        prepare(store.root, agent_id, slug, Path(binding["source"]) if binding["source"] else None,
                initialize_non_git=binding["source"] is None)
        store.binding_status(agent_id, "ready")
        return binding
    common = Path(binding["common_dir"])
    if common.resolve() != common or overlap(common, store.root):
        raise ValueError("Repository metadata path changed or overlaps private state; repair required")
    if not common.is_dir():
        raise ValueError("Repository metadata is missing or moved; repair the worktree before retrying")
    locks = private_dir(store.root / "locks")
    with (locks / (hashlib.sha256(str(common).encode()).hexdigest() + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        binding = store.binding(agent_id)
        if binding["status"] == "creating" and not work.exists() and not work.is_symlink():
            branch = git(common, "show-ref", "--verify", "--quiet", "refs/heads/" + binding["branch"], check=False)
            if branch.returncode == 1:
                store.binding_status(agent_id, "pending")
                binding["status"] = "pending"
        if binding["status"] in {"ready", "creating"}:
            if not work.is_dir():
                raise ValueError("Worktree setup was interrupted or its directory moved; repair required (no files reset)")
            observed = inspect_directory(work)
            git_dir = git(work, "rev-parse", "--absolute-git-dir").stdout.strip()
            backlink = Path(git_dir) / "gitdir"
            if (observed["common_dir"] != str(common) or not backlink.is_file()
                    or Path(backlink.read_text().strip()).resolve() != (work / ".git").resolve()):
                raise ValueError("Worktree ownership changed; repair required")
            if binding["status"] == "creating":
                # A complete clean checkout is recoverable; ambiguous partial/user edits are never reset.
                if (observed["dirty"] or observed["head"] != binding["start_commit"]
                        or observed["branch"] != binding["branch"]):
                    raise ValueError("Worktree setup was interrupted; inspect its files and repair before retrying")
                store.binding_status(agent_id, "ready")
            return store.binding(agent_id)
        if work.exists() or work.is_symlink():
            raise ValueError("Worktree destination already exists and is not owned by this agent")
        if git(common, "show-ref", "--verify", "--quiet", "refs/heads/" + binding["branch"], check=False).returncode == 0:
            raise ValueError(f"Branch {binding['branch']} already exists; choose another agent name")
        private_dir(work.parent)
        store.binding_status(agent_id, "creating")
        git(common, "worktree", "add", "-b", binding["branch"], str(work), binding["start_commit"])
        store.binding_status(agent_id, "ready")
        return store.binding(agent_id)
