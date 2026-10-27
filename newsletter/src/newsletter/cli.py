"""The `newsletter` command line. Its help is the newsletter's documentation."""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import webbrowser
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import pipeline, process
from .config import CONFIG_NAME, Config, ConfigError, Piece, load
from .git import MINIMUM_VERSION, Edition, Git, version
from .github import GhCli
from .pipeline import Workspace
from .sections import Section, kind_of, kinds
from .ui import ICONS, Outcome, State

console = Console(highlight=False)
errors = Console(stderr=True, highlight=False)

WORKFLOW = "Edition"
INSPECT = "Inspection"
HOUSEKEEPING = "Housekeeping"

app = typer.Typer(
    name="newsletter",
    rich_markup_mode="markdown",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="""
Send material the London Project maintains to each piece's repository as a pull request.

`london.toml`, at the root of the London Project checkout, names the pieces and the release of each
that the examples build against, and configures the newsletter. The **source** is the London Project
repository at `newsletter.ref` (normally `origin/main`, fetched first); files are read from that
commit, never from the working tree. The **subscribers** are the pieces' repositories, every one of
them. The **sections** are what is sent; `newsletter sections` describes them.

An **edition** is one source commit sent to every subscriber, in four phases run in order: `clone`,
`sync`, `commit` and `push`. `send` runs all four. Each phase works on every subscriber at once, one
row each, and writes the output of git and gh to `.newsletter/logs/PHASE/SUBSCRIBER.log`. A
subscriber that fails does not stop the others; the phase then exits with status 1.

The edition's branch in each subscriber is `newsletter/` and the first twelve characters of the
source commit, so sending the same commit again updates the same pull request.
""",
)


# --- options shared by the phases --------------------------------------------------------------

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        metavar="FILE",
        help=f"The configuration. Default: the nearest `{CONFIG_NAME}` at or above the working directory.",
        show_default=False,
        envvar="NEWSLETTER_CONFIG",
    ),
]
SubscriberOption = Annotated[
    list[str] | None,
    typer.Option(
        "--subscriber",
        "-s",
        metavar="NAME",
        help="Work on this subscriber only, by repository name. Repeatable.",
        show_default=False,
    ),
]
JobsOption = Annotated[int, typer.Option("--jobs", "-j", min=1, metavar="N", help="Subscribers worked on at once.")]


def find_config(start: Path) -> Path:
    """Return the nearest `london.toml` at or above `start`.

    Args:
        start: The directory the search begins in.
    """
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise ConfigError(f"no {CONFIG_NAME} at or above {start}")


