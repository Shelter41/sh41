from __future__ import annotations

import uuid

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.theme import Theme
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static, TabbedContent, TabPane

from .client import Client, background
from .dialogs import Prompt, Records
from .wizard import Wizard, import_spec


def plain(value):
    text = str(value if value is not None else "-")
    return Text("".join(c if c.isprintable() else " " for c in text))


def size(value):
    if value is None:
        return "-"
    return f"{value / 1024 ** 3:.1f} GiB"


class Shell(App):
    TITLE = "Shelter41"
    SUB_TITLE = ""
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $background; }
    Header { height: 3; padding: 0 1; background: #080806; color: $foreground; }
    HeaderIcon, HeaderClockSpace { display: none; }
    HeaderTitle { height: 100%; content-align: left middle; text-style: bold; }
    Button { text-style: none; background: $surface; color: $foreground;
        border: tall #30291d; }
    Button:hover { background: #17140d; border: tall #57462c; }
    Button:focus { border: tall #c67d2d; text-style: none; }
    Button.-primary { background: #ee982e; color: #170d03; border: tall #d98224;
        text-style: bold; }
    Button.-primary:hover, Button.-primary:focus { background: #f5a43d;
        color: #170d03; border: tall #ffb14f; }
    Input, SelectCurrent { background: $surface; border: tall #30291d; }
    Input:focus, Select:focus > SelectCurrent { border: tall #c67d2d; }
    DataTable { background: $surface; }
    DataTable > .datatable--header { background: #100f0c; color: #aaa195;
        text-style: none; }
    DataTable > .datatable--cursor { background: #332819; color: #eee9df;
        text-style: none; }
    DataTable:focus > .datatable--cursor { background: #332819; color: #f0a84b;
        text-style: none; }
    Tab { color: #9b9386; }
    Tab.-active { color: #f0a84b; text-style: bold; }
    #health { height: 1; padding: 0 1; color: $text-muted; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    .toolbar { height: 3; margin-bottom: 1; }
    .toolbar Button { min-width: 10; margin-right: 1; }
    #filter { width: 1fr; }
    #agents-table { height: 1fr; min-height: 3; }
    #agent-detail { height: 3; padding: 0 1; background: $surface;
        text-wrap: nowrap; text-overflow: ellipsis; }
    #agent-action { width: 22; }
    #agent-controls { height: 3; margin-top: 1; }
    #agent-controls Button { min-width: 10; margin-left: 1; }
    #model-server { height: 3; }
    #models-table { height: 1fr; }
    #operations-table { height: 1fr; }
    #notice { height: 1; padding: 0 1; color: $warning; }
    Footer { background: $surface; }
    """
    BINDINGS = [("ctrl+n", "new", "New agent"), ("ctrl+o", "import_yaml", "Import YAML"),
                ("ctrl+r", "refresh", "Refresh"), ("ctrl+q", "quit", "Quit")]

    def __init__(self, client=None):
        super().__init__()
        self.client = client or Client()
        self.snapshot = {"agents": [], "operations": [], "docker": {}}
        self.models = {}
        self.polling = set()
        self.submitted = set()
        self.attaching = False
        # Copy the cloud dark-theme tokens without depending on its frontend package.
        self.register_theme(Theme(name="shelter41", primary="#ee982e", secondary="#aaa195",
            accent="#f0a13d", foreground="#eee9df", background="#050504",
            surface="#0d0c09", panel="#11100c", success="#82d66b", warning="#f0a13d",
            error="#ef5d4f", dark=True, variables={
                "text-muted": "#80796d", "text-disabled": "#665f54",
                "border": "#c67d2d", "border-blurred": "#30291d",
                "footer-background": "#080806", "footer-key-foreground": "#f0a13d",
                "footer-description-foreground": "#aaa195",
                "button-focus-text-style": "none",
                "block-cursor-background": "#332819", "block-cursor-foreground": "#f0a84b",
                "block-cursor-text-style": "none", "input-selection-background": "#44301a",
                "input-cursor-background": "#f0a13d", "input-cursor-foreground": "#050504",
                "scrollbar": "#3a3021", "scrollbar-hover": "#57462c",
                "scrollbar-active": "#c67d2d", "scrollbar-background": "#080806",
            }))
        self.theme = "shelter41"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Connecting to local supervisor...", id="health", markup=False)
        with TabbedContent(initial="agents-view", id="views"):
            with TabPane("Agents", id="agents-view"):
                with Horizontal(classes="toolbar"):
                    yield Input(placeholder="Filter agents", id="filter")
                    yield Button("New", id="new-agent", variant="primary")
                    yield Button("Import YAML", id="import-yaml")
                yield DataTable(id="agents-table", cursor_type="row")
                yield Static("No agents", id="agent-detail", markup=False)
                with Horizontal(id="agent-controls"):
                    yield Button("Attach", id="attach", variant="primary", disabled=True)
                    yield Select([(label, value) for label, value in (
                        ("Start terminal", "start"), ("Read-only attach", "readonly"),
                        ("Pause", "pause"), ("Resume", "resume"), ("Park", "park"),
                        ("Redeploy", "redeploy"), ("Interrupt", "interrupt"),
                        ("Conversations", "sessions"), ("Run history", "history"),
                        ("Export workspace", "export"))], value="start", allow_blank=False, id="agent-action")
                    yield Button("Apply", id="apply", disabled=True)
            with TabPane("Models", id="models-view"):
                yield Static("Ollama: checking", id="model-server", markup=False)
                with Horizontal(classes="toolbar"):
                    yield Button("Start / Reuse", id="models-start")
                    yield Button("Pull model", id="models-pull", variant="primary")
                    yield Button("Stop server", id="models-stop", disabled=True)
                yield DataTable(id="models-table", cursor_type="row")
            with TabPane("Operations", id="operations-view"):
                yield DataTable(id="operations-table", cursor_type="row")
        yield Static("", id="notice", markup=False)
        yield Footer()

    def q(self, selector, expect_type=None):
        return self.home.query_one(selector, expect_type)

    def on_mount(self):
        self.home = self.screen
        self.q("#agents-table", DataTable).add_columns("Agent", "State", "Activity", "Harness", "Model", "Workspace")
        self.q("#models-table", DataTable).add_columns("Model", "Downloaded", "Loaded", "Disk", "Memory", "Agents", "Expires")
        self.q("#operations-table", DataTable).add_columns("ID", "Action", "Agent", "Status", "Progress")
        self.set_interval(2, self.refresh_agents)
        self.set_interval(5, self.refresh_models)
        self.action_refresh()
        self.q("#agents-table", DataTable).focus()

    def action_refresh(self):
        self.refresh_agents()
        self.refresh_models()

    def refresh_agents(self):
        self.poll("agents", "snapshot")

    def refresh_models(self):
        self.poll("models", "models-snapshot")

    def poll(self, key, op):
        if key not in self.polling and not self.attaching:
            self.polling.add(key)
            self.fetch(key, op)

    @work(exit_on_error=False)
    async def fetch(self, key, op):
        try:
            result = await background(self.client.call, {"op": op})
            if key == "agents":
                self.snapshot = result
                self.render_agents()
                self.render_operations()
            else:
                self.models = result
                self.render_models()
                names = [row.get("name", row.get("model")) for row in result.get("models") or []]
                for screen in self.screen_stack:
                    if isinstance(screen, Wizard):
                        screen.update_models(names)
            self.render_health()
        except (ValueError, RuntimeError, OSError):
            if key == "agents":
                self.q("#health", Static).update("Supervisor disconnected | Displayed data is stale")
            else:
                self.q("#model-server", Static).update("Ollama status unavailable | Displayed data is stale")
        finally:
            self.polling.discard(key)

    def render_health(self):
        docker = self.snapshot.get("docker", {})
        self.q("#health", Static).update(
            f"Docker: {docker.get('state', 'checking')} | Ollama: {self.models.get('state', 'checking')} | "
            f"{len(self.snapshot['agents'])} agents")

    def update_table(self, selector, rows, widths):
        table = self.q(selector, DataTable)
        keys = [str(key) for key, _ in rows]
        existing = [str(key.value) for key in table.rows]
        selected = None
        if table.row_count:
            selected = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        scroll = table.scroll_offset
        if keys != existing:
            table.clear()
            for key, cells in rows:
                table.add_row(*(plain(value) for value in cells), key=str(key))
            if selected in keys:
                table.move_cursor(row=keys.index(selected), animate=False, scroll=False)
            table.scroll_to(scroll.x, scroll.y, animate=False, force=True)
        else:
            for key, cells in rows:
                for column, value in zip(table.columns, cells):
                    rendered = plain(value)
                    if table.get_cell(str(key), column) != rendered:
                        table.update_cell(str(key), column, rendered)
        for column, width in zip(table.columns.values(), widths):
            column.width = width
            column.auto_width = False

    def render_agents(self):
        needle = self.q("#filter", Input).value.casefold()
        rows = [row for row in self.snapshot["agents"] if needle in " ".join(
            str(row.get(key, "")) for key in ("slug", "harness", "model", "workspace", "state", "activity")).casefold()]
        self.update_table("#agents-table", [(row["slug"], [row["slug"],
            row.get("state", "unknown") + ("*" if row.get("stale") else ""),
            row.get("activity", "unknown"), row["harness"], row["model"], row["workspace"]]) for row in rows],
            [20, 13, 14, 12, 24, 16])
        self.q("#attach", Button).disabled = not rows or self.attaching
        self.q("#apply", Button).disabled = not rows
        self.render_detail()

    def selected(self):
        table = self.q("#agents-table", DataTable)
        if not table.row_count:
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def render_detail(self):
        slug = self.selected()
        row = next((r for r in self.snapshot["agents"] if r["slug"] == slug), None)
        if row is None:
            self.q("#agent-detail", Static).update("No matching agents" if self.snapshot["agents"] else "No agents")
            return
        last = row.get("last_run") or {}
        self.q("#agent-detail", Static).update(
            f"{slug} | {row['model']} | {row['workspace']}\n"
            f"Terminal: {row.get('harness_state', 'unknown')} | Last recorded run: {last.get('status', '-')}\n"
            f"Observed: {row.get('observed_at') or 'pending'} | Recorded lifecycle: {row.get('recorded_state', '-')}"
            + (" | stale" if row.get("stale") else ""))

    def render_operations(self):
        self.update_table("#operations-table", [(r["id"], [r["id"][:8], r["kind"], r["resource"],
            r["status"], r["progress"]]) for r in self.snapshot["operations"]], [8, 18, 20, 12, 70])
        for row in self.snapshot["operations"]:
            if row["id"] in self.submitted and row["status"] not in {"pending", "running"}:
                self.submitted.discard(row["id"])
                self.q("#notice", Static).update(f"{row['kind']}: {row['status']} ({row['id'][:8]})")
                self.notify(row["progress"], title=f"{row['resource']}: {row['kind']}",
                            severity="information" if row["status"] == "completed" else "error")

    def render_models(self):
        state = self.models
        unavailable = [label + " unavailable" for key, label in (("models", "Downloaded inventory"),
                       ("loaded", "Loaded inventory")) if state.get(key) is None]
        self.q("#model-server", Static).update(
            f"Ollama: {state.get('state', 'checking')} | {'sh41-managed' if state.get('owned') else 'external / unclaimed'}\n"
            f"{state.get('url', '-')} | Version: {state.get('version') or '-'}\n"
            + (", ".join(unavailable or state.get("errors", [])) or f"Observed: {state.get('observed_at') or 'pending'}")
            + (" | stale" if state.get("stale") else ""))
        downloaded = {r.get("name", r.get("model")): r for r in state.get("models") or []}
        loaded = {r.get("name", r.get("model")): r for r in state.get("loaded") or []}
        rows = []
        for name in sorted(set(downloaded) | set(loaded)):
            users = [r["slug"] for r in self.snapshot["agents"] if r.get("provider") == "ollama"
                     and (r["model"] == name or r["model"] + ":latest" == name)]
            rows.append((name, [name, "yes" if name in downloaded else "unknown" if state.get("models") is None else "no",
                "yes" if name in loaded else "unknown" if state.get("loaded") is None else "no",
                size(downloaded.get(name, {}).get("size")), size(loaded.get(name, {}).get("size")),
                ", ".join(users) or "-", loaded.get(name, {}).get("expires_at", "-")]))
        self.update_table("#models-table", rows, [28, 12, 9, 10, 10, 24, 25])
        self.q("#models-stop", Button).disabled = not state.get("owned")

    @on(Input.Changed, "#filter")
    def filter_changed(self):
        if hasattr(self, "home"):
            self.render_agents()

    @on(DataTable.RowHighlighted, "#agents-table")
    def highlight_agent(self):
        self.render_detail()

    @on(DataTable.RowSelected, "#agents-table")
    def open_agent(self):
        self.attach_agent()

    def action_new(self):
        if self.screen is self.home:
            names = [row.get("name", row.get("model")) for row in self.models.get("models") or []]
            self.push_screen(Wizard(models=names), self.wizard_done)

    def wizard_done(self, result):
        if result:
            self.q("#notice", Static).update("Saved " + result["path"])
            if result["deploy"]:
                self.submit_job("deploy-start", spec=result["spec"])

    def action_import_yaml(self):
        if self.screen is self.home:
            self.push_screen(Prompt("Agent YAML path"), self.import_yaml)

    @work(exit_on_error=False)
    async def import_yaml(self, path):
        if not path:
            return
        try:
            spec = await background(import_spec, path)
            self.push_screen(Prompt(f"Deploy and start {spec.agent} from this YAML?", confirm=True),
                             lambda yes: self.submit_job("deploy-start", spec=spec) if yes else None)
        except (ValueError, OSError) as exc:
            self.notify(str(exc), severity="error")

    @work(exit_on_error=False)
    async def submit_job(self, kind, **fields):
        ident = str(uuid.uuid4())
        try:
            result = await background(self.client.submit, kind, ident=ident, **fields)
            self.submitted.add(ident)
            self.q("#notice", Static).update(f"{kind}: {result['status']} ({ident[:8]})")
            self.refresh_agents()
        except (TimeoutError, ConnectionError):
            self.q("#notice", Static).update(f"Response lost for {ident[:8]}; inspect Operations before retrying")
        except (ValueError, RuntimeError, OSError) as exc:
            self.notify(str(exc), severity="error", timeout=8)

    @work(exit_on_error=False)
    async def attach_agent(self, readonly=False):
        slug = self.selected()
        if not slug or self.attaching:
            return
        self.attaching = True
        self.q("#attach", Button).disabled = True
        self.q("#notice", Static).update(f"Opening {slug}...")
        try:
            attachment = await background(self.client.attachment, slug, readonly)
            with self.suspend():
                code = await background(self.client.attach, attachment)
            self.q("#notice", Static).update(f"Detached from {slug}" if code == 0 else f"Terminal exited ({code})")
        except (ValueError, RuntimeError, OSError) as exc:
            self.notify(str(exc), severity="error")
        finally:
            self.attaching = False
            self.refresh_agents()

    @work(exit_on_error=False)
    async def records(self, slug, kind):
        try:
            rows = await background(self.client.call, {"op": kind, "agent": slug})
            self.push_screen(Records(slug, rows, sessions=kind == "sessions"),
                lambda result: self.submit_job(agent=slug, **result) if result else None)
        except (ValueError, RuntimeError, OSError) as exc:
            self.notify(str(exc), severity="error")

    def apply_action(self):
        slug = self.selected()
        if not slug:
            return
        kind = str(self.q("#agent-action", Select).value)
        if kind == "readonly":
            self.attach_agent(True)
        elif kind in {"sessions", "history"}:
            self.records(slug, kind)
        elif kind == "export":
            from pathlib import Path
            self.push_screen(Prompt("New export directory"), lambda path:
                self.submit_job(kind, agent=slug, output=str(Path(path).expanduser().resolve())) if path else None)
        elif kind in {"pause", "park", "interrupt", "redeploy"}:
            self.push_screen(Prompt(f"{kind.title()} {slug}?", confirm=True),
                             lambda yes: self.submit_job(kind, agent=slug) if yes else None)
        else:
            self.submit_job(kind, agent=slug)

    @on(Button.Pressed)
    def clicked(self, event):
        ident = event.button.id
        if ident == "new-agent":
            self.action_new()
        elif ident == "import-yaml":
            self.action_import_yaml()
        elif ident == "attach":
            self.attach_agent()
        elif ident == "apply":
            self.apply_action()
        elif ident == "models-start":
            self.submit_job("models-start")
        elif ident == "models-pull":
            self.push_screen(Prompt("Local model tag"), lambda model:
                self.submit_job("models-pull", model=model) if model else None)
        elif ident == "models-stop":
            self.push_screen(Prompt("Stop the sh41-managed Ollama server?", confirm=True),
                             lambda yes: self.submit_job("models-stop") if yes else None)
