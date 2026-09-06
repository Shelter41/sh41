import os

import pytest
from textual.widgets import Input, Select

from sh41_local import catalog
from sh41_local.docker import DockerProvider
from sh41_local.drivers.claude_driver import ClaudeDriver
from sh41_local.manifests import build_spec
from sh41_local.spec import parse_yaml
from sh41_local.tui.app import Shell
from sh41_local.tui.native_models import NATIVE_MODELS
from sh41_local.tui.wizard import Wizard
from test_tui import FakeClient, click, settled


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
@pytest.mark.parametrize("harness,model", [("claude-code", "opus"), ("codex", "gpt-5.6-sol")])
async def test_native_choices_and_saved_yaml(tmp_path, monkeypatch, dimensions, harness, model):
    monkeypatch.chdir(tmp_path)
    requests = []
    monkeypatch.setattr(catalog, "search", lambda *args: (requests.append(args) or [], False))
    app = Shell(FakeClient())
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        wizard.query_one("#harness", Select).value = harness
        await pilot.pause()
        await click(pilot, "#next")
        selector = wizard.query_one("#native-model", Select)
        assert selector.display and selector.value == ""
        provider = wizard.query_one("#provider", Select)
        assert provider.disabled and provider.value == "native"
        assert wizard.make_spec().model is None
        for ident in ("#installed", "#library-variant", "#library-search-controls", "#ollama-controls",
                      "#model", "#endpoint", "#endpoint-label"):
            assert not wizard.query_one(ident).display
        assert not requests
        selector.scroll_visible()
        selector.focus()
        await pilot.press("enter")
        await pilot.pause()
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"{harness}-models-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await pilot.press("home", "down", "down", "enter")
        await pilot.pause()
        assert selector.value in {value for _, value in NATIVE_MODELS[harness]}
        selector.value = model
        await pilot.pause()
        assert wizard.make_spec().model == model
        wizard.query_one("#model", Input).value = "qwen3:4b"
        assert wizard.make_spec().model == model
        assert wizard.query_one("#next").region.bottom <= dimensions[1]
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#save")
        saved = parse_yaml(next(tmp_path.glob("*.yaml")).read_text())
        assert saved.harness == harness and saved.model == model
        assert saved.inference.provider == "native"
        assert not app.client.submissions


@pytest.mark.parametrize("label,model", NATIVE_MODELS["claude-code"])
def test_claude_choices_roundtrip_and_reach_harness_unchanged(label, model):
    spec = build_spec(name="reviewer", harness="claude-code", model=model)
    saved = parse_yaml(spec.as_yaml())
    assert saved.model == (model or None)
    argv = ClaudeDriver.build_argv(model=saved.model, system_prompt="")
    if model:
        assert argv[argv.index("--model") + 1] == model
    else:
        assert "--model" not in argv


def test_claude_catalog_includes_active_versions_and_no_retired_models():
    values = [value for _, value in NATIVE_MODELS["claude-code"]]
    assert len(values) == len(set(values))
    assert {value for value in values if value.startswith("claude-") and "[" not in value} == {
        "claude-fable-5-1", "claude-fable-5", "claude-opus-5", "claude-opus-4-8",
        "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5-20251101",
        "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
    }
    assert {"", "best", "fable", "opus", "sonnet", "haiku", "opusplan",
            "fable[1m]", "opus[1m]", "sonnet[1m]"} <= set(values)


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_full_claude_dropdown_scroll_and_fable_yaml(tmp_path, monkeypatch, dimensions):
    monkeypatch.chdir(tmp_path)
    app = Shell(FakeClient())
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.push_screen(Wizard(spec=build_spec(name="reviewer", harness="claude-code")))
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        selector = wizard.query_one("#native-model", Select)
        selector.scroll_visible()
        selector.focus()
        await pilot.press("enter", "end")
        await pilot.pause()
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            app.save_screenshot(f"claude-models-end-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
        await pilot.press("enter")
        await pilot.pause()
        assert wizard.make_spec().model == "opusplan"
        selector.value = "claude-fable-5-1"
        await pilot.pause()
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#save")
        saved = parse_yaml(next(tmp_path.glob("*.yaml")).read_text())
        assert saved.model == "claude-fable-5-1"
        assert not app.client.submissions


@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_DOCKER") != "1", reason="Set SH41_TEST_DOCKER=1")
def test_claude_runner_supports_fable_without_inference(tmp_path):
    provider = DockerProvider(tmp_path)
    image = provider.ensure_image("claude-code")
    command = ["run", "--rm", "--network=none", image, "claude"]
    assert provider.command([*command, "--version"]).stdout.startswith("2.1.263")
    assert "--model" in provider.command([*command, "--help"]).stdout


@pytest.mark.asyncio
async def test_harness_switch_restores_only_its_own_model_endpoint_and_key():
    app = Shell(FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        harness = wizard.query_one("#harness", Select)
        provider = wizard.query_one("#provider", Select)
        provider.value = "openai-compatible"
        await pilot.pause()
        wizard.query_one("#model", Input).value = "custom-model"
        wizard.query_one("#endpoint", Input).value = "https://inference.example.com/v1"
        wizard.query_one("#key-env", Input).value = "MODEL_KEY"
        for name, model, key in (("claude-code", "sonnet", "ANTHROPIC_API_KEY"),
                                 ("codex", "gpt-6-astra", "OPENAI_API_KEY")):
            harness.value = name
            await pilot.pause()
            assert wizard.make_spec().model is None
            assert not wizard.value("endpoint") and not wizard.value("key-env")
            wizard.query_one("#native-model", Select).value = model
            wizard.query_one("#key-env", Input).value = key
            await pilot.pause()
        harness.value = "claude-code"
        await pilot.pause()
        assert wizard.make_spec().model == "sonnet"
        assert wizard.value("key-env") == "ANTHROPIC_API_KEY"
        harness.value = "codex"
        await pilot.pause()
        assert wizard.make_spec().model == "gpt-6-astra"
        assert wizard.value("key-env") == "OPENAI_API_KEY"
        harness.value = "opencode"
        await pilot.pause()
        assert provider.value == "openai-compatible" and not provider.disabled
        assert wizard.make_spec().model == "custom-model"
        assert wizard.value("endpoint") == "https://inference.example.com/v1"
        assert wizard.value("key-env") == "MODEL_KEY"


@pytest.mark.asyncio
async def test_existing_unlisted_model_requires_explicit_native_choice():
    initial = build_spec(name="reviewer", harness="codex", model="future-native-model")
    app = Shell(FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot, app)
        app.push_screen(Wizard(spec=initial))
        await pilot.pause()
        wizard = app.screen
        assert wizard.query_one("#native-model", Select).value is Select.NULL
        with pytest.raises(ValueError, match="Choose a native model"):
            wizard.make_spec()
        wizard.query_one("#native-model", Select).value = ""
        await pilot.pause()
        assert wizard.make_spec().model is None
        assert initial.model == "future-native-model"
