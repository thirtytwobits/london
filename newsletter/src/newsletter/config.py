"""`newsletter.toml`: the source, the subscribers and the sections."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .sections import Section, kinds

CONFIG_NAME = "newsletter.toml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceConfig(_Strict):
    """`[source]`: the London Project repository as `owner/name`, and the ref an edition is sent from."""

    repository: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    ref: str = "origin/main"


class StagingConfig(_Strict):
    """`[staging]`: the staging area's path, relative to the checkout's root."""

    path: Path = Path(".newsletter")


class BranchConfig(_Strict):
    """`[branch]`: the prefix of every edition branch."""

    prefix: str = "newsletter/"


class Subscriber(_Strict):
    """`[[subscriber]]`: a repository that receives the newsletter, the base branch its pull requests
    target, and the URL it is cloned from when that is not GitHub.
    """

    repository: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    base: str = "main"
    url: str | None = None

    @property
    def name(self) -> str:
        """The repository's name without its owner."""
        return self.repository.split("/", 1)[1]

    @property
    def remote(self) -> str:
        """The URL the subscriber is cloned from and pushed to."""
        return self.url or f"https://github.com/{self.repository}.git"


def _section_union() -> object:
    members = tuple(kinds().values())
    return Annotated[Union[members], Field(discriminator="kind")]  # noqa: UP007


class Config(_Strict):
    """The whole of `newsletter.toml`. Subscriber names, section names and section paths are each unique."""

    source: SourceConfig
    staging: StagingConfig = StagingConfig()
    branch: BranchConfig = BranchConfig()
    subscriber: list[Subscriber] = Field(min_length=1)
    section: list[_section_union()] = Field(min_length=1)  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _distinct(self) -> Config:
        for label, values in (
            ("subscriber", [s.name for s in self.subscriber]),
            ("section", [s.name for s in self.section]),
            ("section path", [p for s in self.section for p in s.paths()]),
        ):
            repeated = sorted({v for v in values if values.count(v) > 1})
            if repeated:
                raise ValueError(f"each {label} appears once; repeated: {', '.join(repeated)}")
        return self

    @property
    def sections(self) -> list[Section]:
        """The configured sections, in the order they are applied and committed."""
        return list(self.section)

    def paths(self) -> list[str]:
        """Every path the sections write, in section order."""
        return [path for section in self.section for path in section.paths()]


class ConfigError(Exception):
    """`newsletter.toml` is missing, malformed or invalid."""


def load(path: Path) -> Config:
    """Read and validate the configuration at `path`, raising `ConfigError` with every problem found.

    Args:
        path: The `newsletter.toml` to read.
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
