"""Git operations on the London Project checkout and on subscriber clones."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import process
from .process import Log, OnStep


class Git:
    """Runs git in one working tree, logging everything it prints.

    Args:
        path: The working tree git runs in.
        log: The log git's output is written to, or `None` to discard it.
    """

    def __init__(self, path: Path, log: Log | None = None) -> None:
        self.path = path
        self.log = log

    async def __call__(self, *args: str, on_step: OnStep | None = None, stdin: str | None = None) -> str:
        """Run git and return its standard output, stripped.

        Args:
            *args: The arguments to git.
            on_step: Called with each progress report git prints.
            stdin: Text passed to git on standard input.
        """
        _, out = await process.run(["git", *args], cwd=self.path, log=self.log, on_step=on_step, stdin=stdin)
        return out.strip()

    async def succeeds(self, *args: str) -> bool:
        """Run git and return whether it exited with status 0.

        Args:
            *args: The arguments to git.
        """
        status, _ = await process.run(["git", *args], cwd=self.path, log=self.log, check=False)
        return status == 0

    async def branch(self) -> str | None:
        """The checked-out branch, or `None` for a detached HEAD."""
        name = await self("rev-parse", "--abbrev-ref", "HEAD")
        return None if name == "HEAD" else name


@dataclass
class Source:
    """The London Project repository at one revision. Files are read from git, not the disk.

    Attributes:
        git: Runs git in the London Project checkout.
        repository: The repository as `owner/name`.
        ref: The ref the revision was resolved from.
        sha: The full commit hash of the revision.
    """

    git: Git
    repository: str
    ref: str
    sha: str

    @classmethod
    async def resolve(
        cls, root: Path, repository: str, ref: str, on_step: OnStep | None = None, log: Log | None = None
    ) -> Source:
        """Resolve `ref` in `root`, first fetching it when it names a remote's branch.

        Args:
            root: The London Project checkout.
            repository: The repository as `owner/name`.
            ref: The ref to resolve, such as `origin/main` or a commit hash.
            on_step: Called with each progress report the fetch prints.
            log: The log git's output is written to.
        """
        git = Git(root, log)
        remote, _, branch = ref.partition("/")
        if branch and remote in (await git("remote")).split():
            await git("fetch", "--progress", remote, branch, on_step=on_step)
        sha = await git("rev-parse", "--verify", f"{ref}^{{commit}}")
        return cls(git, repository, ref, sha)

    async def read(self, path: str) -> str:
        """The contents of `path` at the source commit.

        Args:
            path: A repository-relative path.
        """
        _, out = await process.run(["git", "show", f"{self.sha}:{path}"], cwd=self.git.path)
        return out


@dataclass
class Edition:
    """What `sync` sent to one clone, read by `commit` and `push`. Kept inside the clone's `.git`.

    Attributes:
        repository: The source repository as `owner/name`.
        sha: The full hash of the source commit.
        branch: The edition branch in the clone.
        notes: The notes each section made, by section name.
    """

    repository: str
    sha: str
    branch: str
    notes: dict[str, list[str]] = field(default_factory=dict)

    FILE = "newsletter-edition.json"

    def save(self, clone: Path) -> None:
        """Write the edition into the `.git` directory of `clone`.

        Args:
            clone: The subscriber's clone.
        """
        (clone / ".git" / self.FILE).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, clone: Path) -> Edition | None:
        """The edition last synced into `clone`, or `None` if `sync` has not run there.

        Args:
            clone: The subscriber's clone.
        """
        path = clone / ".git" / cls.FILE
        return cls(**json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None


class Pushed:
    """The commit newsletter last pushed to each branch of a subscriber, kept inside `.git`.

    Args:
        clone: The subscriber's clone.
    """

    FILE = "newsletter-pushed.json"

    def __init__(self, clone: Path) -> None:
        self._path = clone / ".git" / self.FILE
        self._heads: dict[str, str] = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}

    def get(self, branch: str) -> str | None:
        """The commit last pushed to `branch`, or `None`.

        Args:
            branch: The branch's name.
        """
        return self._heads.get(branch)

    def set(self, branch: str, sha: str) -> None:
        """Record `sha` as the commit last pushed to `branch`.

        Args:
            branch: The branch's name.
            sha: The full hash of the commit pushed.
        """
        self._heads[branch] = sha
        self._path.write_text(json.dumps(self._heads, indent=2), encoding="utf-8")


def sparse_patterns(paths: list[str]) -> list[str]:
    """Non-cone sparse-checkout patterns matching exactly `paths`.

    Args:
        paths: Repository-relative paths.
    """
    return ["/" + path for path in paths]
