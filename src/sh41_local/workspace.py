from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import private_dir

IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
           ".DS_Store", ".env", ".context"}


def git(*args: str, cwd: Path | None = None, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError("Git workspace operation failed; check the source repository")
    return result


def ignored(path: Path) -> bool:
    return any(part in IGNORED or part.startswith(".env.") for part in path.parts)


def copy_entry(source: Path, destination: Path, root: Path):
    if source.is_symlink():
        target = source.resolve()
        if not target.is_relative_to(root.resolve()) or Path(os.readlink(source)).is_absolute():
            raise ValueError("Workspace contains an absolute or external symlink")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if not ignored(child.relative_to(root)):
                copy_entry(child, destination / child.name, root)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            destination.unlink()
        shutil.copy2(source, destination, follow_symlinks=False)


def prepare(root: Path, agent_id: str, slug: str, source: Path | None) -> Path:
    directory = private_dir(root / "agents" / agent_id)
    work = directory / "work"
    if work.exists():
        return work
    if source and (root.is_relative_to(source) or source.is_relative_to(root)):
        raise ValueError("Source cannot contain or be inside SH41_LOCAL_HOME")
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=directory))
    try:
        target = staging / "work"
        is_git = source is not None and (source / ".git").exists()
        if is_git:
            git("clone", "--no-hardlinks", "--no-local", "--", str(source), str(target))
            git("remote", "remove", "origin", cwd=target)
            files = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=source)
            for name in set(files.stdout.split("\0")) - {""}:
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe path in source repository")
                src, dst = source / relative, target / relative
                if ignored(relative) or (not src.exists() and not src.is_symlink()):
                    if dst.is_dir() and not dst.is_symlink():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink(missing_ok=True)
                else:
                    copy_entry(src, dst, source)
            git("checkout", "-b", f"agent/{slug}", cwd=target)
        else:
            target.mkdir()
            if source:
                for child in source.iterdir():
                    if not ignored(Path(child.name)):
                        copy_entry(child, target / child.name, source)
            git("init", "-b", f"agent/{slug}", cwd=target)
        git("config", "user.name", "sh41 Local Agent", cwd=target)
        git("config", "user.email", "agent@localhost", cwd=target)
        target.rename(work)
    finally:
        shutil.rmtree(staging)
    return work


def export(work: Path, output: Path):
    if output.exists() or output.is_symlink():
        raise ValueError("Export destination already exists; choose a new directory")
    if output.resolve().is_relative_to(work.resolve()):
        raise ValueError("Cannot export into the agent workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sh41-export-", dir=output.parent))
    try:
        for child in work.iterdir():
            if not ignored(Path(child.name)):
                copy_entry(child, staging / child.name, work)
        # mkdir claims the destination before replacing it, without overwriting.
        output.mkdir()
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