def toplevel(path: Path) -> Path:
    """Return the root of the git working tree holding `path`.

    Args:
        path: A directory inside the working tree.
    """
    argv = ["git", "rev-parse", "--show-toplevel"]
    result = subprocess.run(argv, cwd=path, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ConfigError(f"{path} is not inside the London Project checkout")
    return Path(result.stdout.strip())


def fail(message: str, exit_status: int = 2) -> typer.Exit:
    """Print `message` as an error and return the `typer.Exit` that ends the command.

    Args:
        message: The error, printed to standard error.
        exit_status: The command's exit status.
    """
    errors.print(f"[bold red]✘[/] {message}")
    return typer.Exit(exit_status)


def workspace(config_path: Path | None, only: list[str] | None, jobs: int = 8) -> Workspace:
    """Load the configuration and build the workspace for the chosen subscribers.

    Args:
        config_path: The configuration file, or `None` to search for one from the working directory.
        only: The names of the subscribers to work on, or `None` for all of them.
        jobs: The number of subscribers worked on at once.
    """
    try:
        path = (config_path or find_config(Path.cwd())).resolve()
        config = load(path)
        root = toplevel(path.parent)
    except ConfigError as error:
        raise fail(str(error)) from None
    known = {s.name: s for s in config.pieces}
    unknown = sorted(set(only or ()) - known.keys())
    if unknown:
        raise fail(f"unknown subscriber {', '.join(unknown)}; the subscribers are {', '.join(known)}")
    chosen = [s for s in config.pieces if not only or s.name in only]
    return Workspace(root, config, console, GhCli(), chosen, jobs=jobs, animate=console.is_terminal)


def run[T](work: Coroutine[Any, Any, T]) -> T:
    """Run `work` to completion, ending the command with status 130 on an interrupt.

    Args:
        work: The coroutine to run.
    """
    try:
        return asyncio.run(work)
    except KeyboardInterrupt:
        raise fail("interrupted", 130) from None


def plural(count: int, noun: str) -> str:
    """`count` and `noun`, the noun made plural unless the count is one.

    Args:
        count: The number of things.
        noun: The singular name of one thing.
    """
    return f"{count} {noun}{'s' * (count != 1)}"


def settle(outcomes: dict[str, Outcome]) -> None:
    """End the command with status 1 if any subscriber failed the phase.

    Args:
        outcomes: The phase's outcomes, by subscriber.
    """
    if pipeline.failed(outcomes):
        raise typer.Exit(1)


# --- edition phases ----------------------------------------------------------------------------


@app.command(rich_help_panel=WORKFLOW)
def init(config: ConfigOption = None) -> None:
    """
    Check the tools and the configuration, and create the staging area.

    The staging area is `staging.path` under the checkout, normally `.newsletter/`. It holds the
    subscribers' clones and the logs, and git must ignore it.
    """
    ws = workspace(config, None)

    async def checks() -> list[tuple[str, bool, str]]:
        async def tool(argv: list[str]) -> tuple[int, str]:
            try:
                return await process.run(argv, check=False)
            except FileNotFoundError:
                return 127, ""

        (git_status, git_out), (gh_status, _), (auth_status, _) = await asyncio.gather(
            tool(["git", "--version"]),
            tool(["gh", "--version"]),
            tool(["gh", "auth", "status", "--hostname", "github.com"]),
        )
        found = version(git_out) if git_status == 0 else ()
        ws.staging.mkdir(parents=True, exist_ok=True)
        ignored = await Git(ws.root).succeeds("check-ignore", "--quiet", str(ws.staging))
        return [
            (
                f"git {'.'.join(map(str, MINIMUM_VERSION))} or later",
                found >= MINIMUM_VERSION,
                git_out.strip() or "git is not on PATH",
            ),
            ("gh installed", gh_status == 0, "gh is on PATH" if gh_status == 0 else "install the GitHub CLI"),
            (
                "gh signed in to github.com",
                auth_status == 0,
                "signed in" if auth_status == 0 else "run `gh auth login`",
            ),
            (
                "configuration",
                True,
                f"{plural(len(ws.config.pieces), 'piece')}, {plural(len(ws.config.sections), 'section')}",
            ),
            (
                "staging area ignored by git",
                ignored,
                str(ws.staging.relative_to(ws.root))
                + ("/" if ignored else f"/ is not ignored; add /{ws.config.newsletter.staging}/ to .gitignore"),
            ),
        ]

    with console.status("checking", spinner="dots"):
        results = run(checks())
    table = Table.grid(padding=(0, 2))
    for label, ok, detail in results:
        table.add_row(ICONS[State.DONE] if ok else ICONS[State.FAIL], label, Text(detail, style="dim" if ok else "red"))
    ok = all(passed for _, passed, _ in results)
    console.print(
        Panel(table, title="[bold]init", title_align="left", border_style="green" if ok else "red", padding=(0, 1))
    )
    if not ok:
        raise typer.Exit(1)


@app.command(rich_help_panel=WORKFLOW)
def clone(config: ConfigOption = None, subscriber: SubscriberOption = None, jobs: JobsOption = 8) -> None:
    """
    Make or refresh a shallow, sparse clone of each subscriber's base branch.

    A clone holds one commit and only the files the sections write. A clone on its base branch
    moves to the fetched commit, keeping local changes; a clone on an edition branch stays
    there.
    """
    ws = workspace(config, subscriber, jobs)
    settle(run(pipeline.run_phase(ws, "clone", pipeline.clone)))


@app.command(rich_help_panel=WORKFLOW)
def sync(config: ConfigOption = None, subscriber: SubscriberOption = None, jobs: JobsOption = 8) -> None:
    """
    Write every section into each clone, on the edition branch.

    Resolves the source first. Each clone fetches its base branch and is reset to it, discarding
    its changes, and the edition branch is made there afresh. Edit the result before `commit` to adjust what
    is sent; `diff` shows it.
    """
    ws = workspace(config, subscriber, jobs)
    settle(run(pipeline.sync(ws)))


@app.command(rich_help_panel=WORKFLOW)
def commit(config: ConfigOption = None, subscriber: SubscriberOption = None, jobs: JobsOption = 8) -> None:
    """
    Commit each section's files in each clone, one commit per section.

    A commit's subject is the section's `subject`. Its body names the source commit and carries
    any notes `sync` made, and its trailers are `Newsletter-Source` and `Newsletter-Section`. A
    section with nothing to commit makes no commit.
    """
    ws = workspace(config, subscriber, jobs)
    settle(run(pipeline.run_phase(ws, "commit", pipeline.commit)))


DraftOption = Annotated[bool, typer.Option("--draft", help="Open new pull requests as drafts.")]
ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Replace an edition branch on GitHub that holds commits newsletter did not push."),
]


