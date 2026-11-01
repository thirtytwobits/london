"""The phases of an edition. Each phase runs every subscriber concurrently on its own board."""

from __future__ import annotations

import asyncio
import shutil
import textwrap
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.console import Console

from .config import Config, Piece
from .git import Edition, Git, Pushed, Source, sparse_patterns
from .github import MergeMethod, NewPullRequest, PullRequests
from .process import CommandError, Log
from .sections import Section, SectionError
from .ui import Board, Outcome, Row, State


@dataclass
class Workspace:
    """Everything a phase needs: where things are, what to send, and where to show it.

    Attributes:
        root: The London Project checkout.
        config: The loaded `london.toml`.
        console: The console the boards are drawn on.
        pull_requests: Finds, opens and merges pull requests.
        subscribers: The subscribers the phase works on.
        jobs: The number of subscribers worked on at once.
        animate: Whether boards redraw continuously; off when the console is not a terminal.
    """

    root: Path
    config: Config
    console: Console
    pull_requests: PullRequests
    subscribers: list[Piece]
    jobs: int = 8
    animate: bool = True

    @property
    def sections(self) -> list[Section]:
        """The sections sent, in the order they are applied and committed."""
        return self.config.sections

    def sections_for(self, subscriber: Piece) -> list[Section]:
        """The sections sent to `subscriber`, in the order they are applied and committed.

        Args:
            subscriber: The subscriber.
        """
        return self.config.sections_for(subscriber)

    @property
    def staging(self) -> Path:
        """The staging area's absolute path."""
        return self.root / self.config.newsletter.staging

    def clone(self, subscriber: Piece) -> Path:
        """The path of the clone of `subscriber`.

        Args:
            subscriber: The subscriber.
        """
        return self.staging / "clones" / subscriber.name

    def checkout(self, subscriber: Piece) -> Path:
        """The path of the subscriber's own working checkout, which `pull` updates.

        Args:
            subscriber: The subscriber.
        """
        return (self.root / (subscriber.checkout or Path("..", subscriber.name))).resolve()

    def log(self, phase: str, name: str) -> Path:
        """The log file of `name` for `phase`.

        Args:
            phase: The phase's name.
            name: A subscriber's name, or `london` for the source.
        """
        return self.staging / "logs" / phase / f"{name}.log"

    def branch(self, sha: str) -> str:
        """The edition branch for source commit `sha`.

        Args:
            sha: The full hash of the source commit.
        """
        return f"{self.config.newsletter.branch_prefix}{sha[:12]}"


class Refused(Exception):
    """A stage declines to act on a subscriber. `run_phase` shows the message as the subscriber's failure."""


class Stage(Protocol):
    """A phase's work for one subscriber. `run_phase` calls it once for each subscriber, concurrently."""

    async def __call__(self, ws: Workspace, subscriber: Piece, row: Row, log: Log, /) -> Outcome:
        """Do the phase's work for `subscriber` and say how it finished.

        Args:
            ws: The workspace.
            subscriber: The subscriber worked on.
            row: The subscriber's row on the board, which shows each step.
            log: The subscriber's log for the phase, which receives every subprocess's output.
        """
        ...


