import os
import threading
from contextlib import contextmanager

import httpx
import pytest
from textual.widgets import Input, Select, Static

from sh41_local import catalog
from sh41_local.catalog import page as fetch_page
from sh41_local.spec import parse_yaml
from sh41_local.tui.app import Shell
from sh41_local.tui.model_picker import ModelPicker
from test_tui import FakeClient, click, settled


def parsed(html):
    result = catalog.LibraryPage()
    result.feed(html)
    return result


def test_library_parser_filters_cloud_and_unsafe_links(monkeypatch):
    search = parsed('''<a href="/library/qwen3">qwen3 tools 4b 8b</a>
        <a href="/library/remote"><p>18B active parameters</p>
          <span class="inline-flex">cloud</span></a>
        <a href="/library/hybrid"><span class="inline-flex">cloud</span>
          <span class="inline-flex">8b</span></a>
        <a href="https://evil.test/library/other">other</a>
        <a href="/library/../bad">bad</a><a href="/someone/custom">custom</a>
        <li hx-get="/search?page=2"></li>''')
    monkeypatch.setattr(catalog, "page", lambda *a, **kw: search)
    assert catalog.search() == (["qwen3", "hybrid"], True)
    monkeypatch.setattr(catalog, "page", lambda *a, **kw: parsed("<li>No models found.</li>"))
    assert catalog.search("absent") == ([], False)
    tags = parsed('''<a href="/library/qwen3:4b"><span>qwen3:4b</span> 2.5GB</a>
        <a href="/library/qwen3:4b">qwen3:4b</a>
        <a href="/library/qwen3:cloud">qwen3:cloud 9GB</a>
        <a href="/library/qwen3:unknown">unknown</a>
        <a href="/library/other:4b">other:4b 2GB</a>''')
    monkeypatch.setattr(catalog, "page", lambda *a, **kw: tags)
    assert catalog.variants("qwen3") == [("qwen3:4b", "2.5GB")]
    with pytest.raises(ValueError, match="Invalid"):
        catalog.variants("../bad")


@pytest.mark.parametrize("failure", ["timeout", "status", "size"])
def test_library_network_errors_and_limits(monkeypatch, failure):
    @contextmanager
    def response(*args, **kwargs):
        assert args[1] == "https://ollama.com/search"
        assert kwargs["timeout"] == 10
        assert kwargs["headers"] == {"HX-Request": "true"}
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        yield httpx.Response(503 if failure == "status" else 200,
                             content=b"x" * (5_000_001 if failure == "size" else 0),
                             request=httpx.Request("GET", args[1]))
    monkeypatch.setattr(catalog.httpx, "stream", response)
    with pytest.raises(ValueError, match="unavailable|too large"):
        fetch_page("/search")


async def catalog_settled(pilot, wizard, expected):
    for _ in range(50):
        await pilot.pause(0.03)
        if expected in str(wizard.query_one("#library-status", Static).content):
            return
    pytest.fail(str(wizard.query_one("#library-status", Static).content))


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", [(80, 24), (120, 40)])
async def test_library_dropdown_variants_paging_and_save_only(tmp_path, monkeypatch, dimensions):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catalog, "search", lambda query, number: (["qwen3"] if number == 1 else ["coder"], number == 1))
    monkeypatch.setattr(catalog, "variants", lambda family: [(family + ":4b", "2.5GB"), (family + ":8b", "5.2GB")])
    client = FakeClient(0)
    app = Shell(client)
    async with app.run_test(size=dimensions) as pilot:
        await settled(pilot, app)
        app.action_new()
        await pilot.pause()
        wizard = app.screen
        await click(pilot, "#next")
        await catalog_settled(pilot, wizard, "1 library")
        assert not wizard.query("#wizard-ollama-library")
        assert any(value == "@library/qwen3" for _, value in wizard.model_options())
        selector = wizard.query_one("#installed", ModelPicker)
        selector.scroll_visible()
        selector.focus()
        await pilot.press("q", "w", "e", "n", "3")
        await catalog_settled(pilot, wizard, "1 library")
        await pilot.press("enter")
        await catalog_settled(pilot, wizard, "Choose a variant")
        assert wizard.value("model") == ""
        with pytest.raises(ValueError):
            wizard.make_spec()
        variants = wizard.query_one("#library-variant", Select)
        variants.scroll_visible()
        variants.focus()
        await pilot.press("enter", "home", "down", "enter")
        await pilot.pause()
        assert wizard.value("model") == "qwen3:4b"
        variants.clear()
        await pilot.pause()
        assert wizard.value("model") == ""
        variants.value = "qwen3:4b"
        await pilot.pause()
        wizard.update_models(["new:4b"])
        assert selector.value == "@library/qwen3"
        wizard.more_library()
        for _ in range(50):
            await pilot.pause(0.03)
            if len(wizard.library) == 2:
                break
        assert len(wizard.library) == 2
        assert selector.value == "@library/qwen3" and variants.value == "qwen3:4b"
        assert not client.submissions
        assert wizard.query_one("#next").region.bottom <= dimensions[1]
        if os.environ.get("SH41_TEST_SCREENSHOTS"):
            variants.scroll_visible()
            variants.focus()
            await pilot.press("enter")
            await pilot.pause()
            app.save_screenshot(f"library-dropdown-{dimensions[0]}x{dimensions[1]}.svg",
                                path=os.environ["SH41_TEST_SCREENSHOTS"])
            await pilot.press("escape")
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#save")
        manifest = next(tmp_path.glob("*.yaml"))
        assert parse_yaml(manifest.read_text()).model == "qwen3:4b"
        assert not client.submissions


@pytest.mark.asyncio
async def test_library_failure_retry_and_stale_search(monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def search(query, number):
        if query == "slow":
            entered.set()
            release.wait(5)
        return [query or "qwen3"], False

    app = Shell(FakeClient())
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await settled(pilot, app)
            app.action_new()
            await pilot.pause()
            wizard = app.screen
            await click(pilot, "#next")
            await catalog_settled(pilot, wizard, "unavailable")
            assert any(value == "local:4b" for _, value in wizard.model_options())
            monkeypatch.setattr(catalog, "search", search)
            wizard.search_library()
            await catalog_settled(pilot, wizard, "1 library")
            wizard.query_one("#library-search", Input).value = "slow"
            for _ in range(30):
                await pilot.pause(0.03)
                if entered.is_set():
                    break
            assert entered.is_set()
            wizard.query_one("#library-search", Input).value = "fast"
            await pilot.pause(0.5)
            await catalog_settled(pilot, wizard, "1 library")
            release.set()
            await pilot.pause(0.2)
            assert wizard.library == ["fast"]
            assert not app.client.submissions
    finally:
        release.set()
