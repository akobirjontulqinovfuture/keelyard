"""Timeline browser: every step on the left, its diff on the right, rewind with one key."""

from __future__ import annotations

import datetime as dt

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .snapshot import WIRING, Shadow, Step, user_first

QUIET = {"baseline", "pre-rewind", "rewind", "stop", "manual"}

CSS = """
Screen { background: #111110; }
#bar { height: 1; padding: 0 2; background: #111110; color: #8F8F89; }
#bar .b { color: #EDEDEA; }
#steps { width: 46; height: 100%; border: none; border-right: solid #2A2A28; background: #111110; padding: 0 0; }
#steps > .option-list--option-highlighted { background: #22221F; color: #EDEDEA; text-style: none; }
#steps:focus > .option-list--option-highlighted { background: #262623; }
#right { padding: 0 0 0 2; }
#head { height: auto; padding: 1 0 1 0; color: #EDEDEA; }
#diff { background: #111110; }
Footer { background: #161615; color: #8F8F89; }
FooterKey { background: #161615; color: #8F8F89; }
FooterKey .footer-key--key { background: #161615; color: #EDEDEA; text-style: bold; }
FooterKey .footer-key--description { color: #8F8F89; }
ConfirmRewind { align: center middle; background: #00000080; }
#dialog { width: 62; height: auto; padding: 1 2; background: #1C1C1A; border: solid #3A3A37; color: #EDEDEA; }
"""


def _row(s: Step) -> Text:
    t = dt.datetime.fromtimestamp(s.ts).strftime("%H:%M:%S")
    first = user_first(s.files)
    mine = [f for f in first if not f.startswith(WIRING)] or first  # agent setup files are not your work
    files = mine[0] if mine else ""
    if len(files) > 18:
        files = "…" + files[-17:]
    if len(mine) > 1:
        files += f" +{len(mine) - 1}"
    quiet = s.tool in QUIET
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(f" #{s.step:<4}", style="#8F8F89")
    line.append(f"{t}  ", style="#6B6B66")
    line.append(f"{s.tool:<10} ", style="#8F8F89" if quiet else "#EDEDEA")
    line.append(files, style="#8F8F89" if quiet else "#C9C9C4")
    return line


def render_diff(diff: str) -> Text:
    """A quiet diff: file names as headings, +/- lines coloured, git plumbing lines dropped."""
    out = Text(no_wrap=True)
    if not diff.strip():
        return Text("(no changes in this step)", style="#8F8F89")
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            name = line.split(" b/", 1)[-1]
            if out:
                out.append("\n")
            out.append(f"{name}\n", style="bold #EDEDEA")
        elif line.startswith(("index ", "--- ", "+++ ", "new file mode", "deleted file mode", "similarity", "rename ")):
            if line.startswith(("new file", "deleted file")):
                out.append(line.split(" mode")[0] + "\n", style="#8F8F89")
            continue
        elif line.startswith("@@"):
            out.append(line.split("@@")[1].strip() + "\n", style="#6B6B66")
        elif line.startswith("+"):
            out.append(line + "\n", style="#5CB88A")
        elif line.startswith("-"):
            out.append(line + "\n", style="#F0776B")
        else:
            out.append(line + "\n", style="#C9C9C4")
    return out


class ConfirmRewind(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes", "rewind"), Binding("n,escape", "no", "cancel")]

    def __init__(self, step: Step):
        super().__init__()
        self.step = step

    def compose(self) -> ComposeResult:
        n = len(self.step.files)
        yield Static(Text.assemble((f"Rewind to step #{self.step.step}?\n\n", "bold"),
                                   ("Files go back to how they were after this step. A snapshot of the current state is "
                                    "taken first, so you can undo this.\n\n", "#C9C9C4"),
                                   ("y", "bold #5CB88A"), (" rewind    ", "#8F8F89"), ("n", "bold"), (" cancel", "#8F8F89"),
                                   (f"\n\n{n} file{'s' * (n != 1)} touched in this step", "#6B6B66")), id="dialog")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class Timeline(App):
    CSS = CSS
    TITLE = "keelyard timeline"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("up,down", "noop", "move"), Binding("r", "rewind", "rewind to step"), Binding("q", "quit", "quit"),
                Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False),
                Binding("g", "first", show=False), Binding("G", "last", show=False)]

    def __init__(self, shadow: Shadow):
        super().__init__()
        self.shadow = shadow
        self.steps = list(reversed(shadow.steps()))
        self.message: str | None = None

    def compose(self) -> ComposeResult:
        root = self.shadow.root
        bar = Text.assemble(("keelyard", "bold #EDEDEA"), ("  timeline  ", "#8F8F89"), (str(root.name), "#C9C9C4"),
                            (f"  ·  {len(self.steps)} steps", "#6B6B66"))
        yield Static(bar, id="bar")
        with Horizontal():
            yield OptionList(*[Option(_row(s), id=str(s.step)) for s in self.steps], id="steps")
            with Vertical(id="right"):
                yield Static(id="head")
                with VerticalScroll(id="diff"):
                    yield Static(id="diffbody")
        yield Footer()

    def on_mount(self) -> None:
        if self.steps:
            self.show(self.steps[0])

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.show(self.steps[event.option_index])

    def show(self, s: Step) -> None:
        when = dt.datetime.fromtimestamp(s.ts).strftime("%a %d %b, %H:%M:%S")
        n = len(s.files)
        head = Text.assemble((f"#{s.step}  ", "bold #EDEDEA"), (s.tool, "#EDEDEA"), (f"   {when}", "#8F8F89"),
                             (f"   {n} file{'s' * (n != 1)}", "#8F8F89"))
        self.query_one("#head", Static).update(head)
        try:
            diff = self.shadow.diff(s.step)
        except Exception as e:  # noqa: BLE001
            diff = f"(could not read this step: {e})"
        cap = 200_000
        body = render_diff(diff[:cap])
        if len(diff) > cap:
            body.append(f"\n… diff cut at {cap:,} characters; full diff: keelyard rewind show {s.step}", style="#8F8F89")
        self.query_one("#diffbody", Static).update(body)
        self.query_one("#diff", VerticalScroll).scroll_home(animate=False)

    def action_noop(self) -> None:
        pass

    def action_cursor_down(self) -> None:
        self.query_one("#steps", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#steps", OptionList).action_cursor_up()

    def action_first(self) -> None:
        self.query_one("#steps", OptionList).action_first()

    def action_last(self) -> None:
        self.query_one("#steps", OptionList).action_last()

    def action_rewind(self) -> None:
        ol = self.query_one("#steps", OptionList)
        if ol.highlighted is None:
            return
        step = self.steps[ol.highlighted]

        def done(ok: bool | None) -> None:
            if ok:
                res = self.shadow.rewind(step.step)
                self.message = f"restored step #{res['restored']} (undo: keelyard rewind to {res['safety_step']})"
                self.exit()

        self.push_screen(ConfirmRewind(step), done)


def run(shadow: Shadow) -> str | None:
    if not shadow.steps():
        return "no snapshots yet"
    app = Timeline(shadow)
    app.run()
    return app.message
