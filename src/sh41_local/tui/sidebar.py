from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option


def text(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def name_line(value):
    line = Text(text(value))
    line.truncate(19, overflow="ellipsis")
    line.append("\n")
    return line


class Sidebar(Vertical):
    DEFAULT_CSS = """
    Sidebar { width: 24; height: 1fr; padding: 0 1; background: #080806;
        border-right: solid #30291d; }
    Sidebar .section-title { height: 1; text-style: bold; color: $text-muted; }
    Sidebar #sidebar-runtime { height: 3; }
    Sidebar #sidebar-ollama-start { width: 1fr; height: 3; min-width: 1; margin: 0; }
    Sidebar .section-link { height: 1; min-width: 1; width: 1fr; padding: 0;
        margin: 1 0 0 0; border: none; background: transparent; text-align: left;
        content-align: left middle; text-style: bold; color: $foreground; }
    Sidebar .section-link:hover, Sidebar .section-link:focus { color: $accent; border: none; }
    Sidebar OptionList { height: 1fr; min-height: 3; border: none; padding: 0;
        background: transparent; text-wrap: nowrap; text-overflow: ellipsis; }
    Sidebar .empty { height: 1fr; min-height: 3; color: $text-muted; }
    """

    def compose(self):
        yield Static("Runtime", classes="section-title")
        yield Static("Supervisor: checking\nDocker: checking\nOllama: checking", id="sidebar-runtime")
        yield Button("Start Ollama", id="sidebar-ollama-start")
        yield Button("Agents", id="sidebar-agents-nav", classes="section-link")
        yield OptionList(id="sidebar-agents")
        yield Static("Checking agents...", id="sidebar-agents-empty", classes="empty")
        yield Button("Loaded models", id="sidebar-models-nav", classes="section-link")
        yield OptionList(id="sidebar-models")
        yield Static("Checking models...", id="sidebar-models-empty", classes="empty")

    def options(self, ident, rows, empty):
        widget = self.query_one("#" + ident, OptionList)
        keys = [key for key, _ in rows]
        old = [widget.get_option_at_index(i).id for i in range(widget.option_count)]
        selected = old[widget.highlighted] if widget.highlighted is not None and old else None
        scroll = widget.scroll_offset
        if old != keys:
            widget.clear_options().add_options(Option(prompt, id=key) for key, prompt in rows)
            if selected in keys:
                widget.highlighted = keys.index(selected)
                widget.scroll_to(scroll.x, scroll.y, animate=False)
        else:
            for key, prompt in rows:
                if widget.get_option(key).prompt != prompt:
                    widget.replace_option_prompt(key, prompt)
        widget.display = bool(rows)
        label = self.query_one("#" + ident + "-empty", Static)
        label.update(empty)
        label.display = not rows

    def render_state(self, snapshot, models, failed, connected):
        docker = snapshot.get("docker", {})
        runtime = Text()
        for name, state, stale in (
            ("Supervisor", "ready" if connected else "checking", bool(failed)),
            ("Docker", docker.get("state", "checking"), "agents" in failed or docker.get("stale")),
            ("Ollama", models.get("state", "checking"), "models" in failed or models.get("stale")),
        ):
            label = "stale" if stale else {"ready": "up", "unreachable": "down"}.get(state, state)
            runtime.append(name + ": ")
            runtime.append(text(label), style="#82d66b" if label == "up" else "#ef5d4f" if label == "down" else "#f0a13d")
            runtime.append("\n" if name != "Ollama" else "")
        self.query_one("#sidebar-runtime", Static).update(runtime)
        self.query_one("#sidebar-runtime").tooltip = text(models.get("url", "Ollama endpoint not yet known"))
        busy = any(r["kind"] == "models-start" and r["status"] in {"pending", "running"}
                   for r in snapshot.get("operations", []))
        up = models.get("state") == "ready" and not models.get("stale") and "models" not in failed
        button = self.query_one("#sidebar-ollama-start", Button)
        button.disabled = up or busy
        button.label = "Starting..." if busy else "Ollama running" if up else "Start Ollama"

        rows, running = [], 0
        for agent in snapshot.get("agents", []):
            stale = "agents" in failed or agent.get("stale")
            ready = agent.get("state") == "deployed" and agent.get("harness_state") == "ready" and not stale
            running += int(ready)
            state = "stale" if stale else agent.get("activity", "idle") if ready else agent.get("state", "unknown")
            prompt = name_line(agent["slug"])
            prompt.append(text("up / " + state if ready else state), style="#82d66b" if ready else "#aaa195")
            rows.append((agent["slug"], prompt))
        unknown = "agents" in failed or any(r.get("stale") for r in snapshot.get("agents", []))
        self.query_one("#sidebar-agents-nav", Button).label = f"Agents ({'?' if unknown else running} up)"
        self.options("sidebar-agents", rows, "Status unavailable" if "agents" in failed else
                     "No agents" if connected else "Checking agents...")
        loaded = models.get("loaded")
        stale = "models" in failed or models.get("stale") or models.get("state") != "ready"
        self.query_one("#sidebar-models-nav", Button).label = "Loaded models" + (f" ({len(loaded)})" if loaded is not None and not stale else " (?)")
        rows = []
        for model in loaded or []:
            name = model.get("name", model.get("model"))
            if not name or name in [key for key, _ in rows]:
                continue
            prompt = name_line(name)
            memory = model.get("size")
            prompt.append("stale" if stale else f"up / {memory / 1024 ** 3:.1f} GiB" if isinstance(memory, (int, float)) else "up",
                          style="#aaa195" if stale else "#82d66b")
            rows.append((name, prompt))
        self.options("sidebar-models", rows, "Status unavailable" if stale or loaded is None else "None loaded")
