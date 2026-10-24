"""The `file` section kind: a whole file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .base import Applied, Section, Source, register, safe_path


@register
class FileSection(Section):
    """Copies a whole file from the source to each subscriber, replacing the subscriber's copy."""

    kind: Literal["file"] = "file"
    path: str = Field(description="The file in the source.")
    target: str | None = Field(default=None, description="The file in the subscriber; `path` when absent.")

    @field_validator("path", "target")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        return None if value is None else safe_path(value)

    def paths(self) -> tuple[str, ...]:
        return (self.target or self.path,)

    async def apply(self, source: Source, clone: Path) -> Applied:
        target = clone / (self.target or self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(await source.read(self.path), encoding="utf-8")
        return Applied()
