import os
from pathlib import Path

from textual.suggester import Suggester

from .client import background


def complete_directory(value):
    """Complete one path component, preserving the user's spelling and prefix."""
    if not value:
        return None
    try:
        if value == "~":
            return "~/"
        parent, _, prefix = value.rpartition("/")
        directory = Path((parent or "/") if "/" in value else ".").expanduser()
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries
                           if entry.name.startswith(prefix)
                           and (not entry.name.startswith(".") or prefix.startswith("."))
                           and entry.is_dir())
        if names:
            return value[:len(value) - len(prefix)] + names[0] + "/"
    except (OSError, RuntimeError, ValueError):
        pass
    return None


class DirectorySuggester(Suggester):
    def __init__(self):
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value):
        return await background(complete_directory, value)
