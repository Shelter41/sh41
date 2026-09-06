from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option


class SearchInput(Input):
    BINDINGS = [Binding("down", "suggest_next", show=False),
                Binding("up", "suggest_previous", show=False),
                Binding("enter", "choose", show=False),
                Binding("tab", "leave_next", show=False),
                Binding("shift+tab", "leave_previous", show=False),
                Binding("escape", "close_suggestions", show=False, priority=True)]

    def on_focus(self):
        self.parent.open()

    def on_click(self):
        self.parent.open()

    def action_suggest_next(self):
        self.parent.move(1)

    def action_suggest_previous(self):
        self.parent.move(-1)

    def action_choose(self):
        self.parent.accept()

    def check_action(self, action, parameters):
        if action == "close_suggestions":
            return self.parent.expanded
        return True

    def action_close_suggestions(self):
        self.parent.close()

    def action_leave_next(self):
        self.parent.close()
        self.screen.focus_next()

    def action_leave_previous(self):
        self.parent.close()
        self.screen.focus_previous()


class Suggestions(OptionList):
    def focus_on_click(self):
        return False


class ModelPicker(Vertical):
    DEFAULT_CSS = """
    ModelPicker { height: 3; }
    ModelPicker > Input { width: 1fr; }
    ModelPicker > Suggestions { display: none; width: 1fr; height: auto; max-height: 8;
        overlay: screen; constrain: none inside; border: tall $border;
        background: $surface; text-wrap: nowrap; text-overflow: ellipsis; }
    ModelPicker.-expanded > Suggestions { display: block; }
    """

    @dataclass
    class Changed(Message):
        picker: "ModelPicker"
        value: str

        @property
        def control(self):
            return self.picker

    @dataclass
    class Edited(Message):
        picker: "ModelPicker"

        @property
        def control(self):
            return self.picker

    def __init__(self, options, *, value="", **kwargs):
        super().__init__(**kwargs)
        self.options = list(options)
        self._value = value
        self.query_text = ""
        self.expanded = False

    def compose(self):
        yield SearchInput(placeholder="Search downloaded models or Ollama library", id="library-search")
        yield Suggestions(id="model-suggestions")

    def on_mount(self):
        self.value = self._value

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
        if self.is_mounted:
            field = self.query_one(Input)
            with field.prevent(Input.Changed):
                field.value = value.removeprefix("@library/")
            self.query_text = ""
            self.close()
            self.post_message(self.Changed(self, value))

    def focus(self, scroll_visible=True):
        self.query_one(Input).focus(scroll_visible=scroll_visible)
        return self

    def set_options(self, options):
        self.options = list(options)
        if self.is_mounted:
            self.render_options()

    def render_options(self):
        menu = self.query_one(Suggestions)
        old = menu.get_option_at_index(menu.highlighted).id if menu.highlighted is not None else None
        needle = self.query_text.casefold()
        rows = [(label, key) for label, key in self.options if not key or needle in label.casefold()]
        menu.clear_options().add_options(Option(Text(label), id=key) for label, key in rows)
        keys = [key for _, key in rows]
        menu.highlighted = keys.index(old) if old and old in keys else 0 if keys else None

    def open(self):
        self.expanded = True
        self.render_options()
        self.add_class("-expanded")

    def close(self):
        self.expanded = False
        self.remove_class("-expanded")

    def on_descendant_blur(self):
        self.call_after_refresh(self.close_if_unfocused)

    def close_if_unfocused(self):
        if not self.has_focus_within:
            self.close()

    def move(self, delta):
        if not self.expanded:
            self.open()
            return
        menu = self.query_one(Suggestions)
        if menu.option_count:
            menu.highlighted = ((menu.highlighted or 0) + delta) % menu.option_count

    def accept(self):
        if not self.expanded:
            self.open()
            return
        menu = self.query_one(Suggestions)
        if menu.highlighted is not None:
            self.value = menu.get_option_at_index(menu.highlighted).id

    @on(OptionList.OptionSelected)
    def chosen(self, event):
        event.stop()
        self.query_one(Input).focus()
        self.value = event.option_id

    @on(Input.Changed)
    def edited(self, event):
        event.stop()
        self._value = ""
        self.query_text = event.value.strip()
        self.open()
        self.post_message(self.Edited(self))
