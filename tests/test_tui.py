import pytest
from click.testing import CliRunner
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static

from sh41_local.cli import main
from sh41_local.spec import parse_yaml
from sh41_local.tui.app import Shell
from sh41_local.tui.wizard import Wizard, references


class FakeClient:
    def __init__(self, count=2):
        self.fail = False
        self.calls = []
        self.submissions = []
        self.rows = [{"slug": f"agent-{i:03}", "harness": "opencode", "model": "local:4b",
            "provider": "ollama", "workspace": "default", "state": "deployed", "activity": "idle",
            "harness_state": "ready", "recorded_state": "deployed", "observed_at": "2026-09-06T00:00:00Z"}
                     for i in range(count)]

    def call(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError("Disconnected")
        if payload["op"] == "snapshot":
            return {"agents": self.rows, "docker": {"state": "ready"}, "operations": []}
        if payload["op"] == "models-snapshot":
            return {"state": "ready", "owned": False, "url": "http://localhost:11434", "version": "test",
                    "models": [{"name": "local:4b", "size": 1024 ** 3}], "loaded": []}
        if payload["op"] == "inspect-source":
            from sh41_local.directories import inspect_directory
            return dict(inspect_directory(payload["path"]), agents=[])
        return []

    def submit(self, kind, **fields):
        self.submissions.append((kind, fields))
        return {"status": "pending"}


async def settled(pilot, app):
    for _ in range(30):
        await pilot.pause(0.02)
        if not app.polling:
            return
    pytest.fail("UI polling did not finish")


async def click(pilot, selector):
    await pilot.pause(0.2)
    assert await pilot.click(selector)
    await pilot.pause(0.2)


def test_noninteractive_help_and_explicit_shell_requirement():
    runner = CliRunner()
    assert runner.invoke(main, []).exit_code == 0
    result = runner.invoke(main, ["shell"])
    assert result.exit_code == 1
    assert "interactive terminal" in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_dashboard_navigation_filter_selection_and_models(dimensions):
    client = FakeClient(100)
    app = Shell(client)
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        assert app.title == "Shelter41" and app.theme == "shelter41"
        assert app.home.styles.background.hex == "#050504"
        header = app.q("Header")
        assert header.region.y == 0 and header.region.height == 3
        assert "Shelter41" in str(app.q("HeaderTitle", Static).content)
        table = app.q("#agents-table", DataTable)
        assert table.row_count == 100
        table.move_cursor(row=40)
        await pilot.pause()
        assert app.selected() == "agent-040"
        app.action_refresh()
        await settled(pilot, app)
        assert app.selected() == "agent-040"
        app.q("#filter", Input).value = "agent-099"
        await pilot.pause()
        assert table.row_count == 1 and app.selected() == "agent-099"
        assert app.q("#models-table", DataTable).row_count == 1
        assert app.q("#models-stop", Button).disabled
        assert app.q("#agent-controls").region.bottom <= dimensions[1]
        client.fail = True
        app.action_refresh()
        await settled(pilot, app)
        assert "stale" in str(app.q("#health", Static).content)
        client.fail = False
        app.action_refresh()
        await settled(pilot, app)
        assert "Docker: ready" in str(app.q("#health", Static).content)
        import os
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"shell-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])


@pytest.mark.asyncio
async def test_wizard_save_only_back_navigation_and_collision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = FakeClient(0)
    app = Shell(client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        await pilot.press("ctrl+n")
        await pilot.pause()
        wizard = app.screen
        assert isinstance(wizard, Wizard)
        wizard.query_one("#name", Input).value = "reviewer"
        await click(pilot, "#next")
        wizard.query_one("#model", Input).value = "local:4b"
        import os
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot("wizard-80x24.svg", path=os.environ["SH41_TEST_SCREENSHOTS"])
        await click(pilot, "#next")
        await click(pilot, "#back")
        assert wizard.query_one("#model", Input).value == "local:4b"
        await click(pilot, "#next")
        await click(pilot, "#next")
        assert wizard.step == 3
        (tmp_path / "reviewer.yaml").write_text("original")
        await click(pilot, "#save")
        assert "exists" in str(wizard.query_one("#wizard-error", Static).content)
        assert (tmp_path / "reviewer.yaml").read_text() == "original"
        wizard.query_one("#destination", Input).value = str(tmp_path / "new.yaml")
        await click(pilot, "#save")
        assert app.screen is app.home
        spec = parse_yaml((tmp_path / "new.yaml").read_text())
        assert spec.agent == "reviewer" and spec.model == "local:4b"
        assert not client.submissions


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_wizard_invalid_combination_and_save_start(tmp_path, monkeypatch, dimensions):
    monkeypatch.chdir(tmp_path)
    client = FakeClient(0)
    app = Shell(client)
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        await click(pilot, "#next")
        assert wizard.step == 1
        assert "requires" in str(wizard.query_one("#wizard-error", Static).content)
        wizard.query_one("#harness", Select).value = "codex"
        await pilot.pause()
        assert wizard.query_one("#provider", Select).value == "native"
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#deploy")
        await pilot.pause()
        assert client.submissions[0][0] == "deploy-start"
        assert len(list(tmp_path.glob("*.yaml"))) == 1


def test_mcp_reference_validation():
    assert references("Authorization=TOOLS_KEY") == {"Authorization": {"env": "TOOLS_KEY"}}
    with pytest.raises(ValueError):
        references("TOKEN=ONE\nTOKEN=TWO")


@pytest.mark.asyncio
async def test_close_does_not_wait_for_slow_status_request():
    import threading
    import time
    release = threading.Event()
    entered = threading.Event()

    class SlowClient(FakeClient):
        def call(self, payload):
            entered.set()
            release.wait(20)
            return super().call(payload)

    try:
        started = time.monotonic()
        app = Shell(SlowClient())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.1)
            assert entered.is_set()
            await pilot.press("ctrl+q")
        assert time.monotonic() - started < 3
    finally:
        release.set()


@pytest.mark.asyncio
async def test_structured_mcp_editor(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    from sh41_local.tui.wizard import MCPForm
    monkeypatch.chdir(tmp_path)
    app = Shell(FakeClient(0))
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        wizard.query_one("#model", Input).value = "local:4b"
        await click(pilot, "#next")
        await click(pilot, "#add-mcp")
        form = app.screen
        assert isinstance(form, MCPForm)
        form.query_one("#mcp-name", Input).value = "tools"
        form.query_one("#mcp-command", Input).value = '["python", "tools.py"]'
        form.query_one("#mcp-refs", TextArea).load_text("TOKEN=MY_TOKEN")
        await click(pilot, "#mcp-add")
        assert wizard.mcp["tools"]["command"] == ["python", "tools.py"]
        await click(pilot, "#add-mcp")
        form = app.screen
        form.query_one("#mcp-name", Input).value = "http-tools"
        form.query_one("#mcp-transport", Select).value = "http"
        await pilot.pause()
        form.query_one("#mcp-url", Input).value = "https://tools.example/mcp"
        form.query_one("#mcp-refs", TextArea).load_text("Authorization=MY_TOKEN")
        await click(pilot, "#mcp-add")
        assert wizard.make_spec().secret_names() == {"MY_TOKEN"}


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_wizard_directory_completion_and_model_dropdown(tmp_path, monkeypatch, dimensions):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "My project").mkdir()
    app = Shell(FakeClient(0))
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        source = wizard.query_one("#source", Input)
        source.focus()
        await pilot.press(".", "/", "M", "y")
        await pilot.pause(0.2)
        import os
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"directory-completion-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await pilot.press("right")
        assert source.value == "./My project/"
        await pilot.pause(0.5)
        wizard.query_one("#source-mode", Select).value = "copy"
        await click(pilot, "#next")
        selector = wizard.query_one("#installed", Select)
        selector.focus()
        await pilot.press("enter")
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"model-dropdown-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await pilot.press("home", "enter")
        await pilot.pause()
        assert selector.value == "local:4b"
        assert wizard.value("model") == "local:4b"
        assert not wizard.query_one("#model").display
        assert wizard.make_spec().model == "local:4b"
        assert wizard.query_one("#next").region.bottom <= dimensions[1]

        selector.value = ""
        await pilot.pause()
        assert wizard.query_one("#model").display
        wizard.query_one("#model", Input).value = "custom:8b"
        wizard.update_models(["new:4b", "local:4b", "new:4b", None])
        await pilot.pause()
        assert wizard.models == ["local:4b", "new:4b"]
        assert wizard.value("model") == "custom:8b"
        selector.value = "new:4b"
        await pilot.pause()
        assert wizard.value("model") == "new:4b"
        wizard.update_models([])
        await pilot.pause()
        assert selector.value == "" and wizard.query_one("#model").display
        assert wizard.value("model") == "new:4b"

        wizard.query_one("#provider", Select).value = "openai-compatible"
        await pilot.pause()
        assert not selector.display and wizard.query_one("#model").display
        wizard.query_one("#model", Input).value = "remote-model"
        wizard.query_one("#provider", Select).value = "ollama"
        await pilot.pause()
        assert selector.display and selector.value == ""
        assert wizard.value("model") == "remote-model"
        wizard.query_one("#harness", Select).value = "codex"
        await pilot.pause()
        assert not selector.display and wizard.query_one("#model").display


@pytest.mark.asyncio
async def test_model_inventory_refreshes_open_wizard():
    app = Shell(FakeClient(0))
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        wizard.update_models([])
        app.refresh_models()
        await settled(pilot, app)
        assert wizard.models == ["local:4b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_ollama_unavailable_empty_and_download_actions(dimensions, monkeypatch):
    client = FakeClient(0)
    opened = []
    monkeypatch.setattr("sh41_local.tui.wizard.webbrowser.open", lambda url, **kwargs: opened.append(url) or True)
    app = Shell(client)
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        wizard.update_model_snapshot({"state": "unreachable", "url": "http://localhost:11434", "models": None})
        assert "unavailable" in str(wizard.query_one("#ollama-status", Static).content)
        assert not client.submissions
        assert not opened
        wizard.query_one("#wizard-ollama-library").scroll_visible()
        await click(pilot, "#wizard-ollama-library")
        assert opened == ["https://ollama.com/search?c=tools"]
        import os
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"ollama-unavailable-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        assert not wizard.query_one("#wizard-ollama-start", Button).disabled
        wizard.query_one("#wizard-ollama-start").scroll_visible()
        await click(pilot, "#wizard-ollama-start")
        assert client.submissions[-1][0] == "models-start"
        wizard.update_model_snapshot({"state": "ready", "url": "http://localhost:11434", "models": []})
        assert "No downloaded models" in str(wizard.query_one("#ollama-status", Static).content)
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"ollama-empty-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        assert wizard.query_one("#wizard-ollama-start", Button).disabled
        wizard.query_one("#wizard-ollama-pull").scroll_visible()
        await click(pilot, "#wizard-ollama-pull")
        app.screen.query_one("#answer", Input).value = "fixture:4b"
        await click(pilot, "#accept")
        assert client.submissions[-1] == ("models-pull", {"ident": client.submissions[-1][1]["ident"], "model": "fixture:4b"})
        assert wizard.value("model") == "fixture:4b"
        wizard.update_model_snapshot({"state": "ready", "models": [{"name": "fixture:4b"}]})
        assert wizard.models == ["fixture:4b"]
        wizard.update_model_snapshot({"state": "ready", "models": None})
        assert "inventory unavailable" in str(wizard.query_one("#ollama-status", Static).content)
        assert wizard.value("model") == "fixture:4b"
        wizard.query_one("#provider", Select).value = "openai-compatible"
        await pilot.pause()
        assert not wizard.query_one("#ollama-controls").display
        assert not wizard.query_one("#ollama-status").display


async def directory_settled(pilot, wizard):
    for _ in range(50):
        await pilot.pause(0.05)
        if wizard.source_info:
            return
    pytest.fail("Directory inspection did not complete")


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_wizard_non_git_requires_choice_and_shared_ack(tmp_path, monkeypatch, dimensions):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()

    class SharingClient(FakeClient):
        def call(self, payload):
            result = super().call(payload)
            if payload["op"] == "inspect-source":
                result["agents"] = [dict(id="peer-id", agent="editor", mode="direct", state="paused", shared=True)]
            return result

    client = SharingClient(0)
    app = Shell(client)
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        wizard.query_one("#source", Input).value = str(folder)
        await directory_settled(pilot, wizard)
        await click(pilot, "#next")
        assert wizard.step == 0
        assert "Choose" in str(wizard.query_one("#wizard-error", Static).content)
        assert "editor: paused" in str(wizard.query_one("#source-info", Static).content)
        selector = wizard.query_one("#source-mode", Select)
        selector.value = "direct"
        await pilot.pause()
        assert wizard.query_one("#shared-folder").display
        await click(pilot, "#next")
        assert wizard.step == 0
        assert "Confirm shared" in str(wizard.query_one("#wizard-error", Static).content)
        checkbox = wizard.query_one("#shared-folder", Checkbox)
        checkbox.focus()
        await pilot.press("space")
        import os
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"shared-directory-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await click(pilot, "#next")
        assert wizard.step == 1
        await click(pilot, "#back")
        assert selector.value == "direct" and checkbox.value
        await click(pilot, "#next")
        wizard.query_one("#model", Input).value = "local:4b"
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#deploy")
        assert client.submissions[0][1]["shared_ack"] == ["peer-id"]
        assert client.submissions[0][1]["spec"].source.mode == "direct"
        assert not list(folder.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_wizard_repository_notice_and_save_only(tmp_path, monkeypatch, dimensions):
    import subprocess
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init"], ["-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "commit", "--allow-empty", "-m", "initial"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "dirty").write_text("local only")
    app = Shell(FakeClient(0))
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        wizard.query_one("#source", Input).value = str(repo)
        await directory_settled(pilot, wizard)
        notice = str(wizard.query_one("#source-info", Static).content)
        assert "New worktree" in notice and "Uncommitted changes stay" in notice
        assert not wizard.query_one("#source-mode").display
        wizard.query_one("#source-info").scroll_visible()
        await pilot.pause()
        import os
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"repository-directory-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await click(pilot, "#next")
        wizard.query_one("#harness", Select).value = "codex"
        await pilot.pause()
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#save")
        manifest = next(tmp_path.glob("*.yaml"))
        assert parse_yaml(manifest.read_text()).source.mode == "worktree"
        assert not app.client.submissions
        listed = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                                check=True, capture_output=True, text=True).stdout
        assert listed.count("worktree ") == 1


@pytest.mark.asyncio
async def test_directory_inspection_failure_and_stale_reply(tmp_path):
    import threading
    release, entered = threading.Event(), threading.Event()
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()

    class SlowDirectoryClient(FakeClient):
        def call(self, payload):
            if payload["op"] == "inspect-source" and payload["path"] == str(one):
                entered.set()
                release.wait(10)
            return super().call(payload)

    try:
        app = Shell(SlowDirectoryClient(0))
        async with app.run_test(size=(80, 24)) as pilot:
            await settled(pilot, app)
            app.action_new()
            await pilot.pause()
            wizard = app.screen
            field = wizard.query_one("#source", Input)
            field.value = str(one)
            await pilot.pause(0.4)
            assert entered.is_set()
            field.value = str(two)
            await directory_settled(pilot, wizard)
            assert wizard.source_info["path"] == str(two)
            release.set()
            await pilot.pause(0.2)
            assert wizard.source_info["path"] == str(two)
            field.value = str(tmp_path / "missing")
            await pilot.pause(0.5)
            await click(pilot, "#next")
            assert wizard.step == 0 and wizard.source_info is None
            assert "existing" in str(wizard.query_one("#source-info", Static).content)
    finally:
        release.set()
