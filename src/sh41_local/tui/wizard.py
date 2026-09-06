import json
import secrets
from pathlib import Path

from textual import on, work
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, ContentSwitcher, Input, Label, Select, Static, TextArea

from ..credentials import import_native
from ..manifests import build_spec, write_manifest
from ..paths import state_home
from ..spec import parse_yaml
from .dialogs import Dialog, Prompt
from .client import background
from .completion import DirectorySuggester
from .directory_picker import DirectoryPicker


def references(text):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.strip().partition("=")
        if not separator or not key or not value or key in result:
            raise ValueError("Secret mappings require unique NAME=ENV_REFERENCE lines")
        result[key] = {"env": value}
    return result


class MCPForm(Dialog):
    DEFAULT_CSS = """
    MCPForm > Vertical { height: 92%; max-height: 34; }
    MCPForm VerticalScroll { height: 1fr; max-height: 100%; }
    MCPForm TextArea { height: 4; }
    """
    def compose(self):
        with Vertical():
            yield Static("Add MCP server", classes="dialog-title")
            with VerticalScroll():
                yield Label("Name")
                yield Input(id="mcp-name")
                yield Label("Transport")
                yield Select([("stdio", "stdio"), ("HTTP", "http")], value="stdio",
                             allow_blank=False, id="mcp-transport")
                yield Label("Command arguments (JSON array)", id="command-label")
                yield Input(id="mcp-command", placeholder='["npx", "-y", "your-mcp-server"]')
                yield Label("URL", id="url-label")
                yield Input(id="mcp-url")
                yield Label("Environment references: NAME=ENV_REFERENCE", id="refs-label")
                yield TextArea(id="mcp-refs")
                yield Static("", classes="error", id="mcp-error", markup=False)
            with Horizontal():
                yield Button("Cancel", id="mcp-cancel")
                yield Button("Add", id="mcp-add", variant="primary")

    def on_mount(self):
        self.transport_changed()

    @on(Select.Changed, "#mcp-transport")
    def transport_changed(self):
        stdio = self.query_one("#mcp-transport", Select).value == "stdio"
        for selector in ("#mcp-command", "#command-label"):
            self.query_one(selector).display = stdio
        for selector in ("#mcp-url", "#url-label"):
            self.query_one(selector).display = not stdio
        self.query_one("#refs-label", Label).update(
            ("Environment" if stdio else "Header") + " references: NAME=ENV_REFERENCE")

    @on(Button.Pressed, "#mcp-cancel")
    def cancel(self):
        self.action_cancel()

    @on(Button.Pressed, "#mcp-add")
    def add(self):
        name = self.query_one("#mcp-name", Input).value.strip()
        transport = self.query_one("#mcp-transport", Select).value
        try:
            refs = references(self.query_one("#mcp-refs", TextArea).text)
            server = {"transport": transport}
            if transport == "stdio":
                server.update(command=json.loads(self.query_one("#mcp-command", Input).value), env=refs)
            else:
                server.update(url=self.query_one("#mcp-url", Input).value.strip(), headers=refs)
            # Route validation errors through the same secret-safe YAML parser.
            spec = build_spec(name="validation", harness="codex", mcp={name: server})
            self.dismiss((name, spec.mcp[name].model_dump(exclude_none=True)))
        except (ValueError, OSError) as exc:
            self.query_one("#mcp-error", Static).update(str(exc))


