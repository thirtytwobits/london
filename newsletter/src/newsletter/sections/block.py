"""The `block` section kind: a marked block of a text file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .base import Applied, Section, SectionError, Source, decode, register, safe_path


@dataclass(frozen=True)
class Span:
    """The lines of a block, from its begin marker to its end marker inclusive.

    Attributes:
        start: The index of the begin marker's line.
        stop: The index after the end marker's line.
    """

    start: int
    stop: int


def find_block(lines: list[str], block: str) -> Span | None:
    """Locate `block` in `lines`. A file holds each block once, opened and closed in order.

    Args:
        lines: The file's lines.
        block: The NAME in the block's markers.
    """
    begin = re.compile(rf"^<!--\s*newsletter:begin\s+{re.escape(block)}(\s.*)?-->\s*$")
    end = re.compile(rf"^<!--\s*newsletter:end\s+{re.escape(block)}\s*-->\s*$")
    begins = [i for i, line in enumerate(lines) if begin.match(line)]
    ends = [i for i, line in enumerate(lines) if end.match(line)]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise SectionError(f"block {block!r} needs one begin marker followed by one end marker")
    return Span(begins[0], ends[0] + 1)


def extract(source: str, block: str) -> str:
    """The text of `block` in `source`, markers included, ending with a line ending.

    Args:
        source: The source file's text.
        block: The NAME in the block's markers.
    """
    lines = source.splitlines(keepends=True)
    span = find_block(lines, block)
    if span is None:
        raise SectionError(f"the source holds no block {block!r}")
    text = "".join(lines[span.start : span.stop])
    return text if text.endswith("\n") else text + "\n"


def splice(source: str, target: str | None, block: str) -> tuple[str, bool]:
    """Return `target` with `block` replaced by the source's copy, and whether it was appended.

    A target without the block, or without the file, receives the block at its end.

    Args:
        source: The source file's text.
        target: The subscriber file's text, or `None` if the file does not exist.
        block: The NAME in the block's markers.
    """
    text = extract(source, block)
    if target is None:
        return text, True
    target_lines = target.splitlines(keepends=True)
    found = find_block(target_lines, block)
    if found is None:
        head = target.rstrip("\n")
        return (head + "\n\n" if head else "") + text, True
    return "".join(target_lines[: found.start]) + text + "".join(target_lines[found.stop :]), False


@register
class BlockSection(Section):
    """Copies a marked block of a text file into the same file in each subscriber.

    The block runs from `<!-- newsletter:begin NAME -->` to `<!-- newsletter:end NAME -->`,
    each on a line of its own; text after NAME in the begin marker is free. The subscriber's
    copy of the block is replaced, and a file without the block receives it at its end.
    Everything outside the markers belongs to the subscriber.
    """

    kind: Literal["block"] = "block"
    path: str = Field(description="The file in both the source and the subscriber.")
    block: str = Field(pattern=r"^[a-z][a-z0-9-]*$", description="The NAME in the block's markers.")

    @field_validator("path")
    @classmethod
    def _plain(cls, value: str) -> str:
        return safe_path(value)

    def paths(self) -> tuple[str, ...]:
        return (self.path,)

    async def check(self, source: Source) -> None:
        where = f"{source.sha[:12]}:{self.path}"
        try:
            extract(decode(await source.read(self.path), where), self.block)
        except SectionError as error:
            raise SectionError(f"{self.name}: {where}: {error}") from None

    async def apply(self, source: Source, clone: Path) -> Applied:
        target = clone / self.path
        current = decode(target.read_bytes(), self.path) if target.exists() else None
        source_text = decode(await source.read(self.path), f"{source.sha[:12]}:{self.path}")
        text, appended = splice(source_text, current, self.block)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        applied = Applied()
        if appended and current is not None:
            applied.notes.append(
                f"`{self.path}` held no `{self.block}` block, so the block was appended."
                " Remove any text above it that the block repeats."
            )
        return applied
