"""Pull requests on GitHub, through the `gh` CLI and its authentication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from . import process
from .process import Log


@dataclass(frozen=True)
class PullRequest:
    """An open pull request: its number and its web address.

    Attributes:
        number: The pull request's number in its repository.
        url: The pull request's web address.
    """

    number: int
    url: str


@dataclass(frozen=True, kw_only=True)
class NewPullRequest:
    """A pull request to open. Every field is passed by name, so `base` and `head` cannot be swapped unseen.

    Attributes:
        repository: The repository as `owner/name`.
        base: The branch the pull request targets.
        head: The branch the pull request comes from.
        title: The pull request's title.
        body: The pull request's description, in Markdown.
        draft: Whether to open the pull request as a draft.
    """

    repository: str
    base: str
    head: str
    title: str
    body: str
    draft: bool = False


class PullRequests(Protocol):
    """Finds and opens pull requests. `GhCli` implements the protocol over the `gh` CLI."""

    async def find(self, repository: str, head: str, log: Log, /) -> PullRequest | None:
        """The open pull request from `head`, if there is one.

        Args:
            repository: The repository as `owner/name`.
            head: The branch the pull request comes from.
            log: The log the lookup's output is written to.
        """

    async def open(self, request: NewPullRequest, log: Log, /) -> PullRequest:
        """Open `request` and return the pull request.

        Args:
            request: The pull request to open.
            log: The log the request's output is written to.
        """
        ...


class GhCli:
    """`PullRequests` through the `gh` CLI and its sign-in."""

    async def find(self, repository: str, head: str, log: Log) -> PullRequest | None:
        """The open pull request from `head` in `repository`, found with `gh pr list`.

        Args:
            repository: The repository as `owner/name`.
            head: The branch the pull request comes from.
            log: The log gh's output is written to.
        """
        _, out = await process.run(
            ["gh", "pr", "list", "--repo", repository, "--head", head, "--state", "open", "--json", "number,url"],
            log=log,
        )
        found = json.loads(out or "[]")
        return PullRequest(found[0]["number"], found[0]["url"]) if found else None

    async def open(self, request: NewPullRequest, log: Log) -> PullRequest:
        """Open `request` with `gh pr create`, passing its body on standard input.

        Args:
            request: The pull request to open.
            log: The log gh's output is written to.
        """
        argv = ["gh", "pr", "create", "--repo", request.repository, "--base", request.base, "--head", request.head]
        argv += ["--title", request.title, "--body-file", "-"]
        _, out = await process.run([*argv, "--draft"] if request.draft else argv, log=log, stdin=request.body)
        url = out.strip().splitlines()[-1]
        return PullRequest(int(url.rsplit("/", 1)[-1]), url)