async def run_phase(
    ws: Workspace,
    phase: str,
    stage: Stage,
    *,
    board_title: str | None = None,
    before: Callable[[Board], Awaitable[bool]] | None = None,
) -> dict[str, Outcome]:
    """Run `stage` for every subscriber, at most `ws.jobs` at once, on one board.

    Args:
        ws: The workspace.
        phase: The phase's name, which names its logs.
        stage: The work done for each subscriber.
        board_title: The board's title; `phase` when absent.
        before: Runs first on the same board and returns whether the subscribers should run.
    """
    names = [s.name for s in ws.subscribers]
    board = Board(ws.console, board_title or phase, ["london", *names], animate=ws.animate, root=ws.root)
    limit = asyncio.Semaphore(ws.jobs)

    async def one(subscriber: Piece, row: Row) -> None:
        async with limit:
            path = ws.log(phase, subscriber.name)
            log = Log.open(path)
            row.status("starting")
            try:
                outcome = await stage(ws, subscriber, row, log)
            except CommandError as error:
                outcome = Outcome(State.FAIL, str(error), error.tail, path)
            except (Refused, SectionError, OSError) as error:
                outcome = Outcome(State.FAIL, str(error), (), path)
            finally:
                log.close()
            if outcome.state is State.FAIL and outcome.log is None:
                outcome.log = path
            board.record(subscriber.name, row, outcome)

    with board.live():
        if before is not None and not await before(board):
            return board.outcomes
        rows = {s.name: board.row(s.name) for s in ws.subscribers}
        async with asyncio.TaskGroup() as group:
            for subscriber in ws.subscribers:
                group.create_task(one(subscriber, rows[subscriber.name]))
    return board.outcomes


def failed(outcomes: dict[str, Outcome]) -> bool:
    """Whether any subscriber failed the phase.

    Args:
        outcomes: The phase's outcomes, by subscriber.
    """
    return any(o.state is State.FAIL for o in outcomes.values())


# clone ---------------------------------------------------------------------------------------


async def clone(ws: Workspace, subscriber: Piece, row: Row, log: Log) -> Outcome:
    """The `clone` phase's `Stage`: make a shallow, sparse clone of the subscriber's base branch, or
    refresh one.
    """
    path = ws.clone(subscriber)
    patterns = sparse_patterns(ws.config.paths())
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        row.status("cloning")
        await Git(path.parent, log)(
            "clone", "--progress", "--depth", "1", "--filter=blob:none", "--sparse", "--single-branch",
            "--branch", subscriber.base, subscriber.remote, path.name,
            on_step=row.step,
        )  # fmt: skip
        git = Git(path, log)
        row.status("checking out")
        await git("sparse-checkout", "set", "--no-cone", *patterns, on_step=row.step)
        return Outcome(State.DONE, f"cloned {subscriber.base} at {(await git('rev-parse', '--short', 'HEAD'))}")

    git = Git(path, log)
    await git("remote", "set-url", "origin", subscriber.remote)
    await fetch_base(git, subscriber, row)
    await git("sparse-checkout", "set", "--no-cone", *patterns, on_step=row.step)
    tip = await git("rev-parse", "--short", f"origin/{subscriber.base}")
    branch = await git.branch()
    if branch == subscriber.base:
        await git("reset", "--keep", f"origin/{subscriber.base}")
        return Outcome(State.DONE, f"updated {subscriber.base} to {tip}")
    return Outcome(State.DONE, f"fetched {subscriber.base} at {tip}; on {branch or 'a detached HEAD'}")


async def fetch_base(git: Git, subscriber: Piece, row: Row) -> None:
    """Fetch the tip of the subscriber's base branch into `origin/<base>`.

    Args:
        git: Runs git in the subscriber's clone.
        subscriber: The subscriber.
        row: The subscriber's row on the board.
    """
    row.status(f"fetching {subscriber.base}")
    # A single-branch clone tracks only the base it was cloned with, so name the tracking ref.
    tracking = f"+refs/heads/{subscriber.base}:refs/remotes/origin/{subscriber.base}"
    await git("fetch", "--progress", "--depth", "1", "origin", tracking, on_step=row.step)


# sync ----------------------------------------------------------------------------------------