@app.command(rich_help_panel=WORKFLOW)
def push(
    config: ConfigOption = None,
    subscriber: SubscriberOption = None,
    jobs: JobsOption = 8,
    draft: DraftOption = False,
    force: ForceOption = False,
) -> None:
    """
    Push each edition branch to GitHub and open its pull request.

    A branch already on GitHub is replaced only if it holds what newsletter last pushed there. A
    pull request is titled by its commits' subjects, and its body lists them and the notes. One is
    opened against the base branch, or, if one is already open from the branch, given the new title
    and body.
    """
    ws = workspace(config, subscriber, jobs)
    settle(run(pipeline.run_phase(ws, "push", pipeline.make_push(draft=draft, force=force))))


@app.command(rich_help_panel=WORKFLOW)
def send(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # each one is a CLI option
    config: ConfigOption = None,
    subscriber: SubscriberOption = None,
    jobs: JobsOption = 8,
    draft: DraftOption = False,
    force: ForceOption = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Push without asking.")] = False,
) -> None:
    """
    Run `clone`, `sync` and `commit`, then ask before `push`.

    Stops after the first phase in which a subscriber fails.
    """
    ws = workspace(config, subscriber, jobs)
    settle(run(pipeline.prepare(ws)))
    if not yes and not typer.confirm(
        f"Push to {len(ws.subscribers)} subscribers and open pull requests?", default=False
    ):
        raise typer.Exit(0)
    settle(run(pipeline.run_phase(ws, "push", pipeline.make_push(draft=draft, force=force))))


# --- inspection --------------------------------------------------------------------------------


@app.command(rich_help_panel=INSPECT)
def status(config: ConfigOption = None, subscriber: SubscriberOption = None) -> None:
    """
    Show each clone's branch, the source commit synced into it, its commits and changes not yet
    sent, and its open pull request.
    """
    ws = workspace(config, subscriber)

    async def row(sub: Piece) -> list[str]:
        path = ws.clone(sub)
        if not (path / ".git").exists():
            return [sub.name, "[dim]not cloned", "", "", "", ""]
        git = Git(path)
        edition = Edition.load(path)
        branch = await git.branch() or "detached"
        ahead, changed = await asyncio.gather(
            git("rev-list", "--count", f"origin/{sub.base}..HEAD"), git("status", "--porcelain")
        )
        pr = "[dim]–"
        if edition is not None:
            log = process.Log.open(ws.log("status", sub.name))
            try:
                found = await ws.pull_requests.find(sub.repository, edition.branch, log)
                pr = f"#{found.number} {found.url}" if found else "[dim]none"
            except process.CommandError:
                pr = "[red]gh failed"
            finally:
                log.close()
        files = len(changed.splitlines())
        return [
            sub.name,
            escape(branch),
            edition.sha[:12] if edition else "[dim]–",
            ahead,
            f"[yellow]{files}" if files else "[dim]0",
            pr,
        ]

    async def rows() -> list[list[str]]:
        return await asyncio.gather(*(row(s) for s in ws.subscribers))

    with console.status("reading the clones and GitHub", spinner="dots"):
        found = run(rows())
    table = Table(box=None, padding=(0, 2), header_style="bold")
    for heading in ("subscriber", "branch", "edition", "commits", "changes", "pull request"):
        table.add_column(heading, no_wrap=heading != "pull request")
    for cells in found:
        table.add_row(*cells)
    console.print(Panel(table, title="[bold]status", title_align="left", border_style="blue", padding=(0, 1)))


@app.command("open", rich_help_panel=INSPECT)
def open_pull_requests(config: ConfigOption = None, subscriber: SubscriberOption = None) -> None:
    """
    Open each subscriber's edition pull request in the web browser.

    The browser is the system's default, or the one the `BROWSER` environment variable names. A
    subscriber with no edition synced, or no open pull request from its edition branch, is skipped.
    """
    ws = workspace(config, subscriber)
    settle(run(pipeline.run_phase(ws, "open", pipeline.make_open(webbrowser.open))))


