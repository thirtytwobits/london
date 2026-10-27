"""The interface every section kind implements."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class SectionError(Exception):
    """A section cannot be applied as configured."""


@dataclass(frozen=True)
class Context:
    """The subscriber a section is applied to, and the values of the tokens rendered for it.

    Attributes:
        subscriber: The subscriber's name.
        tokens: The value of each token, by name, such as `piece.title`.
    """

    subscriber: str
    tokens: Mapping[str, str] = field(default_factory=dict)


TOKEN = re.compile(r"\{\{\s*london:([^{}]*?)\s*\}\}")


def render(text: str, context: Context, where: str) -> str:
    """Replace each `{{london:NAME}}` token in `text` with the value of NAME in `context`.

    Text that is not a token, such as `${{ github.ref }}` or `{{version}}`, is left as it is.

    Args:
        text: The text to render.
        context: Supplies the value of each token.
        where: Names the text in the error raised when it holds a token `context` has no value for.
    """
    unknown = sorted({name for name in TOKEN.findall(text) if name not in context.tokens})
    if unknown:
        raise SectionError(
            f"{where}: no value for {', '.join(unknown)} when sent to {context.subscriber};"
            " `newsletter sections` lists the tokens"
        )
    return TOKEN.sub(lambda match: context.tokens[match.group(1)], text)


@dataclass
class Applied:
    """What a section did to one subscriber's clone.

    Attributes:
        notes: Remarks for the reviewer, carried into the commit and the pull request.
    """

    notes: list[str] = field(default_factory=list)


class Section(BaseModel, ABC):
    """A piece of the London Project repository that the newsletter carries to subscribers.

    A kind subclasses `Section`, declares `kind` as a `Literal` and registers with `@register`.
    Its fields are its configuration keys, and its docstring is its `newsletter sections` entry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", description="Identifies the section in commit trailers.")
    subject: str = Field(description="The subject line of the section's commit.")
    pieces: list[str] | None = Field(
        default=None, min_length=1, description="The pieces the section is sent to, by name; every piece when absent."
    )
    render: bool = Field(
        default=False,
        description="Whether `{{london:NAME}}` tokens in the text sent are replaced with each piece's values.",
    )

    @field_validator("pieces")
    @classmethod
    def _distinct_pieces(cls, value: list[str] | None) -> list[str] | None:
        """Refuse a piece named twice.

        Args:
            value: The configured pieces.
        """
        if value is not None and len(set(value)) != len(value):
            raise ValueError("each piece is named once")
        return value

    def sends_to(self, piece: str) -> bool:
        """Whether the section is sent to `piece`.

        Args:
            piece: The piece's name.
        """
        return self.pieces is None or piece in self.pieces

    def rendered(self, text: str, context: Context, where: str) -> str:
        """`text` with its tokens replaced if the section renders, and unchanged if it does not.

        Args:
            text: The text sent.
            context: Supplies the value of each token.
            where: Names the text in the error raised for a token without a value.
        """
        return render(text, context, where) if self.render else text

    @abstractmethod
    def paths(self) -> tuple[str, ...]:
        """Repository-relative paths the section writes in a subscriber."""

    async def check(self, source: Source, context: Context) -> None:
        """Raise `SectionError` if the source cannot supply the section to the subscriber `context` names.

        Args:
            source: The source commit.
            context: The subscriber and its tokens.
        """

    @abstractmethod
    async def apply(self, source: Source, clone: Path, context: Context) -> Applied:
        """Write the section into `clone`.

        Args:
            source: The source commit.
            clone: The subscriber's clone.
            context: The subscriber and its tokens.
        """

    @classmethod
    def reference(cls) -> dict[str, dict[str, str]]:
        """Tables of terms the kind's keys take, by title, which `newsletter sections` shows."""
        return {}

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


def decode(data: bytes, where: str) -> str:
    """Decode a text file, which must be UTF-8.

    Args:
        data: The file's contents.
        where: Names the file in the error raised when it is not UTF-8.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SectionError(f"{where} is not UTF-8 text: {error.reason} at byte {error.start}") from None