async def sync(ws: Workspace) -> dict[str, Outcome]:
    """The `sync` phase: resolve the source and check each section against it for every subscriber
    the section is sent to, then write those sections into each clone on the edition branch.

    Args:
        ws: The workspace.
    """
    source: Source | None = None

    async def resolve(board: Board) -> bool:
        nonlocal source
        row = board.row("london")
        row.status(f"resolving {ws.config.newsletter.ref}")
        path = ws.log("sync", "london")
        log = Log.open(path)
        try:
            source = await Source.resolve(ws.root, ws.config.london.repository, ws.config.newsletter.ref, row.step, log)
            row.status("checking the sections")
            for subscriber in ws.subscribers:
                context = ws.config.context(subscriber)
                for section in ws.sections_for(subscriber):
                    await section.check(source, context)
        except CommandError as error:
            board.record("london", row, Outcome(State.FAIL, str(error), error.tail, path))
            return False
        except SectionError as error:
            board.record("london", row, Outcome(State.FAIL, str(error)))
            return False
        finally:
            log.close()
        ref = ws.config.newsletter.ref
        board.record(
            "london",
            row,
            Outcome(State.DONE, source.sha[:12] if source.sha.startswith(ref) else f"{ref} at {source.sha[:12]}"),
        )
        board.title = f"sync · {ws.branch(source.sha)}"
        return True

    async def stage(ws: Workspace, subscriber: Piece, row: Row, log: Log) -> Outcome:
        assert source is not None
        path = ws.clone(subscriber)
        if not (path / ".git").exists():
            return Outcome(State.SKIP, "not cloned; run `newsletter clone`")
        if not ws.sections_for(subscriber):
            # An edition synced before the sections changed would otherwise still be committed and pushed.
            Edition.discard(path)
            return Outcome(State.SKIP, "no section is sent to it")
        git = Git(path, log)
        branch = ws.branch(source.sha)
        await fetch_base(git, subscriber, row)
        row.status(f"resetting to origin/{subscriber.base}")
        await git("sparse-checkout", "set", "--no-cone", *sparse_patterns(ws.config.paths()), on_step=row.step)
        await git("checkout", "--force", "-B", branch, f"origin/{subscriber.base}", on_step=row.step)
        edition = await write_edition(ws, subscriber, source, branch, row)
        paths = ws.config.paths(subscriber)
        written = [p for p in paths if (path / p).exists()]
        if written:
            # Files a section creates are untracked; marking them lets `git diff` show them.
            await git("add", "--intent-to-add", "--sparse", "--", *written)
        changed = await git("status", "--porcelain", "--", *paths)
        if not changed:
            return Outcome(State.SAME, "up to date")
        count = len(changed.splitlines())
        notes = [note for notes in edition.notes.values() for note in notes]
        return Outcome(
            State.DONE, f"{count} file{'s' * (count != 1)} changed" + (" · notes below" if notes else ""), notes=notes
        )

    return await run_phase(ws, "sync", stage, before=resolve)


async def write_edition(ws: Workspace, subscriber: Piece, source: Source, branch: str, row: Row) -> Edition:
    """Apply each section sent to `subscriber` to its clone, then save and return the edition.

    Args:
        ws: The workspace.
        subscriber: The subscriber.
        source: The source commit.
        branch: The edition branch, checked out in the clone.
        row: The subscriber's row on the board.
    """
    path = ws.clone(subscriber)
    edition = Edition(source.repository, source.sha, branch)
    context = ws.config.context(subscriber)
    for section in ws.sections_for(subscriber):
        row.status(f"applying {section.name}")
        applied = await section.apply(source, path, context)
        if applied.notes:
            edition.notes[section.name] = applied.notes
    edition.save(path)
    return edition


# commit --------------------------------------------------------------------------------------


def message(section: Section, edition: Edition, source_body: str) -> str:
    """The commit message for `section`: its subject, `source_body`, its notes and the trailers.

    Args:
        section: The section committed.
        edition: The edition the commit belongs to.
        source_body: The paragraph naming what was sent and where from.
    """
    paragraphs = [section.subject, *(textwrap.fill(p, 72) for p in (source_body, *edition.notes.get(section.name, [])))]
    trailers = f"Newsletter-Source: {edition.repository}@{edition.sha}\nNewsletter-Section: {section.name}"
    return "\n\n".join([*paragraphs, trailers]) + "\n"