@app.command(rich_help_panel=INSPECT)
def diff(
    config: ConfigOption = None,
    subscriber: SubscriberOption = None,
    stat: Annotated[bool, typer.Option("--stat", help="Show only the files changed and their line counts.")] = False,
) -> None:
    """Show what each clone would send: its commits and its changes against its base branch."""
    ws = workspace(config, subscriber)

    async def one(sub: Piece) -> tuple[str, str | None]:
        path = ws.clone(sub)
        if not (path / ".git").exists():
            return sub.name, None
        args = ["diff", f"--stat={console.width - 4}"] if stat else ["diff", "--no-color"]
        return sub.name, await Git(path)(*args, f"origin/{sub.base}")

    async def every() -> list[tuple[str, str | None]]:
        return await asyncio.gather(*(one(s) for s in ws.subscribers))

    with console.status("diffing", spinner="dots"):
        found = run(every())
    for name, text in found:
        body: RenderableType
        if text is None:
            body = Text("not cloned", style="dim")
        elif not text:
            body = Text("no difference from the base branch", style="dim")
        else:
            body = (
                Text(text)
                if stat
                else Syntax(text, "diff", theme="ansi_dark", background_color="default", word_wrap=True)
            )
        console.print(Panel(body, title=f"[bold]{name}", title_align="left", border_style="blue" if text else "dim"))


@app.command(rich_help_panel=INSPECT)
def sections(config: ConfigOption = None) -> None:
    """
    Describe the configured sections, every section kind with its keys, and the tokens a section
    that renders replaces.

    A token is `{{london:NAME}}`, and is replaced with NAME's value for the piece the section is
    sent to. A token without a value for that piece stops `sync` before any subscriber is written.
    """
    ws = workspace(config, None)
    configured = Table(box=None, padding=(0, 2), header_style="bold")
    for heading in ("name", "kind", "writes", "sent to", "commit subject"):
        configured.add_column(heading)
    for section in ws.sections:
        configured.add_row(
            section.name,
            kind_of(type(section)) + (", rendered" if section.render else ""),
            ", ".join(section.paths()),
            ", ".join(section.pieces) if section.pieces else "[dim]every piece",
            section.subject,
        )
    console.print(
        Panel(
            configured,
            title=f"[bold]sections in {CONFIG_NAME}",
            title_align="left",
            border_style="blue",
            padding=(0, 1),
        )
    )

    for kind, cls in kinds().items():
        console.print(kind_panel(kind, cls))

    console.print(token_panel(ws.config))


def token_panel(config: Config) -> Panel:
    """The tokens and their value for each piece; a blank cell is a token the piece has no value for.

    Args:
        config: The loaded configuration.
    """
    contexts = [config.context(piece) for piece in config.pieces]
    names = list(dict.fromkeys(name for context in contexts for name in context.tokens))
    table = Table(box=None, padding=(0, 2), header_style="bold")
    table.add_column("token", style="cyan", no_wrap=True)
    for context in contexts:
        table.add_column(context.subscriber)
    for name in names:
        table.add_row(f"{{{{london:{name}}}}}", *(escape(context.tokens.get(name, "")) for context in contexts))
    return Panel(table, title="[bold]tokens", title_align="left", border_style="dim", padding=(0, 1))


def kind_panel(kind: str, cls: type[Section]) -> Panel:
    """A section kind's documentation: its docstring, its keys and its reference tables.

    Args:
        kind: The kind's name.
        cls: The kind's class.
    """
    keys = Table(box=None, padding=(0, 2), show_header=False)
    keys.add_column(style="cyan", no_wrap=True)
    keys.add_column()
    for key, field in cls.model_fields.items():
        if key != "kind":
            optional = "" if field.is_required() else " [dim](optional)[/]"
            keys.add_row(key, f"{field.description or ''}{optional}")
    parts: list[RenderableType] = [Markdown(inspect.cleandoc(cls.__doc__ or "")), Text(""), keys]
    for title, terms in cls.reference().items():
        table = Table(
            box=None, padding=(0, 2), title=title, title_justify="left", title_style="bold", show_header=False
        )
        table.add_column(style="cyan", no_wrap=True)
        table.add_column()
        for term, meaning in terms.items():
            table.add_row(term, meaning)
        parts += [Text(""), table]
    return Panel(Group(*parts), title=f'[bold]kind = "{kind}"', title_align="left", border_style="dim", padding=(0, 1))


# --- housekeeping ------------------------------------------------------------------------------


@app.command(rich_help_panel=HOUSEKEEPING)
def clean(
    config: ConfigOption = None,
    subscriber: SubscriberOption = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Remove without asking.")] = False,
) -> None:
    """Remove subscribers' clones, with any commits in them not yet pushed."""
    ws = workspace(config, subscriber)
    present = [s for s in ws.subscribers if ws.clone(s).exists()]
    if not present:
        console.print("[dim]no clones to remove")
        return
    if not yes and not typer.confirm(f"Remove the clones of {', '.join(s.name for s in present)}?", default=False):
        raise typer.Exit(0)
    settle(run(pipeline.run_phase(ws, "clean", pipeline.clean)))


def main() -> None:
    """The `newsletter` console script."""
    app()
