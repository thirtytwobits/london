"""The interface every section kind implements."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class Source(Protocol):
    """The London Project repository at the edition's revision."""

    repository: str
    sha: str

    async def read(self, path: str) -> bytes:
        """The contents of `path` at the source commit, byte for byte.

        Args:
            path: A repository-relative path.
        """
        ...


@dataclass
class Applied:
    """What a section did to one subscriber's clone.

    Attributes:
        notes: Remarks for the reviewer, carried into the commit and the pull request.
    """

    notes: list[str] = field(default_factory=list)


class SectionError(Exception):
    """A section cannot be applied as configured."""


class Section(BaseModel, ABC):
    """A piece of the London Project repository that the newsletter carries to subscribers.

    A kind subclasses `Section`, declares `kind` as a `Literal` and registers with `@register`.
    Its fields are its configuration keys, and its docstring is its `newsletter sections` entry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", description="Identifies the section in commit trailers.")
    subject: str = Field(description="The subject line of the section's commit.")

    @abstractmethod
    def paths(self) -> tuple[str, ...]:
        """Repository-relative paths the section writes in a subscriber."""

    async def check(self, source: Source) -> None:
        """Raise `SectionError` if the source cannot supply the section.

        Args:
            source: The source commit.
        """

    @abstractmethod
    async def apply(self, source: Source, clone: Path) -> Applied:
        """Write the section into `clone`.

        Args:
            source: The source commit.
            clone: The subscriber's clone.
        """

    def body(self, repository: str, sha: str) -> str:
        """The body of the section's commit, above the notes and trailers.

        Args:
            repository: The source repository as `owner/name`.
            sha: The full hash of the source commit.
        """
        files = ", ".join(f"`{path}`" for path in self.paths())
        return f"Sent from {repository}@{sha[:12]}: {files}."


_REGISTRY: dict[str, type[Section]] = {}


def register[S: type[Section]](cls: S) -> S:
    """Make a section kind available to configuration.

    Args:
        cls: The section class; it declares a literal `kind` field.
    """
    kind = kind_of(cls)
    if kind in _REGISTRY:
        raise TypeError(f"section kind {kind!r} is registered twice")
    _REGISTRY[kind] = cls
    return cls


def kind_of(cls: type[Section]) -> str:
    """The `kind` a section class declares.

    Args:
        cls: The section class.
    """
    declared = cls.model_fields.get("kind")
    if declared is None or not isinstance(declared.default, str):
        raise TypeError(f"{cls.__name__} needs a literal `kind` field with a default")
    return declared.default


def kinds() -> dict[str, type[Section]]:
    """Every registered section kind, by its `kind`."""
    return dict(_REGISTRY)


def safe_path(path: str) -> str:
    """Refuse absolute paths and paths that leave the repository.

    Args:
        path: The path to check, returned unchanged when it is plain and repository-relative.
    """
    if path.startswith("/") or ".." in Path(path).parts or not re.fullmatch(r"[\w./-]+", path):
        raise ValueError(f"{path!r} is not a plain repository-relative path")
    return path