async def commit(ws: Workspace, subscriber: Piece, row: Row, log: Log) -> Outcome:
    """The `commit` phase's `Stage`: commit each section's paths in the subscriber's clone, one commit
    per section.
    """
    path = ws.clone(subscriber)
    edition = Edition.load(path) if (path / ".git").exists() else None
    if edition is None:
        return Outcome(State.SKIP, "nothing synced; run `newsletter sync`")
    git = Git(path, log)
    if await git.branch() != edition.branch:
        return Outcome(State.FAIL, f"not on {edition.branch}; run `newsletter sync`")
    made: list[str] = []
    for section in ws.sections_for(subscriber):
        row.status(f"committing {section.name}")
        paths = section.paths()
        await git("add", "--all", "--sparse", "--", *paths)
        if await git.succeeds("diff", "--cached", "--quiet", "--", *paths):
            continue
        text = message(section, edition, section.body(edition.repository, edition.sha))
        # Naming the paths commits them alone, leaving anything else staged out of the commit.
        await git("commit", "--quiet", "--file=-", "--", *paths, stdin=text)
        made.append(await git("rev-parse", "--short", "HEAD"))
    ahead = int(await git("rev-list", "--count", f"origin/{subscriber.base}..HEAD"))
    if not made:
        return Outcome(State.SAME, f"nothing new; {ahead} commit{'s' * (ahead != 1)} to send")
    return Outcome(State.DONE, f"{len(made)} commit{'s' * (len(made) != 1)}, {made[-1]}")


# push ----------------------------------------------------------------------------------------


TITLE_LIMIT = 256  # GitHub refuses longer pull request titles.


def title(subjects: list[str]) -> str:
    """The pull request title: the commit subjects, oldest first, joined by semicolons.

    Subjects that would take the title past `TITLE_LIMIT` are counted instead, as in `; and 2 more`.

    Args:
        subjects: The subjects of the commits sent, oldest first.
    """
    for shown in range(len(subjects), 0, -1):
        rest = len(subjects) - shown
        text = "; ".join(subjects[:shown]) + (f"; and {rest} more" if rest else "")
        if len(text) <= TITLE_LIMIT:
            return text
    return subjects[0][: TITLE_LIMIT - 1] + "…"


def body(edition: Edition, subjects: list[str]) -> str:
    """The pull request body: the source commit, the commit subjects and the notes.

    Args:
        edition: The edition sent.
        subjects: The subjects of the commits sent, oldest first.
    """
    source = f"[{edition.repository}@{edition.sha[:12]}](https://github.com/{edition.repository}/commit/{edition.sha})"
    lines = [f"This pull request brings the newsletter from {source}.", "", *[f"- {s}" for s in subjects]]
    notes = [note for notes in edition.notes.values() for note in notes]
    if notes:
        lines += ["", "## Notes", "", *[f"- {note}" for note in notes]]
    return "\n".join(lines) + "\n"


def make_push(*, draft: bool, force: bool) -> Stage:
    """Make the `push` phase's `Stage`.

    Args:
        draft: Whether new pull requests are opened as drafts.
        force: Whether to replace an edition branch on GitHub that holds commits newsletter did not push.
    """

    async def push(ws: Workspace, subscriber: Piece, row: Row, log: Log) -> Outcome:
        path = ws.clone(subscriber)
        edition = Edition.load(path) if (path / ".git").exists() else None
        if edition is None:
            return Outcome(State.SKIP, "nothing synced; run `newsletter sync`")
        git = Git(path, log)
        subjects = (
            await git("log", "--reverse", "--format=%s", f"origin/{subscriber.base}..{edition.branch}")
        ).splitlines()
        if not subjects:
            return Outcome(State.SKIP, "no commits to send")

        changed = await push_branch(git, Pushed(path), edition.branch, row, force=force)

        row.status("finding the pull request")
        existing = await ws.pull_requests.find(subscriber.repository, edition.branch, log)
        request = NewPullRequest(
            repository=subscriber.repository,
            base=subscriber.base,
            head=edition.branch,
            title=title(subjects),
            body=body(edition, subjects),
            draft=draft,
        )
        if existing is not None:
            row.status("updating the pull request's title and description")
            await ws.pull_requests.edit(existing, request, log)
            if changed:
                return Outcome(State.DONE, f"updated #{existing.number} {existing.url}")
            return Outcome(State.SAME, f"unchanged #{existing.number} {existing.url}")
        row.status("opening the pull request")
        pr = await ws.pull_requests.open(request, log)
        return Outcome(State.DONE, f"opened #{pr.number} {pr.url}")

    return push


