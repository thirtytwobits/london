"""The live board: one panel per phase, one row per subscriber, nothing else on screen.

Subprocess output goes to logs. A failed row's last lines of output are printed under the panel
when the phase ends.

Definitions:
    subscriber: A piece's repository, which receives the newsletter. Every piece named by a
        `[piece.NAME]` table in `london.toml` is a subscriber.
    section: A part of the London Project repository the newsletter sends, such as a marked block
        of `AGENTS.md`, named by a `[[newsletter.section]]` table. Each section makes at most one commit in
        each subscriber; `newsletter sections` describes the kinds.
    edition: One source commit sent to every subscriber, on a branch named after the commit, in the
        phases `clone`, `sync`, `commit` and `push`.
    phase: One step of an edition, such as `clone` or `push`, done for every subscriber at once by
        `pipeline.run_phase`. Each phase has one board and its own logs.
    board: Everything shown for one phase, redrawn while the phase runs: a panel holding one row
        for each subscriber, and for the source in `sync`. `Board` draws it.
    panel: The bordered box a board is drawn in, titled with the phase. Its border is blue while
        rows run, green once every row has finished, and red if any row failed.
    row: One line of a board, for one subscriber or the source. While running, a row shows a
        spinner and its current step, with a bar and percentage when git reports how far the step
        has got; once finished, it shows an icon and a one-line outcome. `Row` updates it.
    column: A vertical slice through every row of a board. A board has four: the state (spinner or
        outcome icon), the name, the detail (step and bar, or outcome) and the time elapsed.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from rich.console import Console, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Column, Table
from rich.text import Text

from .process import Step


class State(Enum):
    """How a row finished: it changed something, found nothing to change, was skipped, or failed."""

    DONE = "done"
    SAME = "same"
    SKIP = "skip"
    FAIL = "fail"


ICONS = {
    State.DONE: Text("✔", style="green"),
    State.SAME: Text("=", style="cyan"),
    State.SKIP: Text("–", style="dim"),
    State.FAIL: Text("✘", style="bold red"),
}

SUMMARY_STYLE = {State.DONE: "", State.SAME: "cyan", State.SKIP: "dim", State.FAIL: "red"}


@dataclass
class Outcome:
    """How one subscriber finished a phase.

    Attributes:
        state: How the subscriber finished.
        summary: One line shown on the subscriber's row.
        tail: The last lines of output of a failed command.
        log: The log of a failed subscriber.
        notes: Remarks printed once under the board.
    """

    state: State
    summary: str
    tail: tuple[str, ...] = ()
    log: Path | None = None
    notes: list[str] = field(default_factory=list)


class _StateColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__(table_column=Column(width=1, no_wrap=True))
        self._spinner = SpinnerColumn("dots", style="yellow")

    def render(self, task: Task) -> RenderableType:
        state = task.fields.get("state")
        return ICONS[state] if state in ICONS else self._spinner.render(task)


class _DetailColumn(ProgressColumn):
    """The step, bar and percentage of a running row, or the whole width for a finished row's summary."""

    def __init__(self) -> None:
        super().__init__(table_column=Column(ratio=1, no_wrap=True, overflow="ellipsis"))
        self._bar = BarColumn(bar_width=24, complete_style="yellow", pulse_style="yellow")

    def render(self, task: Task) -> RenderableType:
        description = Text.from_markup(task.description, overflow="ellipsis")
        description.no_wrap = True
        if task.fields.get("state") in ICONS:
            return description
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(width=24)
        grid.add_column(width=4, justify="right")
        grid.add_row(
            description, self._bar.render(task), Text(f"{task.percentage:.0f}%" if task.total else "", style="dim")
        )
        return grid


class _ElapsedColumn(TimeElapsedColumn):
    def render(self, task: Task) -> Text:
        text = super().render(task)
        text.stylize("dim")
        return text


