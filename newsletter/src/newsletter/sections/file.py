"""The `file` section kind: a whole file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .base import Applied, Context, Section, Source, decode, register, safe_path


@register
class FileSection(Section):
    """Copies a whole file from the source to each subscriber, replacing the subscriber's copy.

    The file is copied byte for byte unless the section renders, when it must be UTF-8 text.
    """

    kind: Literal["file"] = "file"
    path: str = Field(description="The file in the source.")
    target: str | None = Field(default=None, description="The file in the subscriber; `path` when absent.")

    @field_validator("path", "target")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        return None if value is None else safe_path(value)

    def paths(self) -> tuple[str, ...]:
        return (self.target or self.path,)

    async def _contents(self, source: Source, context: Context) -> bytes:
        """The file's contents as sent to the subscriber `context` names.

        Args:
            source: The source commit.
            context: The subscriber and its tokens.
        """
        data = await source.read(self.path)
        if not self.render:
            return data
        where = f"{self.name}: {source.sha[:12]}:{self.path}"
        return self.rendered(decode(data, where), context, where).encode("utf-8")

    async def check(self, source: Source, context: Context) -> None:
        await self._contents(source, context)

    async def apply(self, source: Source, clone: Path, context: Context) -> Applied:
        target = clone / (self.target or self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await self._contents(source, context))
        return Applied()