async def push_branch(git: Git, pushed: Pushed, branch: str, row: Row, *, force: bool) -> bool:
    """Push `branch` to `origin` unless `origin` already holds its content, and return whether it pushed.

    `origin`'s branch is replaced only if it holds what newsletter last pushed there, or if `force` is set;
    otherwise `Refused` is raised. The push is leased on the commit read from `origin`.

    Args:
        git: Runs git in the subscriber's clone.
        pushed: The commits newsletter last pushed from the clone.
        branch: The edition branch.
        row: The subscriber's row on the board.
        force: Whether to replace a branch on `origin` holding commits newsletter did not push.
    """
    row.status("checking the remote branch")
    listed = await git("ls-remote", "origin", f"refs/heads/{branch}")
    remote = listed.split()[0] if listed else ""
    local = await git("rev-parse", branch)
    ours = remote == pushed.get(branch)
    if remote and not ours and not force:
        raise Refused(f"{branch} on GitHub holds commits newsletter did not push; --force replaces them")
    if remote == local:
        return False
    # Sending an edition again makes new commits with the content already on GitHub.
    if remote and ours and await git("rev-parse", f"{remote}^{{tree}}") == await git("rev-parse", f"{local}^{{tree}}"):
        return False
    row.status("pushing")
    lease = f"--force-with-lease=refs/heads/{branch}:{remote}"
    await git("push", "--progress", "--porcelain", lease, "origin", f"{branch}:refs/heads/{branch}", on_step=row.step)
    pushed.set(branch, local)
    return True


# send ----------------------------------------------------------------------------------------


async def prepare(ws: Workspace) -> dict[str, Outcome]:
    """Run the `clone`, `sync` and `commit` phases in order, stopping after a phase in which a subscriber fails.

    Returns the outcomes of the last phase run.

    Args:
        ws: The workspace.
    """
    outcomes = await run_phase(ws, "clone", clone)
    if not failed(outcomes):
        outcomes = await sync(ws)
    if not failed(outcomes):
        outcomes = await run_phase(ws, "commit", commit)
    return outcomes


# open ----------------------------------------------------------------------------------------


def make_open(browse: Callable[[str], bool]) -> Stage:
    """Make the `open` phase's `Stage`, which opens the subscriber's edition pull request.

    Args:
        browse: Opens a web address and returns whether a browser took it, as `webbrowser.open` does.
    """

    async def open_pull_request(ws: Workspace, subscriber: Piece, row: Row, log: Log) -> Outcome:
        path = ws.clone(subscriber)
        edition = Edition.load(path) if (path / ".git").exists() else None
        if edition is None:
            return Outcome(State.SKIP, "nothing synced; run `newsletter sync`")
        row.status("finding the pull request")
        found = await ws.pull_requests.find(subscriber.repository, edition.branch, log)
        if found is None:
            return Outcome(State.SKIP, f"no open pull request from {edition.branch}; run `newsletter push`")
        if not browse(found.url):
            raise Refused(f"no browser opened {found.url}; set BROWSER to choose one")
        return Outcome(State.DONE, f"opened #{found.number} {found.url}")

    return open_pull_request


