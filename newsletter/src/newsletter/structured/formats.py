"""Document formats reduced to maps, arrays and scalars, written back with the original's layout.

A format reads text to plain Python values and writes new values into the original text, changing
only what differs: comments, ordering and formatting elsewhere in the file are kept. A format
registers with `@register` and is chosen by a file's suffix.
"""

from __future__ import annotations

import functools
import io
import json
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, ClassVar, Protocol

import json5
import tomlkit
from tomlkit.exceptions import ParseError
from json5.loader import ModelLoader
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.util import load_yaml_guess_indent


class Format(Protocol):
    """Reads and writes one kind of document.

    Attributes:
        name: The format's name, as a section's `format` key gives it.
        suffixes: The file suffixes the format is chosen for.
    """

    name: ClassVar[str]
    suffixes: ClassVar[tuple[str, ...]]

    def load(self, text: str) -> Any:
        """The document's value as plain maps, arrays and scalars, raising `FormatError` if it is malformed.

        Args:
            text: The document.
        """
        ...

    def update(self, text: str | None, value: Any) -> str:
        """`text` with its value replaced by `value`, keeping everything that is unchanged.

        Args:
            text: The document, or `None` to write a new one.
            value: The document's new value.
        """
        ...


class FormatError(Exception):
    """A document cannot be read in its format."""


_FORMATS: dict[str, Format] = {}


def register[F: type[Format]](cls: F) -> F:
    """Make a format available to sections.

    Args:
        cls: The format class, constructed with no arguments.
    """
    _FORMATS[cls.name] = cls()
    return cls


def named(name: str) -> Format:
    """The format called `name`, raising `KeyError` if there is none.

    Args:
        name: The format's name.
    """
    return _FORMATS[name]


def names() -> list[str]:
    """The names of every registered format."""
    return sorted(_FORMATS)


def for_path(path: str) -> Format | None:
    """The format chosen for `path` by its suffix, or `None`.

    Args:
        path: A file's path.
    """
    return next((f for f in _FORMATS.values() if path.endswith(f.suffixes)), None)


def assign(container: MutableMapping[str, Any], old: Mapping[str, Any], new: Mapping[str, Any]) -> None:
    """Make `container` hold `new`, writing only the keys that differ from `old` and descending into maps.

    Args:
        container: The document's map, which keeps its formatting where its values are unchanged.
        old: The map's value before the merge.
        new: The map's value after it.
    """
    for key in [key for key in old if key not in new]:
        del container[key]
    for key, value in new.items():
        if key in old and old[key] == value:
            continue
        mine = container.get(key)
        if isinstance(mine, MutableMapping) and isinstance(value, Mapping) and isinstance(old.get(key), Mapping):
            assign(mine, old[key], value)
        else:
            container[key] = value


@register
class Json:
    """JSON, and JSON with comments and trailing commas as VS Code reads them.

    A changed or added top-level member is written as JSON in place; everything else in the file is
    kept as it was, comments included.
    """

    name: ClassVar[str] = "json"
    suffixes: ClassVar[tuple[str, ...]] = (".json", ".jsonc", ".json5", ".code-workspace")

    def load(self, text: str) -> Any:
        """The document's value, raising `FormatError` if it is not JSON, JSONC or JSON5.

        Args:
            text: The document.
        """
        try:
            return json5.loads(text)
        except ValueError as error:
            raise FormatError(f"not JSON: {error}") from None

    def update(self, text: str | None, value: Any) -> str:
        """`text` with changed, added and removed top-level members written in place.

        Args:
            text: The document, or `None` to write a new one.
            value: The document's new value.
        """
        if text is None:
            return _dump(value, "", "    ") + "\n"
        old = self.load(text)
        if old == value:
            return text
        root = json5.loads(text, loader=ModelLoader()).value
        starts = _line_starts(text)
        pairs = getattr(root, "key_value_pairs", None)
        if not (isinstance(old, dict) and isinstance(value, dict) and pairs and old.keys() & value.keys()):
            start, end = _span(starts, root)
            return text[:start] + _dump(value, "", "    ") + text[end:]
        edits = _member_edits(text, starts, root, old, value)
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        return text


def _member_edits(
    text: str, starts: list[int], root: Any, old: dict[str, Any], new: dict[str, Any]
) -> list[tuple[int, int, str]]:
    """The text replacements that turn an object's members from `old` into `new`.

    A changed member's value is replaced where it stands; added members follow the last member, in
    the indentation of the first. An object written on one line keeps its members on that line. A
    removed member goes with the comma and anything else before it, back to the member it follows;
    members removed from the start go with everything from the opening brace to the comma after
    the last of them.

    Args:
        text: The document.
        starts: The offset of each line of `text`, from `_line_starts`.
        root: The object in json-five's model, holding at least one member of `new`.
        old: The object's value before the merge.
        new: The object's value after it.
    """
    pairs = root.key_value_pairs
    separator, dump = _layout(text, _span(starts, pairs[0].key)[0])
    keys = [_key(text[slice(*_span(starts, pair.key))]) for pair in pairs]
    edits = _removals(text, starts, root, keys, new)
    for key, pair in zip(keys, pairs):
        if key in new and new[key] != old.get(key):
            edits.append((*_span(starts, pair.value), dump(new[key])))
    added = [key for key in new if key not in old]
    if added:
        end = _span(starts, pairs[-1].value)[1]
        members = (f"{separator}{json.dumps(k, ensure_ascii=False)}: {dump(new[k])}" for k in added)
        edits.append((end, end, "".join(members)))
    return edits


