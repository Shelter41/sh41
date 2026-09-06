import os

import pytest
from textual.widgets import Input, OptionList, Select

from sh41_local import catalog
from sh41_local.tui.app import Shell
from sh41_local.tui.model_picker import ModelPicker
from test_catalog import catalog_settled
from test_tui import FakeClient, click, settled


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_combined_search_keyboard_mouse_and_variant_reset(monkeypatch, dimensions):
    calls = []

    def search(query, number):
        calls.append(query)
        return ["qwen3", "qwen3-coder"], False

    monkeypatch.setattr(catalog, "search", search)
    monkeypatch.setattr(catalog, "variants", lambda family: [(family + ":4b", "2.5GB"), (family + ":8b", "5GB")])
    app = Shell(FakeClient())
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        await catalog_settled(pilot, wizard, "2 library")
        picker = wizard.query_one("#installed", ModelPicker)
        field = picker.query_one(Input)
        menu = picker.query_one(OptionList)
        picker.scroll_visible()
        picker.focus()
        await pilot.pause()
        assert picker.expanded
        assert picker.region.height == 3
        assert field.parent is picker and not wizard.query("Select#installed")
        await pilot.press("q", "w", "e", "n")
        await pilot.pause(0.5)
        await catalog_settled(pilot, wizard, "2 library")
        assert calls[-1] == "qwen"
        assert menu.option_count == 3
        assert field.has_focus and field.value == "qwen"
        assert menu.region.right <= dimensions[0]
        assert menu.region.bottom <= dimensions[1]
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"model-autocomplete-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await pilot.press("down", "enter")
        await catalog_settled(pilot, wizard, "Choose a variant")
        assert picker.value == "@library/qwen3-coder"
        assert field.value == "qwen3-coder" and not picker.expanded
        variants = wizard.query_one("#library-variant", Select)
        variants.value = "qwen3-coder:4b"
        await pilot.pause()
        assert wizard.make_spec().model == "qwen3-coder:4b"

        picker.focus()
        await pilot.press("home", "shift+end", "l", "o", "c", "a", "l")
        await pilot.pause()
        assert wizard.value("model") == ""
        assert not variants.display
        assert picker.expanded and not picker.value
        await pilot.press("escape")
        await pilot.pause()
        assert not picker.expanded and app.screen is wizard
        await pilot.press("down")
        await pilot.pause()
        assert picker.expanded
        assert menu.get_option_at_index(0).id == "local:4b"
        assert await pilot.click("#model-suggestions", offset=(2, 1))
        await pilot.pause()
        assert picker.value == "local:4b"
        assert wizard.make_spec().model == "local:4b"
        assert not picker.expanded
        picker.open()
        await pilot.press("tab")
        await pilot.pause()
        assert not picker.expanded
        assert not app.client.submissions
