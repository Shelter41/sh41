from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def state_home() -> Path:
    return Path(os.environ.get("SH41_LOCAL_HOME", "~/.local/share/sh41-local")).expanduser().resolve()


def private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("State directories cannot be symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def atomic_json(path: Path, data: object) -> None:
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
