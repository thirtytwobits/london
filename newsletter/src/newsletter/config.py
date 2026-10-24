"""`london.toml`: the London Project's pieces, their releases and what the newsletter sends them."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .sections import Section, kinds

CONFIG_NAME = "london.toml"

_REPOSITORY = r"^[\w.-]+/[\w.-]+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LondonConfig(_Strict):
    """`[london]`: the London Project's own repository.

    Attributes:
        repository: The repository as `owner/name`; editions are sent from it.
    """

    repository: str = Field(pattern=_REPOSITORY)


class Piece(_Strict):
    """`[piece.NAME]`: one piece of the London Project. Every piece subscribes to the newsletter.

    Attributes:
        name: The table's key, which names the piece on the command line.
        repository: The piece's repository as `owner/name`.
        tag: The release of the piece the examples build against; absent until the piece is released.
        base: The branch the newsletter's pull requests target.
        url: The URL the piece is cloned from, when that is not its GitHub repository.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    repository: str = Field(pattern=_REPOSITORY)
    tag: str | None = Field(default=None, pattern=r"^v\d+\.\d+\.\d+$")
    base: str = "main"
    url: str | None = None

    @property
    def remote(self) -> str:
        """The URL the piece is cloned from and pushed to."""
        return self.url or f"https://github.com/{self.repository}.git"


def _section_union() -> object:
    members = tuple(kinds().values())
    return Annotated[Union[members], Field(discriminator="kind")]  # noqa: UP007


class NewsletterConfig(_Strict):
    """`[newsletter]`: how editions are made, and the sections they send.

    Attributes:
        ref: The London Project ref an edition is sent from.
        staging: The staging area, relative to the checkout's root.
        branch_prefix: The prefix of every edition branch; `branch-prefix` in the file.
        section: The sections, in the order they are applied and committed.
    """

    ref: str = "origin/main"
    staging: Path = Path(".newsletter")
    branch_prefix: str = Field(default="newsletter/", alias="branch-prefix")
    section: list[_section_union()] = Field(min_length=1)  # type: ignore[valid-type]


class Config(_Strict):
    """The whole of `london.toml`. Piece repositories, section names and section paths are each unique.

    Attributes:
        london: The London Project's own repository.
        piece: The pieces, by name, in the order the file lists them.
        newsletter: How editions are made, and the sections they send.
    """

    london: LondonConfig
    piece: dict[str, Piece] = Field(min_length=1)
    newsletter: NewsletterConfig

    @model_validator(mode="before")
    @classmethod
    def _name_pieces(cls, data: Any) -> Any:
        pieces = data.get("piece") if isinstance(data, dict) else None
        if not isinstance(pieces, dict):
            return data
        named = {}
        for name, table in pieces.items():
            if isinstance(table, dict) and "name" in table:
                raise ValueError(f"piece.{name}: a piece is named by its table's key, not a `name` key")
            named[name] = {**table, "name": name} if isinstance(table, dict) else table
        return {**data, "piece": named}

    @model_validator(mode="after")
    def _distinct(self) -> Config:
        for label, values in (
            ("piece repository", [p.repository for p in self.pieces]),
            ("section", [s.name for s in self.sections]),
            ("section path", self.paths()),
        ):
            repeated = sorted({v for v in values if values.count(v) > 1})
            if repeated:
                raise ValueError(f"each {label} appears once; repeated: {', '.join(repeated)}")
        return self

    @property
    def pieces(self) -> list[Piece]:
        """The pieces, in the order the file lists them."""
        return list(self.piece.values())

    @property
    def sections(self) -> list[Section]:
        """The configured sections, in the order they are applied and committed."""
        return list(self.newsletter.section)

    def paths(self) -> list[str]:
        """Every path the sections write, in section order."""
        return [path for section in self.newsletter.section for path in section.paths()]


class ConfigError(Exception):
    """`london.toml` is missing, malformed or invalid."""


def load(path: Path) -> Config:
    """Read and validate the configuration at `path`, raising `ConfigError` with every problem found.

    Args:
        path: The `london.toml` to read.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None
    try:
        return Config.model_validate(data)
    except ValidationError as error:
        lines = [f"{'.'.join(map(str, e['loc'])) or '(top level)'}: {e['msg']}" for e in error.errors()]
        raise ConfigError(f"{path}:\n  " + "\n  ".join(lines)) from None
