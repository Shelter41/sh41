from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static


class Dialog(ModalScreen):
    DEFAULT_CSS = """
    Dialog { align: center middle; background: $background 70%; }
    Dialog > Vertical { width: 70; max-width: 95%; height: auto; max-height: 90%;
        border: solid $primary; background: $surface; padding: 1 2; }
    Dialog .dialog-title { text-style: bold; margin-bottom: 1; }
    Dialog Horizontal { height: auto; align-horizontal: right; margin-top: 1; }
    Dialog Button { margin-left: 1; }
    Dialog VerticalScroll { height: auto; max-height: 20; }
    Dialog DataTable { height: 12; }
    Dialog .error { color: $error; height: auto; }
    """
    BINDINGS = [("escape", "cancel", "Close")]

    def action_cancel(self):
        self.dismiss(None)


class Prompt(Dialog):
    DEFAULT_CSS = """
    Prompt .dialog-title { max-height: 12; overflow-y: auto; }
    """

    def __init__(self, title, *, initial="", confirm=False):
        super().__init__()
        self.heading, self.initial, self.confirm = title, initial, confirm

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.heading, classes="dialog-title", markup=False)
            if not self.confirm:
                yield Input(self.initial, id="answer")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Confirm" if self.confirm else "Continue", id="accept", variant="primary")

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.action_cancel()

    @on(Button.Pressed, "#accept")
    @on(Input.Submitted)
    def accept(self):
        self.dismiss(True if self.confirm else self.query_one(Input).value.strip())


class Records(Dialog):
    DEFAULT_CSS = """
    Records > Vertical { height: 90%; max-height: 30; }
    Records DataTable { height: 1fr; }
    Records #record-detail { height: 1; }
    """
    def __init__(self, agent, rows, *, sessions=False):
        super().__init__()
        self.agent, self.rows, self.sessions = agent, rows, sessions

    def compose(self):
        with Vertical():
            yield Static(f"{self.agent} / {'Conversations' if self.sessions else 'Run history'}",
                         classes="dialog-title", markup=False)
            yield DataTable(id="records", cursor_type="row")
            yield Static("", id="record-detail", markup=False)
            with Horizontal():
                yield Button("Close", id="close")
                if self.sessions:
                    yield Button("New", id="new")
                    yield Button("Continue", id="continue", variant="primary", disabled=not self.rows)

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_columns("ID", "State", "Created")
        for row in self.rows:
            table.add_row(row["id"][:8], ("active" if row.get("active") else "saved")
                          if self.sessions else row["status"], row["created_at"], key=row["id"])
        table.focus()

    @on(DataTable.RowHighlighted)
    def highlight(self, event):
        self.query_one("#record-detail", Static).update(str(event.row_key.value))

    @on(Button.Pressed)
    def choose(self, event):
        if event.button.id == "new":
            self.dismiss({"kind": "new-session"})
        elif event.button.id == "continue" and self.rows:
            table = self.query_one(DataTable)
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            self.dismiss({"kind": "continue-session", "session_id": key})
        else:
            self.dismiss(None)