# merge ---------------------------------------------------------------------------------------


def make_merge(*, method: MergeMethod) -> Stage:
    """Make the `merge` phase's `Stage`, which merges the subscriber's edition pull request.

    Args:
        method: How the edition's commits reach the base branch.
    """

    async def merge(ws: Workspace, subscriber: Piece, row: Row, log: Log) -> Outcome:
        path = ws.clone(subscriber)
        edition = Edition.load(path) if (path / ".git").exists() else None
        if edition is None:
            return Outcome(State.SKIP, "nothing synced; run `newsletter sync`")
        row.status("finding the pull request")
        found = await ws.pull_requests.find(subscriber.repository, edition.branch, log)
        if found is None:
            return Outcome(State.SKIP, f"no open pull request from {edition.branch}")
        row.status("reading its checks")
        ready = await ws.pull_requests.readiness(subscriber.repository, found, log)
        where = f"#{found.number} {found.url}"
        if ready.head != Pushed(path).get(edition.branch):
            raise Refused(f"{where} holds commits newsletter did not push; merge it on GitHub")
        if ready.draft:
            raise Refused(f"{where} is a draft")
        if ready.conflicting:
            raise Refused(f"{where} conflicts with {subscriber.base}")
        if ready.failing:
            raise Refused(f"{where} has failing checks: {', '.join(ready.failing)}")
        if ready.pending:
            raise Refused(f"{where} has checks still running: {', '.join(ready.pending)}")
        row.status(f"merging by {method}")
        await ws.pull_requests.merge(subscriber.repository, found, ready.head, method, log)
        return Outcome(State.DONE, f"merged {where}")

    return merge


# pull ----------------------------------------------------------------------------------------


async def pull(ws: Workspace, subscriber: Piece, row: Row, log: Log) -> Outcome:
    """The `pull` phase's `Stage`: fetch the subscriber's own checkout, switch it to its base branch
    and fast-forward the branch to `origin`'s.
    """
    path = ws.checkout(subscriber)
    if not (path / ".git").exists():
        return Outcome(State.SKIP, f"no checkout at {path}")
    git = Git(path, log)
    if await git("status", "--porcelain", "--untracked-files=no"):
        raise Refused(f"{path} has uncommitted changes")
    row.status("fetching")
    await git("fetch", "--progress", "origin", on_step=row.step)
    local, remote = f"refs/heads/{subscriber.base}", f"origin/{subscriber.base}"
    if await git.succeeds("rev-parse", "--verify", "--quiet", local) and not await git.succeeds(
        "merge-base", "--is-ancestor", local, remote
    ):
        raise Refused(f"{subscriber.base} in {path} has commits {remote} lacks")
    was = await git.branch()
    if was != subscriber.base:
        row.status(f"switching to {subscriber.base}")
        await git("switch", subscriber.base)
    before = await git("rev-parse", "HEAD")
    row.status(f"pulling {subscriber.base}")
    await git("merge", "--ff-only", remote)
    tip = await git("rev-parse", "--short", "HEAD")
    switched = "" if was == subscriber.base else f"switched from {was or 'a detached HEAD'}; "
    if not switched and await git("rev-parse", "HEAD") == before:
        return Outcome(State.SAME, f"{subscriber.base} at {tip}")
    return Outcome(State.DONE, f"{switched}{subscriber.base} at {tip}")


# clean ---------------------------------------------------------------------------------------


async def clean(ws: Workspace, subscriber: Piece, row: Row, _log: Log) -> Outcome:
    """The `clean` phase's `Stage`: remove the subscriber's clone. Removing runs no subprocess."""
    path = ws.clone(subscriber)
    if not path.exists():
        return Outcome(State.SKIP, "no clone")
    row.status("removing")
    await asyncio.to_thread(shutil.rmtree, path)
    return Outcome(State.DONE, "removed")