def _layout(text: str, first: int) -> tuple[str, Callable[[Any], str]]:
    """What goes before an added member, and how a member's value is written, to match an object's layout.

    An object whose first member shares its line with something else is written on one line; any
    other is written a member to a line, in the first member's indentation.

    Args:
        text: The document.
        first: The offset of the object's first key.
    """
    indent = text[text.rfind("\n", 0, first) + 1 : first]
    if indent.strip():
        return ", ", _dump_inline
    return ",\n" + indent, functools.partial(_dump, indent=indent, unit=indent or "    ")


def _removals(
    text: str, starts: list[int], root: Any, keys: list[str], new: dict[str, Any]
) -> list[tuple[int, int, str]]:
    """The text deletions that remove the members of an object that `new` lacks.

    Args:
        text: The document.
        starts: The offset of each line of `text`, from `_line_starts`.
        root: The object in json-five's model, holding at least one member of `new`.
        keys: The name of each of the object's members.
        new: The object's value after the merge.
    """
    pairs = root.key_value_pairs
    kept = next(i for i, key in enumerate(keys) if key in new)
    edits = []
    if kept:
        comma = text.index(",", _span(starts, pairs[kept - 1].value)[1]) + 1
        edits.append((_span(starts, root)[0] + 1, comma, ""))
    for i in range(kept + 1, len(pairs)):
        if keys[i] not in new:
            edits.append((_span(starts, pairs[i - 1].value)[1], _span(starts, pairs[i].value)[1], ""))
    return edits


def _line_starts(text: str) -> list[int]:
    """The offset at which each line of `text` starts, and the length of `text` last.

    Args:
        text: The document.
    """
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _span(starts: list[int], node: Any) -> tuple[int, int]:
    """The offsets in the text where a node of json-five's model starts and ends.

    Args:
        starts: The offset of each line of the text, from `_line_starts`.
        node: A node, which records its one-based lines and zero-based columns.
    """
    return starts[node.lineno - 1] + node.col_offset, starts[node.end_lineno - 1] + node.end_col_offset


def _key(text: str) -> str:
    """The name a member's key gives, from the key's text: quoted, or an unquoted JSON5 identifier.

    Args:
        text: The key as the document writes it.
    """
    return str(json5.loads(text)) if text[:1] in "\"'" else text


def _dump(value: Any, indent: str, unit: str) -> str:
    """`value` as indented JSON whose lines after the first begin with `indent`, to sit at that depth.

    Args:
        value: The value written.
        indent: The indentation of the line the value starts on.
        unit: One level of indentation.
    """
    return json.dumps(value, indent=unit, ensure_ascii=False).replace("\n", "\n" + indent)


def _dump_inline(value: Any) -> str:
    """`value` as JSON on one line.

    Args:
        value: The value written.
    """
    return json.dumps(value, ensure_ascii=False)


@register
class Toml:
    """TOML. Comments, ordering and formatting are kept wherever values are unchanged."""

    name: ClassVar[str] = "toml"
    suffixes: ClassVar[tuple[str, ...]] = (".toml",)

    def load(self, text: str) -> Any:
        """The document's value, raising `FormatError` if it is not TOML.

        Args:
            text: The document.
        """
        try:
            return tomlkit.parse(text).unwrap()
        except ParseError as error:
            raise FormatError(f"not TOML: {error}") from None

    def update(self, text: str | None, value: Any) -> str:
        """`text` with the changed values written in place.

        Args:
            text: The document, or `None` to write a new one.
            value: The document's new value, a table.
        """
        document = tomlkit.parse(text or "")
        if not isinstance(value, Mapping):
            raise ValueError("a TOML document is a table")
        assign(document, document.unwrap(), value)
        return tomlkit.dumps(document)


@register
class Yaml:
    """YAML, one document per file. Comments and ordering are kept wherever values are unchanged."""

    name: ClassVar[str] = "yaml"
    suffixes: ClassVar[tuple[str, ...]] = (".yaml", ".yml")

    def load(self, text: str) -> Any:
        """The document's value, raising `FormatError` if it is not YAML.

        Args:
            text: The document.
        """
        try:
            return _plain(YAML(typ="rt").load(text))
        except YAMLError as error:
            raise FormatError(f"not YAML: {error}") from None

    def update(self, text: str | None, value: Any) -> str:
        """`text` with the changed values written in, keeping its comments, quotes and indentation.

        Args:
            text: The document, or `None` to write a new one.
            value: The document's new value.
        """
        if text is not None and self.load(text) == value:
            return text
        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        document = None
        if text:
            _, sequence, offset = load_yaml_guess_indent(text)
            yaml.indent(mapping=_mapping_indent(text), sequence=sequence, offset=offset)
            document = yaml.load(text)
        if isinstance(document, MutableMapping) and isinstance(value, Mapping):
            assign(document, _plain(document), value)
        else:
            document = value
        out = io.StringIO()
        yaml.dump(document, out)
        return out.getvalue()


def _mapping_indent(text: str) -> int:
    """The indentation of maps nested in maps, measured from the first such pair of lines; 2 if none.

    ruamel's own guess measures sequences, so maps are measured here.

    Args:
        text: The YAML document.
    """
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    for parent, child in zip(lines, lines[1:]):
        body = parent.lstrip()
        depth = len(parent) - len(body)
        nested = len(child) - len(child.lstrip())
        if not body.startswith("- ") and body.rstrip().endswith(":") and nested > depth:
            if not child.lstrip().startswith("- "):
                return nested - depth
    return 2


def _plain(value: Any) -> Any:
    """`value` with ruamel's round-trip maps and sequences turned into plain dicts and lists.

    Args:
        value: A value ruamel loaded.
    """
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value