class Row:
    """A subscriber's line on the board.

    Args:
        progress: The board's progress table.
        task: The row's task in the table.
    """

    def __init__(self, progress: Progress, task: TaskID) -> None:
        self._progress = progress
        self._task = task

    def status(self, text: str) -> None:
        """Start a step whose extent is unknown.

        Args:
            text: What the subscriber is doing.
        """
        self._progress.update(self._task, description=text, total=None, completed=0)
        self._progress.start_task(self._task)

    def step(self, step: Step) -> None:
        """Report git's progress through a step.

        Args:
            step: The progress report.
        """
        self._progress.update(self._task, description=step.label, total=step.total, completed=step.done)

    def finish(self, outcome: Outcome) -> None:
        """Show `outcome` in place of the row's step.

        Args:
            outcome: How the subscriber finished.
        """
        style = SUMMARY_STYLE[outcome.state]
        self._progress.update(
            self._task,
            description=f"[{style}]{outcome.summary}[/]" if style else outcome.summary,
            state=outcome.state,
            total=1,
            completed=1,
        )
        self._progress.stop_task(self._task)


class Board:
    """A panel of rows for one phase.

    Args:
        console: The console the board is drawn on.
        title: The panel's title.
        names: The names of the rows, which set the width of the name column.
        animate: Whether the board redraws continuously.
        root: The checkout that log paths under failures are shown relative to.
    """

    def __init__(
        self, console: Console, title: str, names: list[str], *, animate: bool, root: Path | None = None
    ) -> None:
        self.console = console
        self.root = root
        self.title = title
        width = max(map(len, names), default=8)
        self.progress = Progress(
            _StateColumn(),
            TextColumn("[bold]{task.fields[name]}", table_column=Column(width=width, no_wrap=True)),
            _DetailColumn(),
            _ElapsedColumn(table_column=Column(width=7, justify="right")),
            console=console,
            expand=True,
            auto_refresh=False,
        )
        self.outcomes: dict[str, Outcome] = {}
        self._live = Live(
            self._render(), console=console, auto_refresh=animate, refresh_per_second=12.5, transient=False
        )

    def _render(self) -> RenderableType:
        failed = any(o.state is State.FAIL for o in self.outcomes.values())
        style = "red" if failed else "green" if self.outcomes and self._finished() else "blue"
        return Panel(self.progress, title=f"[bold]{self.title}", title_align="left", border_style=style, padding=(0, 1))

    def _finished(self) -> bool:
        return all(task.finished for task in self.progress.tasks)

    def row(self, name: str) -> Row:
        """Add a row for `name`, shown as waiting.

        Args:
            name: The subscriber's name, or `london` for the source.
        """
        task = self.progress.add_task("[dim]waiting", name=name, state=None, start=False, total=None)
        self.refresh()
        return Row(self.progress, task)

    def record(self, name: str, row: Row, outcome: Outcome) -> None:
        """Store the outcome for `name` and show it on its row.

        Args:
            name: The row's name.
            row: The row.
            outcome: How the subscriber finished.
        """
        self.outcomes[name] = outcome
        row.finish(outcome)
        self.refresh()

    def refresh(self) -> None:
        """Redraw the board."""
        self._live.update(self._render(), refresh=True)

    @contextmanager
    def live(self) -> Generator[Board]:
        """Show the board live for the duration of the `with` block, then print notes and failures under it."""
        with self._live:
            yield self
            self.refresh()
        if not self.console.is_terminal:
            self.console.line()
        self.report()

    def report(self) -> None:
        """Print each note once with the subscribers it applies to, then each failure with its last lines
        of output.
        """
        by_note: dict[str, list[str]] = {}
        for name, outcome in self.outcomes.items():
            for note in outcome.notes:
                by_note.setdefault(note, []).append(name)
        for note, names in by_note.items():
            self.console.print(
                Text.assemble(("📝 ", ""), (", ".join(names), "bold"), ": ", Text.from_markup(note.replace("`", "")))
            )
        for name, outcome in self.outcomes.items():
            if outcome.state is not State.FAIL:
                continue
            if not outcome.tail:
                self.console.print(
                    Text.assemble(ICONS[State.FAIL], " ", (name, "bold"), ": ", (outcome.summary, "red"))
                )
                continue
            lines = Text("\n".join(outcome.tail), style="dim")
            log = outcome.log
            if log is not None and self.root is not None and log.is_relative_to(self.root):
                log = log.relative_to(self.root)
            footer = f"[dim]log: {log}" if log else None
            self.console.print(
                Panel(
                    lines,
                    title=f"[red]{name}[/] · {outcome.summary}",
                    title_align="left",
                    subtitle=footer,
                    subtitle_align="left",
                    border_style="red",
                )
            )