class Wizard(Dialog):
    DEFAULT_CSS = """
    Wizard > Vertical { width: 88; height: 92%; max-height: 42; }
    Wizard ContentSwitcher { height: 1fr; }
    Wizard VerticalScroll { height: 1fr; max-height: 100%; }
    Wizard Label { margin-top: 1; }
    Wizard TextArea { height: 1fr; min-height: 5; }
    Wizard #wizard-error { max-height: 4; overflow-y: auto; }
    Wizard #auth-import { margin: 1 0 0 0; }
    Wizard #source-controls { height: 3; margin: 0; }
    Wizard #source { width: 1fr; }
    Wizard #source-browse { min-width: 10; width: 10; }
    """

    def __init__(self, models=(), spec=None):
        super().__init__()
        self.models = sorted({model for model in models if isinstance(model, str) and model})
        self.initial = spec
        self.mcp = {k: v.model_dump(exclude_none=True) for k, v in spec.mcp.items()} if spec else {}
        self.step = 0
        self.spec = None
        self.source_info = None
        self.inspection_key = None
        self.inspection_timer = None

    def compose(self):
        spec = self.initial
        with Vertical():
            yield Static("New agent / 1. Identity", id="wizard-title", classes="dialog-title")
            with ContentSwitcher(initial="identity", id="wizard-pages"):
                with VerticalScroll(id="identity"):
                    yield Label("Agent name")
                    yield Input(spec.agent if spec else f"agent-{secrets.token_hex(2)}", id="name")
                    yield Label("Workspace")
                    yield Input(spec.workspace if spec else "default", id="workspace")
                    yield Label("Source directory (optional)")
                    with Horizontal(id="source-controls"):
                        yield Input(spec.source.path if spec and spec.source else "", id="source",
                                    suggester=DirectorySuggester())
                        yield Button("Browse", id="source-browse")
                    yield Static("", id="source-info", markup=False)
                    yield Select([("Original folder", "direct"), ("Private copy", "copy")],
                                 prompt="Choose folder access", id="source-mode")
                    yield Checkbox("Allow shared edits with the listed agents", id="shared-folder")
                with VerticalScroll(id="inference"):
                    yield Label("Harness")
                    yield Select([(s, s) for s in ("opencode", "claude-code", "codex")],
                                 value=spec.harness if spec else "opencode", allow_blank=False, id="harness")
                    yield Label("Inference")
                    yield Select([("Ollama", "ollama"), ("Compatible endpoint", "openai-compatible"),
                                  ("Native provider", "native")], allow_blank=False,
                                 value=spec.inference.provider if spec else "ollama", id="provider")
                    yield Label("Downloaded Ollama model", id="installed-label")
                    yield Select(self.model_options(), value=spec.model if spec and spec.model in self.models else "",
                                 allow_blank=False, id="installed")
                    yield Label("Model name (native default when blank)", id="model-label")
                    yield Input(spec.model or "" if spec else "", id="model")
                    yield Label("Compatible endpoint URL", id="endpoint-label")
                    yield Input(spec.inference.base_url or "" if spec else "", id="endpoint")
                    yield Label("API key environment reference (optional)")
                    yield Input(spec.inference.api_key.env if spec and spec.inference.api_key else "", id="key-env")
                    yield Button("Import native account", id="auth-import")
                with VerticalScroll(id="extras"):
                    yield Label("Instructions file (optional)")
                    yield Input(spec.instructions or "" if spec else "", id="instructions")
                    yield Label("MCP servers")
                    yield Static("", id="mcp-list", markup=False)
                    with Horizontal():
                        yield Button("Add MCP", id="add-mcp")
                        yield Button("Remove MCP", id="remove-mcp")
                with Vertical(id="review"):
                    yield TextArea(read_only=True, id="yaml-review")
                    yield Label("Manifest destination")
                    yield Input(id="destination")
            yield Static("", id="wizard-error", classes="error", markup=False)
            with Horizontal():
                yield Button("Cancel", id="wizard-cancel")
                yield Button("Back", id="back")
                yield Button("Next", id="next", variant="primary")
                yield Button("Save Only", id="save")
                yield Button("Save and Start", id="deploy", variant="primary")

    def on_mount(self):
        self.update_step()
        self.update_mcp()
        self.update_provider()
        self.source_changed()

    @on(Input.Changed, "#source")
    @on(Input.Changed, "#name")
    def source_changed(self):
        if not self.is_mounted:
            return
        self.source_info = None
        self.inspection_key = (self.value("source"), self.value("name"))
        self.query_one("#shared-folder", Checkbox).value = False
        self.query_one("#shared-folder").display = False
        self.query_one("#source-mode", Select).clear()
        self.query_one("#source-mode").display = False
        if self.inspection_timer:
            self.inspection_timer.stop()
        self.query_one("#source-info", Static).update("Checking directory..." if self.value("source") else "")
        if self.value("source"):
            self.inspection_timer = self.set_timer(0.25, self.inspect_source)

    @on(Button.Pressed, "#source-browse")
    def browse_source(self):
        self.app.push_screen(DirectoryPicker(self.value("source")), self.directory_chosen)

    def directory_chosen(self, path):
        if path is not None:
            self.query_one("#source", Input).value = path

    @work(exit_on_error=False)
    async def inspect_source(self):
        key = self.inspection_key
        try:
            info = await background(self.app.client.call, {"op": "inspect-source", "path": key[0], "agent": key[1]})
            if key != self.inspection_key or not self.is_mounted:
                return
            from ..directories import describe, validate_worktree
            if info["kind"] == "repository":
                await background(validate_worktree, info)
            if key != self.inspection_key or not self.is_mounted:
                return
            self.source_info = info
            peers = "\n".join(f"{p['agent']}: {p['state']}, {p['mode']}" +
                               (" (shared files)" if p["shared"] else " (separate working files)") for p in info["agents"])
            self.query_one("#source-info", Static).update(describe(info, key[1], state_home()) +
                                                       ("\nOther agents:\n" + peers if peers else ""))
            self.query_one("#source-mode").display = info["kind"] != "repository"
            self.source_mode_changed()
        except (ValueError, RuntimeError, OSError) as exc:
            if key == self.inspection_key and self.is_mounted:
                self.query_one("#source-info", Static).update(f"Directory {key[0]}: {exc}")

    @on(Select.Changed, "#source-mode")
    def source_mode_changed(self):
        self.query_one("#wizard-error", Static).update("")
        self.query_one("#shared-folder", Checkbox).value = False
        self.query_one("#shared-folder").display = bool(self.source_info and
            self.query_one("#source-mode", Select).value == "direct" and
            any(p["shared"] for p in self.source_info["agents"]))

    @on(Checkbox.Changed, "#shared-folder")
    def sharing_changed(self):
        self.query_one("#wizard-error", Static).update("")

    def source_mode(self):
        if not self.value("source"):
            return "copy"
        if not self.source_info:
            raise ValueError("Wait for a valid directory inspection before continuing")
        if self.source_info["kind"] == "repository":
            return "worktree"
        mode = self.query_one("#source-mode", Select).value
        if mode is Select.NULL:
            raise ValueError("Choose Original folder or Private copy")
        if (mode == "direct" and any(p["shared"] for p in self.source_info["agents"])
                and not self.query_one("#shared-folder", Checkbox).value):
            raise ValueError("Confirm shared edits with the listed agents or choose Private copy")
        return mode

    def value(self, ident):
        return self.query_one("#" + ident, Input).value.strip()

    def make_spec(self):
        return build_spec(name=self.value("name"), workspace=self.value("workspace"),
            source=self.value("source"), source_mode=self.source_mode(), harness=self.query_one("#harness", Select).value,
            provider=self.query_one("#provider", Select).value, model=self.value("model"),
            base_url=self.value("endpoint"), api_key_env=self.value("key-env"),
            instructions=self.value("instructions"), mcp=self.mcp)

    def update_step(self):
        pages = ("identity", "inference", "extras", "review")
        self.query_one("#wizard-pages", ContentSwitcher).current = pages[self.step]
        self.query_one("#wizard-title", Static).update(f"New agent / {self.step + 1}. {pages[self.step].title()}")
        self.query_one("#back", Button).disabled = self.step == 0
        self.query_one("#next").display = self.step < 3
        self.query_one("#save").display = self.step == 3
        self.query_one("#deploy").display = self.step == 3

    def update_mcp(self):
        self.query_one("#mcp-list", Static).update("\n".join(
            f"{name} ({server['transport']})" for name, server in self.mcp.items()) or "None")

    @on(Select.Changed, "#installed")
    def installed(self, event):
        if event.value and event.value is not Select.NULL:
            self.query_one("#model", Input).value = str(event.value)
        self.update_model_fields()

    def model_options(self):
        return [(m, m) for m in self.models] + [
            ("Custom model..." if self.models else "Custom model... (no models listed)", "")]

    def update_models(self, models):
        names = sorted({model for model in models if isinstance(model, str) and model})
        if names == self.models:
            return
        self.models = names
        selector = self.query_one("#installed", Select)
        selected = selector.value
        with selector.prevent(Select.Changed):
            selector.set_options(self.model_options())
            selector.value = selected if selected in names else ""
        self.update_model_fields()

    def update_model_fields(self):
        ollama = self.query_one("#provider", Select).value == "ollama"
        for ident in ("#installed", "#installed-label"):
            self.query_one(ident).display = ollama
        custom = not ollama or not self.query_one("#installed", Select).value
        for ident in ("#model", "#model-label"):
            self.query_one(ident).display = custom

    @on(Select.Changed, "#harness")
    def harness_changed(self):
        harness = self.query_one("#harness", Select).value
        provider = self.query_one("#provider", Select)
        if harness != "opencode":
            provider.value = "native"
        elif provider.value == "native":
            provider.value = "ollama"
        self.update_provider()

    @on(Select.Changed, "#provider")
    def update_provider(self):
        harness = self.query_one("#harness", Select).value
        provider = self.query_one("#provider", Select).value
        self.query_one("#auth-import").display = harness != "opencode"
        for selector in ("#endpoint", "#endpoint-label"):
            self.query_one(selector).display = provider == "openai-compatible"
        if provider != "openai-compatible":
            self.query_one("#endpoint", Input).value = ""
        # Returning from another provider must not silently overwrite its custom model.
        selector = self.query_one("#installed", Select)
        model = self.value("model")
        with selector.prevent(Select.Changed):
            selector.value = model if model in self.models else ""
        self.update_model_fields()

    @on(Button.Pressed, "#auth-import")
    def auth(self):
        harness = self.query_one("#harness", Select).value
        self.app.push_screen(Prompt(f"Import your existing {harness} account into private sh41 storage?",
                                    confirm=True), lambda yes: self.import_auth(harness) if yes else None)

    @work(exit_on_error=False)
    async def import_auth(self, harness):
        try:
            await background(import_native, state_home(), harness)
            self.app.notify("Native account imported")
        except (ValueError, RuntimeError, OSError):
            self.app.notify("Import failed; check native account login", severity="error")

    @on(Button.Pressed)
    def clicked(self, event):
        ident = event.button.id
        if ident == "wizard-cancel":
            self.action_cancel()
        elif ident == "back":
            self.step -= 1
            self.update_step()
        elif ident == "next":
            try:
                if self.step == 0:
                    build_spec(name=self.value("name"), workspace=self.value("workspace"),
                               source=self.value("source"), source_mode=self.source_mode(), harness="codex")
                if self.step >= 1:
                    self.spec = self.make_spec()
                if self.step == 2:
                    self.query_one("#yaml-review", TextArea).load_text(self.spec.as_yaml())
                    destination = self.query_one("#destination", Input)
                    if not destination.value:
                        destination.value = str(Path.cwd() / f"{self.spec.agent}.yaml")
                self.query_one("#wizard-error", Static).update("")
                self.step += 1
                self.update_step()
            except (ValueError, OSError) as exc:
                self.query_one("#wizard-error", Static).update(str(exc))
        elif ident == "add-mcp":
            self.app.push_screen(MCPForm(), self.add_mcp)
        elif ident == "remove-mcp":
            self.app.push_screen(Prompt("MCP server name to remove"), self.remove_mcp)
        elif ident in {"save", "deploy"}:
            try:
                destination = Path(self.value("destination")).expanduser()
                write_manifest(self.spec, destination)
                ack = [p["id"] for p in self.source_info["agents"] if p["shared"]] if self.source_info else []
                self.dismiss({"spec": self.spec, "deploy": ident == "deploy", "path": str(destination), "shared_ack": ack})
            except FileExistsError:
                self.query_one("#wizard-error", Static).update("Manifest exists; choose another destination")
            except (ValueError, OSError) as exc:
                self.query_one("#wizard-error", Static).update(str(exc))

    def add_mcp(self, result):
        if result:
            name, server = result
            if name in self.mcp:
                self.query_one("#wizard-error", Static).update("MCP name already exists")
            else:
                self.mcp[name] = server
                self.update_mcp()

    def remove_mcp(self, name):
        if name:
            if name not in self.mcp:
                self.query_one("#wizard-error", Static).update("Unknown MCP name")
            else:
                del self.mcp[name]
                self.update_mcp()


def import_spec(path):
    path = Path(path).expanduser().resolve()
    return parse_yaml(path.read_text()).resolved(path.parent)
