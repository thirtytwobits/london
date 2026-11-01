"""Pull requests on GitHub, through the `gh` CLI and its authentication."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

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


# The conclusions and states of a finished check that do not stop a merge.
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}


@dataclass(frozen=True, kw_only=True)
class Readiness:
    """What decides whether an open pull request may be merged.

    Attributes:
        head: The full hash of the pull request's head commit.
        draft: Whether the pull request is a draft.
        conflicting: Whether the pull request conflicts with its base branch.
        pending: The names of the checks not yet finished.
        failing: The names of the checks that finished without passing.
    """

    head: str
    draft: bool = False
    conflicting: bool = False
    pending: tuple[str, ...] = ()
    failing: tuple[str, ...] = ()

    @classmethod
    def from_gh(cls, data: Mapping[str, Any]) -> Readiness:
        """Read the output of `gh pr view --json headRefOid,isDraft,mergeable,statusCheckRollup`.

        A check run reports `status` and `conclusion`; a commit status reports `state`.

        Args:
            data: The parsed JSON.
        """
        pending: list[str] = []
        failing: list[str] = []
        for check in data.get("statusCheckRollup") or []:
            name = check.get("name") or check.get("context") or "unnamed check"
            if "state" in check:
                result = check["state"]
                finished = result not in {"PENDING", "EXPECTED"}
            else:
                result = check.get("conclusion") or ""
                finished = check.get("status") == "COMPLETED"
            if not finished:
                pending.append(name)
            elif result not in PASSED:
                failing.append(name)
        return cls(
            head=data["headRefOid"],
            draft=bool(data.get("isDraft")),
            conflicting=data.get("mergeable") == "CONFLICTING",
            pending=tuple(pending),
            failing=tuple(failing),
        )


class MergeMethod(StrEnum):
    """How a pull request's commits reach its base branch, as `gh pr merge` names them."""

    SQUASH = "squash"
    MERGE = "merge"
    REBASE = "rebase"


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
    """Finds, opens and merges pull requests. `GhCli` implements the protocol over the `gh` CLI."""

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

    async def edit(self, pull_request: PullRequest, request: NewPullRequest, log: Log, /) -> None:
        """Give `pull_request` the title and body of `request`.

        Args:
            pull_request: The open pull request.
            request: The pull request as it would be opened now; its repository, title and body are used.
            log: The log the request's output is written to.
        """
        ...

    async def readiness(self, repository: str, pull_request: PullRequest, log: Log, /) -> Readiness:
        """The head commit, draft state, mergeability and checks of `pull_request`.

        Args:
            repository: The repository as `owner/name`.
            pull_request: The open pull request.
            log: The log the lookup's output is written to.
        """
        ...

    async def merge(
        self, repository: str, pull_request: PullRequest, head: str, method: MergeMethod, log: Log, /
    ) -> None:
        """Merge `pull_request` by `method`, provided its head commit is still `head`.

        Args:
            repository: The repository as `owner/name`.
            pull_request: The open pull request.
            head: The full hash of the head commit the merge is made from.
            method: How the commits reach the base branch.
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

    async def edit(self, pull_request: PullRequest, request: NewPullRequest, log: Log) -> None:
        """Retitle `pull_request` and replace its body with `gh pr edit`, passing the body on standard input.

        Args:
            pull_request: The open pull request.
            request: The pull request as it would be opened now; its repository, title and body are used.
            log: The log gh's output is written to.
        """
        argv = ["gh", "pr", "edit", str(pull_request.number), "--repo", request.repository]
        await process.run([*argv, "--title", request.title, "--body-file", "-"], log=log, stdin=request.body)

    async def readiness(self, repository: str, pull_request: PullRequest, log: Log) -> Readiness:
        """The head commit, draft state, mergeability and checks of `pull_request`, read with `gh pr view`.

        Args:
            repository: The repository as `owner/name`.
            pull_request: The open pull request.
            log: The log gh's output is written to.
        """
        fields = "headRefOid,isDraft,mergeable,statusCheckRollup"
        argv = ["gh", "pr", "view", str(pull_request.number), "--repo", repository, "--json", fields]
        _, out = await process.run(argv, log=log)
        return Readiness.from_gh(json.loads(out))

    async def merge(self, repository: str, pull_request: PullRequest, head: str, method: MergeMethod, log: Log) -> None:
        """Merge `pull_request` with `gh pr merge`, which refuses if its head commit is no longer `head`.

        Args:
            repository: The repository as `owner/name`.
            pull_request: The open pull request.
            head: The full hash of the head commit the merge is made from.
            method: How the commits reach the base branch.
            log: The log gh's output is written to.
        """
        argv = ["gh", "pr", "merge", str(pull_request.number), "--repo", repository, f"--{method}"]
        await process.run([*argv, "--match-head-commit", head], log=log)
