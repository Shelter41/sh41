import os

import pytest
from textual.widgets import Button, DataTable, Input, OptionList, Static, TabbedContent

from sh41_local.tui.app import Shell
from test_tui import FakeClient, click, settled


class SidebarClient(FakeClient):
    def __init__(self):
        super().__init__(20)
        self.rows[1]["state"] = "paused"
        self.rows[2]["slug"] = "agent-with-a-very-long-name"
        self.state = "ready"
        self.operations = []

    def call(self, payload):
        result = super().call(payload)
        if payload["op"] == "snapshot":
            result["operations"] = self.operations
        if payload["op"] == "models-snapshot":
            result["state"] = self.state
            result["loaded"] = [{"name": "a-long-loaded-model-name:8b", "size": 2 * 1024 ** 3}]
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_sidebar_status_layout_and_keyboard_navigation(dimensions):
    client = SidebarClient()
    app = Shell(client)
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        await pilot.pause()
        sidebar = app.q("#sidebar")
        assert sidebar.region.width == 24
        assert sidebar.region.right <= app.q("#views").region.x
        assert sidebar.region.bottom <= dimensions[1] - 2
        assert "Supervisor: up\nDocker: up\nOllama: up" == str(app.q("#sidebar-runtime", Static).content)
        assert str(app.q("#sidebar-agents-nav", Button).label) == "Agents (19 up)"
        assert app.q("#sidebar-ollama-start", Button).disabled
        agents = app.q("#sidebar-agents", OptionList)
        models = app.q("#sidebar-models", OptionList)
        assert agents.option_count == 20
        assert "paused" in str(agents.get_option_at_index(1).prompt)
        assert models.option_count == 1
        assert models.get_option_at_index(0).id == "a-long-loaded-model-name:8b"
        assert len(str(models.get_option_at_index(0).prompt).splitlines()) == 2
        assert len(str(models.get_option_at_index(0).prompt).splitlines()[0]) <= 19
        assert "2.0 GiB" in str(models.get_option_at_index(0).prompt)
        assert models.region.bottom <= sidebar.region.bottom
        assert not client.submissions

        app.q("#filter", Input).value = "agent-019"
        agents.focus()
        await pilot.press("home", "down", "enter")
        await pilot.pause()
        assert app.selected() == "agent-001"
        assert app.q("#filter", Input).value == ""
        agents.highlighted = 15
        app.action_refresh()
        await settled(pilot, app)
        assert agents.highlighted == 15

        models.focus()
        await pilot.press("home", "enter")
        await pilot.pause()
        assert app.q("#views", TabbedContent).active == "models-view"
        table = app.q("#models-table", DataTable)
        assert table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value == "a-long-loaded-model-name:8b"
        for selector in ("#models-start", "#models-pull", "#models-stop"):
            assert app.q(selector).region.right <= dimensions[0]
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"sidebar-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await click(pilot, "#sidebar-agents-nav")
        assert app.q("#views", TabbedContent).active == "agents-view"


@pytest.mark.asyncio
async def test_sidebar_start_empty_and_stale_states():
    client = SidebarClient()
    client.rows = []
    client.state = "unreachable"
    app = Shell(client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        assert "Ollama: down" in str(app.q("#sidebar-runtime", Static).content)
        assert app.q("#sidebar-agents-empty").display
        assert str(app.q("#sidebar-agents-empty", Static).content) == "No agents"
        assert not app.q("#sidebar-ollama-start", Button).disabled
        assert not client.submissions
        await click(pilot, "#sidebar-ollama-start")
        assert [kind for kind, _ in client.submissions] == ["models-start"]
        client.operations = [{"id": "operation", "resource": "_models", "kind": "models-start",
                              "status": "running", "progress": "Starting"}]
        app.action_refresh()
        await settled(pilot, app)
        assert app.q("#sidebar-ollama-start", Button).disabled
        assert str(app.q("#sidebar-ollama-start", Button).label) == "Starting..."

        client.fail = True
        app.action_refresh()
        await settled(pilot, app)
        assert "Ollama: stale" in str(app.q("#sidebar-runtime", Static).content)
        assert str(app.q("#sidebar-agents-nav", Button).label) == "Agents (? up)"
        assert "stale" in str(app.q("#sidebar-models", OptionList).get_option_at_index(0).prompt)
        client.fail = False
        client.operations = []
        client.state = "ready"
        app.action_refresh()
        await settled(pilot, app)
        assert "Ollama: up" in str(app.q("#sidebar-runtime", Static).content)
        assert app.q("#sidebar-ollama-start", Button).disabled
