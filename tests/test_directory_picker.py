import os
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from sh41_local.tui.app import Shell
from sh41_local.tui.directory_picker import DirectoryPicker, Folders, readable_directory
from test_tui import FakeClient, click, directory_settled, settled


def test_readable_directory_and_directory_only_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file").write_text("not a directory")
    assert readable_directory("~") == tmp_path
    assert list(Folders.filter_paths(None, tmp_path.iterdir())) == [tmp_path / ".hidden"]
    with pytest.raises(ValueError, match="directory"):
        readable_directory(tmp_path / "file")


def test_permission_failure_is_not_a_valid_selection(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError("denied")

    monkeypatch.setattr("sh41_local.tui.directory_picker.os.scandir", denied)
    with pytest.raises(PermissionError):
        readable_directory(tmp_path)


async def tree_settled(pilot, picker, path, child=None):
    for _ in range(50):
        await pilot.pause(0.05)
        tree = picker.query_one(Folders)
        if tree.path == path and (child is None or any(n.data.path.name == child for n in tree.root.children)):
            return tree
    pytest.fail("Directory tree did not load")


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_browse_home_root_up_hidden_and_select(tmp_path, monkeypatch, dimensions):
    home = tmp_path / "home"
    home.mkdir()
    (home / "Applications").mkdir()
    (home / "Projects").mkdir()
    (home / ".hidden").mkdir()
    (home / "file.txt").write_text("not shown")
    sibling = tmp_path / "other project"
    sibling.mkdir()
    monkeypatch.setenv("HOME", str(home))
    app = Shell(FakeClient(0))
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        wizard.query_one("#source", Input).value = "~"
        wizard.query_one("#source-browse").scroll_visible()
        await click(pilot, "#source-browse")
        picker = app.screen
        assert isinstance(picker, DirectoryPicker)
        tree = await tree_settled(pilot, picker, home, "Projects")
        names = {node.data.path.name for node in tree.root.children}
        assert names == {"Applications", "Projects", ".hidden"}
        assert picker.query_one("#directory-choose").region.bottom <= dimensions[1]
        assert tree.region.height >= 5
        for ident in ("#directory-root", "#directory-home"):
            button = picker.query_one(ident)
            assert button.content_size.width >= len(str(button.label))
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"directory-browser-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await click(pilot, "#directory-root")
        await tree_settled(pilot, picker, Path("/"))
        await click(pilot, "#directory-home")
        await tree_settled(pilot, picker, home, "Projects")
        await click(pilot, "#directory-up")
        tree = await tree_settled(pilot, picker, tmp_path, "other project")
        node = next(n for n in tree.root.children if n.data.path == sibling)
        tree.move_cursor(node)
        tree.focus()
        await pilot.pause()
        assert picker.query_one("#directory-path", Input).value == str(sibling)
        await click(pilot, "#directory-choose")
        assert app.screen is wizard
        assert wizard.query_one("#source", Input).value == str(sibling)
        await directory_settled(pilot, wizard)
        assert wizard.source_info["path"] == str(sibling)
        assert not app.client.submissions
        assert not list(sibling.iterdir())


@pytest.mark.asyncio
async def test_typed_location_errors_and_cancel_preserve_source(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    app = Shell(FakeClient(0))
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        wizard.query_one("#source", Input).value = str(folder)
        wizard.query_one("#source-browse").scroll_visible()
        await click(pilot, "#source-browse")
        picker = app.screen
        await tree_settled(pilot, picker, folder)
        location = picker.query_one("#directory-path", Input)
        location.value = str(tmp_path / "missing")
        await click(pilot, "#directory-choose")
        assert app.screen is picker
        assert "unavailable" in str(picker.query_one("#directory-error", Static).content)
        location.value = str(tmp_path)
        location.focus()
        await pilot.press("enter")
        await tree_settled(pilot, picker, tmp_path, "folder")
        await click(pilot, "#directory-cancel")
        assert wizard.query_one("#source", Input).value == str(folder)
