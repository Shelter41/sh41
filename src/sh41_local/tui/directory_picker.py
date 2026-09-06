import os
from pathlib import Path

from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Input, Static, Tree

from .client import background
from .completion import DirectorySuggester
from .dialogs import Dialog


def readable_directory(value):
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Choose an existing directory")
    with os.scandir(path) as entries:
        next(entries, None)
    return path


class Folders(DirectoryTree):
    def filter_paths(self, paths):
        for path in paths:
            try:
                if path.is_dir():
                    yield path
            except OSError:
                continue


class DirectoryPicker(Dialog):
    DEFAULT_CSS = """
    DirectoryPicker > Vertical { width: 88; height: 94%; max-height: 40; }
    DirectoryPicker .dialog-title { margin-bottom: 0; }
    DirectoryPicker .location { height: 3; margin: 0; align-horizontal: left; }
    DirectoryPicker .location Button { min-width: 10; width: 10; }
    DirectoryPicker #directory-path { width: 1fr; }
    DirectoryPicker #directory-tree { height: 1fr; min-height: 3; background: $surface; }
    DirectoryPicker #directory-error { height: auto; max-height: 3; overflow-y: auto; }
    """

    def __init__(self, initial=""):
        super().__init__()
        self.initial = initial
        self.navigation = 0

    def compose(self):
        with Vertical():
            yield Static("Choose source directory", classes="dialog-title")
            with Horizontal(classes="location"):
                yield Button("Root /", id="directory-root")
                yield Button("Home", id="directory-home")
                yield Button("Up", id="directory-up")
            with Horizontal(classes="location"):
                yield Input("/", id="directory-path", suggester=DirectorySuggester())
                yield Button("Go", id="directory-go")
            yield Folders("/", id="directory-tree")
            yield Static("", id="directory-error", classes="error", markup=False)
            with Horizontal():
                yield Button("Cancel", id="directory-cancel")
                yield Button("Choose folder", id="directory-choose", variant="primary")

    def on_mount(self):
        self.query_one("#directory-tree").focus()
        if self.initial:
            self.navigate(self.initial)

    @work(exit_on_error=False)
    async def navigate(self, value, *, choose=False):
        self.navigation += 1
        navigation = self.navigation
        try:
            path = await background(readable_directory, value)
            if navigation != self.navigation or not self.is_mounted:
                return
            if choose:
                self.dismiss(str(path))
                return
            self.query_one("#directory-path", Input).value = str(path)
            self.query_one("#directory-tree", Folders).path = path
            self.query_one("#directory-error", Static).update("")
            self.query_one("#directory-tree").focus()
        except (OSError, ValueError, RuntimeError):
            if navigation == self.navigation and self.is_mounted:
                self.query_one("#directory-error", Static).update("Directory unavailable; check the path and macOS access permissions")

    @on(Tree.NodeHighlighted, "#directory-tree")
    def highlight(self, event):
        if event.node.data:
            self.query_one("#directory-path", Input).value = str(event.node.data.path)

    @on(Input.Submitted, "#directory-path")
    def go(self):
        self.navigate(self.query_one("#directory-path", Input).value)

    @on(Button.Pressed)
    def clicked(self, event):
        event.stop()
        ident = event.button.id
        if ident == "directory-cancel":
            self.action_cancel()
        elif ident == "directory-root":
            self.navigate("/")
        elif ident == "directory-home":
            self.navigate(str(Path.home()))
        elif ident == "directory-up":
            self.navigate(str(self.query_one("#directory-tree", Folders).path.parent))
        elif ident == "directory-go":
            self.go()
        elif ident == "directory-choose":
            self.navigate(self.query_one("#directory-path", Input).value, choose=True)
