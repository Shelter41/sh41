# Adapted for sh41 Local from Shelter41 1d085945; package-local imports and local persistence.
"""Native transcript lookup and archive helpers retained for driver compatibility.

Local execution persists the native home directly. No archive is uploaded and
there is no backend transport. Archive helpers are not used by the local manager.
"""
from __future__ import annotations

import hashlib
import io
import logging
import tarfile
from pathlib import Path


from .claude_jsonl import project_dir_for_cwd

logger = logging.getLogger("hatchery.manager.session_store")

# Preserve the original archive size guard for the retained driver contract.
MAX_SNAPSHOT_BYTES = 200 * 1024 * 1024


def session_paths(cwd: str | Path, session_ids: list[str] | tuple[str, ...],
                  *, home: str | Path | None = None) -> list[Path]:
    """Existing transcript files/dirs for the given session ids, in the cwd's project dir."""
    project_dir = project_dir_for_cwd(cwd, home=home)
    found: list[Path] = []
    seen: set[str] = set()
    for session_id in session_ids:
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        jsonl = project_dir / f"{session_id}.jsonl"
        if jsonl.is_file():
            found.append(jsonl)
        subdir = project_dir / session_id
        if subdir.is_dir():
            found.append(subdir)
    return found


def pack(cwd: str | Path, session_ids: list[str] | tuple[str, ...],
         *, home: str | Path | None = None) -> bytes | None:
    """tar.gz of one session's transcripts, or None when there is nothing to send.

    Members are stored relative to the project dir's parent so a restore lands
    them back under the same ``projects/<encoded-cwd>/`` regardless of where the
    Claude config directory sits.
    """
    paths = session_paths(cwd, session_ids, home=home)
    if not paths:
        return None
    return pack_paths(paths, root=paths[0].parent.parent)


def pack_paths(paths: list[Path] | tuple[Path, ...], *, root: Path) -> bytes | None:
    """tar.gz of ``paths``, stored relative to ``root``. None if there is nothing.

    ``root`` decides where a restore lands the members, so it must be the
    directory the harness resolves its transcripts against — the project dir's
    parent for Claude, the sessions root for Codex and Kimi.
    """
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in existing:
            archive.add(path, arcname=str(path.relative_to(root)))
    data = buffer.getvalue()
    if len(data) > MAX_SNAPSHOT_BYTES:
        logger.warning(
            "Session snapshot is %d bytes (> %d); not uploading",
            len(data), MAX_SNAPSHOT_BYTES,
        )
        return None
    return data


def unpack(data: bytes, cwd: str | Path, *, home: str | Path | None = None) -> list[str]:
    """Restore a packed snapshot into this sandbox. Returns the session ids restored."""
    project_dir = project_dir_for_cwd(cwd, home=home)
    restored = unpack_into(data, project_dir.parent)
    logger.info("Restored session transcript(s) %s into %s", restored, project_dir)
    return restored


def unpack_into(data: bytes, root: Path) -> list[str]:
    """Restore a packed snapshot under ``root``. Returns the transcript stems.

    Members that could escape the destination, or that are not plain files or
    directories, are refused: the archive came back over the network.
    """
    root.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = []
        for member in archive.getmembers():
            name = member.name
            # Refuse anything that could escape the destination.
            if name.startswith("/") or ".." in Path(name).parts:
                logger.warning("Skipping unsafe snapshot member %r", name)
                continue
            if not (member.isfile() or member.isdir()):
                logger.warning("Skipping non-regular snapshot member %r", name)
                continue
            members.append(member)
            # Top-level transcripts only; a nested match is a subagent file.
            if member.isfile() and name.endswith(".jsonl") and len(Path(name).parts) == 2:
                restored.append(Path(name).stem)
        # filter="data" strips ownership/permission surprises (Python 3.12+).
        archive.extractall(path=root, members=members, filter="data")
    return restored


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transcript_path(cwd: str | Path, session_id: str,
                    *, home: str | Path | None = None) -> Path:
    project_dir = project_dir_for_cwd(cwd, home=home)
    return project_dir / f"{session_id}.jsonl"


def has_transcript(cwd: str | Path, session_id: str,
                   *, home: str | Path | None = None) -> bool:
    if not session_id:
        return False
    return transcript_path(cwd, session_id, home=home).is_file()


def newest_session_id(cwd: str | Path, *, home: str | Path | None = None,
                      after_mtime: float | None = None) -> str | None:
    """Session id of the newest transcript in this cwd's project dir.

    Used after a run to notice that the CLI forked the conversation onto a new
    id, which would otherwise leave the next resume pointing at a stale file.
    """
    from .claude_jsonl import discover_newest_jsonl

    project_dir = project_dir_for_cwd(cwd, home=home)
    newest = discover_newest_jsonl(project_dir, after_mtime=after_mtime)
    return newest.stem if newest else None
