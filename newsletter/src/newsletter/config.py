"""`london.toml`: the London Project's pieces, their releases and what the newsletter sends them."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .sections import Context, Section, kinds

CONFIG_NAME = "london.toml"

_REPOSITORY = r"^[\w.-]+/[\w.-]+$"
_VARIABLE = r"^[a-z][a-z0-9_-]*$"

Variables = dict[Annotated[str, Field(pattern=_VARIABLE)], str]


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
        title: The piece's name in prose; the table's key, capitalised, when absent.
        vars: Values of `vars.NAME` tokens for this piece, overriding `newsletter.vars`.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    repository: str = Field(pattern=_REPOSITORY)
    tag: str | None = Field(default=None, pattern=r"^v\d+\.\d+\.\d+$")
    base: str = "main"
    url: str | None = None
    title: str = Field(min_length=1)
    vars: Variables = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _titled(cls, data: Any) -> Any:
        if isinstance(data, dict) and "title" not in data and isinstance(data.get("name"), str):
            return {**data, "title": data["name"].capitalize()}
        return data

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
        vars: Values of `vars.NAME` tokens for every piece.
        section: The sections, in the order they are applied and committed.
    """

    ref: str = "origin/main"
    staging: Path = Path(".newsletter")
    branch_prefix: str = Field(default="newsletter/", alias="branch-prefix")
    vars: Variables = Field(default_factory=dict)
    section: list[_section_union()] = Field(min_length=1)  # type: ignore[valid-type]


class Config(_Strict):
    """The whole of `london.toml`. Piece repositories and section names are each unique, and no two
    sections sent to the same piece write the same path.

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
        checks = [
            ("each piece repository appears once", [p.repository for p in self.pieces]),
            ("each section appears once", [s.name for s in self.sections]),
            *((f"each path is written by one section sent to {p.name}", self.paths(p)) for p in self.pieces),
        ]
        for rule, values in checks:
            repeated = sorted({v for v in values if values.count(v) > 1})
            if repeated:
                raise ValueError(f"{rule}; repeated: {', '.join(repeated)}")
        return self

    @model_validator(mode="after")
    def _known_pieces(self) -> Config:
        for section in self.sections:
            unknown = sorted(set(section.pieces or ()) - self.piece.keys())
            if unknown:
                raise ValueError(
                    f"section {section.name} names unknown piece {', '.join(unknown)};"
                    f" the pieces are {', '.join(self.piece)}"
                )
        return self

    @property
    def pieces(self) -> list[Piece]:
        """The pieces, in the order the file lists them."""
        return list(self.piece.values())

    @property
    def sections(self) -> list[Section]:
        """The configured sections, in the order they are applied and committed."""
        return list(self.newsletter.section)

    def sections_for(self, piece: Piece) -> list[Section]:
        """The sections sent to `piece`, in the order they are applied and committed.

        Args:
            piece: The piece.
        """
        return [s for s in self.sections if s.sends_to(piece.name)]

    def paths(self, piece: Piece | None = None) -> list[str]:
        """Every path the sections write, in section order.

        Args:
            piece: Limits the paths to those of the sections sent to this piece; every section's when `None`.
        """
        chosen = self.sections if piece is None else self.sections_for(piece)
        return [path for section in chosen for path in section.paths()]

    def context(self, piece: Piece) -> Context:
        """The values of the tokens rendered for `piece`.

        `piece.NAME` describes the piece; `set.PIECE.tag` is the release of each released piece;
        `vars.NAME` comes from `newsletter.vars` and the piece's `vars`, which take precedence.

        Args:
            piece: The piece the tokens are rendered for.
        """
        tokens = {
            "piece.name": piece.name,
            "piece.title": piece.title,
            "piece.repository": piece.repository,
            "piece.base": piece.base,
            **({"piece.tag": piece.tag} if piece.tag else {}),
            **{f"set.{p.name}.tag": p.tag for p in self.pieces if p.tag},
            **{f"vars.{k}": v for k, v in (self.newsletter.vars | piece.vars).items()},
        }
        return Context(piece.name, tokens)


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
