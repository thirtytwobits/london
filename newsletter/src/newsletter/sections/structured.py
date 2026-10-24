"""The `structured` section kind: chosen values of a structured document, deep-merged."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ..structured import formats
from ..structured.merge import (
    ARRAY_STRATEGIES,
    MAP_STRATEGIES,
    MISSING,
    SCALAR_STRATEGIES,
    MergeError,
    Pointer,
    Strategy,
    format_pointer,
    parse_pointer,
    sync,
)
from .base import Applied, Section, SectionError, Source, decode, register, safe_path


@register
class StructuredSection(Section):
    """Deep-merges chosen values of a JSON, JSONC, TOML or YAML document into each subscriber's copy.

    Values are addressed by JSON Pointers (RFC 6901): `""` is the whole document, `/a/b` is key `b`
    of key `a`, and `~1` and `~0` stand for `/` and `~` in a key, so `/cSpell.words` is the key
    `cSpell.words`. Each value in `select` is merged into the subscriber's document, creating the
    maps on the way to it; everything else in the subscriber's document is kept, and nothing is
    deleted from it. The subscriber's comments and formatting are kept wherever values are unchanged.

    A rule names how the value at its pointer is merged. Without one, maps merge and arrays and
    other values are replaced, as in JSON Merge Patch (RFC 7386); a value whose type differs between
    the documents is replaced. Rules address values reached through maps; the items of a
    `merge-by` array merge with the defaults.
    """

    kind: Literal["structured"] = "structured"
    path: str = Field(description="The document in both the source and the subscriber.")
    format: str | None = Field(
        default=None,
        description=f"The document's format: {', '.join(formats.names())}; chosen by the path's suffix when absent.",
    )
    select: list[str] = Field(
        default=[""], min_length=1, description="JSON Pointers to the values sent; the whole document when absent."
    )
    rules: dict[str, str] = Field(
        default_factory=dict, description="Strategies by JSON Pointer, from the tables below; none when absent."
    )

    @field_validator("path")
    @classmethod
    def _plain(cls, value: str) -> str:
        """Refuse a `path` that is absolute or leaves the repository.

        Args:
            value: The configured path.
        """
        return safe_path(value)

    @field_validator("select")
    @classmethod
    def _pointers(cls, value: list[str]) -> list[str]:
        """Refuse a `select` entry that is not a JSON Pointer.

        Args:
            value: The configured pointers.
        """
        for pointer in value:
            parse_pointer(pointer)
        return value

    @field_validator("rules")
    @classmethod
    def _rules(cls, value: dict[str, str]) -> dict[str, str]:
        """Refuse a rule whose key is not a JSON Pointer or whose value is not a strategy.

        Args:
            value: The configured rules.
        """
        for pointer, strategy in value.items():
            parse_pointer(pointer)
            Strategy.parse(strategy)
        return value

    @model_validator(mode="after")
    def _format_known(self) -> StructuredSection:
        """Refuse a `format` that names no format, and a path whose suffix chooses none when `format` is absent."""
        if self.format is not None and self.format not in formats.names():
            raise ValueError(f"{self.format!r} is not a format; the formats are {', '.join(formats.names())}")
        if self.format is None and formats.for_path(self.path) is None:
            raise ValueError(f"no format is chosen by the suffix of {self.path!r}; name one with `format`")
        return self

    def paths(self) -> tuple[str, ...]:
        """The section's document, the only file it writes."""
        return (self.path,)

    @classmethod
    def reference(cls) -> dict[str, dict[str, str]]:
        """The strategies for maps, arrays and other values, which `rules` names."""
        return {
            "strategies for maps": MAP_STRATEGIES,
            "strategies for arrays": ARRAY_STRATEGIES,
            "strategies for other values": SCALAR_STRATEGIES,
        }

    def _format(self) -> formats.Format:
        """The configured format, or the one the path's suffix chooses."""
        found = formats.named(self.format) if self.format else formats.for_path(self.path)
        assert found is not None
        return found

    def _rule_map(self) -> dict[Pointer, Strategy]:
        """The rules with their pointers split and their strategies parsed."""
        return {parse_pointer(p): Strategy.parse(s) for p, s in self.rules.items()}

    async def _source_document(self, source: Source) -> Any:
        """The source's document as plain values, raising `SectionError` if it cannot be read.

        Args:
            source: The source commit.
        """
        where = f"{source.sha[:12]}:{self.path}"
        try:
            return self._format().load(decode(await source.read(self.path), where))
        except formats.FormatError as error:
            raise SectionError(f"{self.name}: {where}: {error}") from None

    async def check(self, source: Source) -> None:
        """Raise `SectionError` unless the source holds every selected value in a shape its rule can merge.

        Args:
            source: The source commit.
        """
        document = await self._source_document(source)
        try:
            for pointer in self.select:
                sync({}, document, parse_pointer(pointer), self._rule_map())
        except MergeError as error:
            raise SectionError(f"{self.name}: {source.sha[:12]}:{self.path}: {error}") from None

    async def apply(self, source: Source, clone: Path) -> Applied:
        """Merge the selected values into the subscriber's document, creating it if it is absent.

        The file is written only if its text changes.

        Args:
            source: The source commit.
            clone: The subscriber's clone.
        """
        document = await self._source_document(source)
        target = clone / self.path
        text = decode(target.read_bytes(), self.path) if target.exists() else None
        fmt = self._format()
        try:
            merged = fmt.load(text) if text is not None else MISSING
        except formats.FormatError as error:
            raise SectionError(f"{self.name}: {self.path}: {error}") from None
        try:
            for pointer in self.select:
                merged = sync(merged, document, parse_pointer(pointer), self._rule_map())
        except MergeError as error:
            raise SectionError(f"{self.name}: {self.path}: {error}") from None
        updated = fmt.update(text, merged)
        if updated != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(updated.encode("utf-8"))
        return Applied()

    def body(self, repository: str, sha: str) -> str:
        """The commit body: the document merged from, and the values selected.

        Args:
            repository: The source repository as `owner/name`.
            sha: The full hash of the source commit.
        """
        values = ", ".join(f"`{format_pointer(parse_pointer(p)) or '(the document)'}`" for p in self.select)
        return f"Merged from {repository}@{sha[:12]}:{self.path}: {values}."
