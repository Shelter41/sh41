import pytest
from textual.widgets import Button, Input, Select, Static

from sh41_local import catalog
from sh41_local.tui.app import Shell
from sh41_local.tui.model_picker import ModelPicker
from test_catalog import catalog_settled
from test_tui import FakeClient, click, settled


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [[], "error", [("fixture:latest", "2GB")],
                                    [("fixture:4b", "2GB"), ("fixture:8b", "5GB")]])
async def test_pull_library_selection_never_requests_manual_tag(monkeypatch, result):
    monkeypatch.setattr(catalog, "search", lambda *args: (["fixture"], False))

    def variants(family):
        if result == "error":
            raise ValueError("Ollama library unavailable")
        return result

    monkeypatch.setattr(catalog, "variants", variants)
    client = FakeClient()
    app = Shell(client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        await catalog_settled(pilot, wizard, "1 library")
        picker = wizard.query_one("#installed", ModelPicker)
        picker.value = "@library/fixture"
        expected = "unavailable" if result == "error" else "No downloadable" if not result else (
            "Download required" if len(result) == 1 else "Choose a variant")
        await catalog_settled(pilot, wizard, expected)
        button = wizard.query_one("#wizard-ollama-pull", Button)
        assert not client.submissions
        if not result or result == "error":
            assert button.disabled and not wizard.value("model")
            wizard.pull_ollama()
            await pilot.pause()
            assert app.screen is wizard
            assert not client.submissions
            return
        if len(result) > 1:
            assert button.disabled
            wizard.query_one("#library-variant", Select).value = result[0][0]
            await pilot.pause()
        assert wizard.value("model") == result[0][0]
        assert not button.disabled
        button.scroll_visible()
        await click(pilot, "#wizard-ollama-pull")
        assert app.screen is not wizard
        assert not app.screen.query(Input)
        assert result[0][0] in str(app.screen.query_one(".dialog-title", Static).content)
        await click(pilot, "#accept")
        assert client.submissions[-1][0] == "models-pull"
        assert client.submissions[-1][1]["model"] == result[0][0]
        assert picker.value == "@library/fixture"


@pytest.mark.asyncio
async def test_downloaded_model_pull_confirmation_can_be_cancelled():
    app = Shell(FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        wizard.query_one("#installed", ModelPicker).value = "local:4b"
        await pilot.pause()
        wizard.pull_ollama()
        await pilot.pause()
        assert not app.screen.query(Input)
        await click(pilot, "#cancel")
        assert app.screen is wizard and not app.client.submissions
