"""Deep merges of documents reduced to maps, arrays and scalars, steered by rules at JSON Pointers.

A value is addressed by an RFC 6901 JSON Pointer: `""` is the whole document, `/a/b` is key `b`
of key `a`, and `~1` and `~0` stand for `/` and `~` within a key. A rule names the strategy for
the value at one pointer; values without a rule take the default for their type, which gives
RFC 7386 JSON Merge Patch semantics. Merging never deletes a key from the target.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Pointer = tuple[str, ...]

MAP_STRATEGIES = {
    "merge": "Merges the source's keys into the target's, recursively, keeping keys only the target has. The default.",
    "replace": "Replaces the target's map with the source's.",
    "keep": "Keeps the target's map; the source's is used only where the target has none.",
}
ARRAY_STRATEGIES = {
    "replace": "Replaces the target's array with the source's. The default.",
    "append": "The target's items, then all of the source's.",
    "union": "The target's items, then the source's items the target lacks.",
    "keep": "Keeps the target's array; the source's is used only where the target has none.",
    "merge-by:KEY": "Merges maps sharing the value of KEY, recursively, and appends the source's others.",
}
SCALAR_STRATEGIES = {
    "replace": "Replaces the target's value with the source's. The default.",
    "keep": "Keeps the target's value; the source's is used only where the target has none.",
}
DEFAULTS = {"map": "merge", "array": "replace", "scalar": "replace"}


class MergeError(Exception):
    """A merge cannot be done as its rules say."""


def parse_pointer(text: str) -> Pointer:
    """Split an RFC 6901 JSON Pointer into its keys.

    Args:
        text: The pointer, `""` or starting with `/`.
    """
    if text == "":
        return ()
    if not text.startswith("/"):
        raise ValueError(f"{text!r} is not a JSON Pointer; a pointer is empty or starts with `/`")
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in text[1:].split("/"))


def format_pointer(pointer: Pointer) -> str:
    """Join keys into an RFC 6901 JSON Pointer.

    Args:
        pointer: The keys, outermost first.
    """
    return "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in pointer)


@dataclass(frozen=True)
class Strategy:
    """How the value at one pointer is merged.

    Attributes:
        name: The strategy's name, such as `union`.
        key: The key `merge-by` matches maps on.
    """

    name: str
    key: str | None = None

    @classmethod
    def parse(cls, text: str) -> Strategy:
        """Read a strategy as rules write it, such as `union` or `merge-by:name`.

        Args:
            text: The strategy.
        """
        name, _, key = text.partition(":")
        if name == "merge-by":
            if not key:
                raise ValueError("`merge-by` names the key it matches maps on, as in `merge-by:name`")
            return cls(name, key)
        if key or name not in {*MAP_STRATEGIES, *ARRAY_STRATEGIES, *SCALAR_STRATEGIES}:
            known = sorted({*MAP_STRATEGIES, *ARRAY_STRATEGIES, *SCALAR_STRATEGIES})
            raise ValueError(f"{text!r} is not a strategy; the strategies are {', '.join(known)}")
        return cls(name)

    def applies_to(self, kind: str) -> bool:
        """Whether the strategy can merge a value of `kind`.

        Args:
            kind: `map`, `array` or `scalar`.
        """
        names = {"map": MAP_STRATEGIES, "array": ARRAY_STRATEGIES, "scalar": SCALAR_STRATEGIES}[kind]
        return ("merge-by:KEY" if self.name == "merge-by" else self.name) in names


Rules = Mapping[Pointer, Strategy]


def kind_of(value: Any) -> str:
    """`map`, `array` or `scalar`: the shape of `value` the strategies distinguish.

    Args:
        value: Any value of a document.
    """
    if isinstance(value, Mapping):
        return "map"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "array"
    return "scalar"


def strategy_at(rules: Rules, pointer: Pointer, value: Any) -> Strategy:
    """The strategy for the source's `value` at `pointer`: its rule, or the default for its kind.

    Raises `MergeError` if the rule's strategy cannot merge a value of that kind.

    Args:
        rules: The strategies, by pointer.
        pointer: Where `value` is.
        value: The source's value.
    """
    kind = kind_of(value)
    strategy = rules.get(pointer, Strategy(DEFAULTS[kind]))
    if not strategy.applies_to(kind):
        article = "an" if kind == "array" else "a"
        where = format_pointer(pointer) or "(the document)"
        raise MergeError(f"{where} holds {article} {kind}, which `{strategy.name}` cannot merge")
    return strategy


def merge(target: Any, source: Any, rules: Rules, pointer: Pointer = ()) -> Any:
    """Merge `source` into `target` and return the result, leaving both unchanged.

    Args:
        target: The subscriber's value.
        source: The source's value.
        rules: The strategies, by pointer.
        pointer: Where both values are.
    """
    strategy = strategy_at(rules, pointer, source)
    if strategy.name == "keep":
        return copy.deepcopy(target)
    if strategy.name == "replace" or kind_of(target) != kind_of(source):
        return copy.deepcopy(source)
    if strategy.name == "merge":
        merged = dict(copy.deepcopy(target))
        for key, value in source.items():
            child = (*pointer, str(key))
            merged[key] = merge(target[key], value, rules, child) if key in target else adopt(value, rules, child)
        return merged
    if strategy.name == "append":
        return [*copy.deepcopy(target), *copy.deepcopy(source)]
    if strategy.name == "union":
        return [*copy.deepcopy(target), *(copy.deepcopy(item) for item in source if item not in target)]
    return _merge_by(target, source, strategy.key or "")


def adopt(source: Any, rules: Rules, pointer: Pointer = ()) -> Any:
    """A copy of `source` for a target that has no value at `pointer`, checked against the rules.

    Args:
        source: The source's value.
        rules: The strategies, by pointer.
        pointer: Where the value is.
    """
    strategy_at(rules, pointer, source)
    if kind_of(source) == "map":
        return {key: adopt(value, rules, (*pointer, str(key))) for key, value in source.items()}
    return copy.deepcopy(source)


def _merge_by(target: Sequence[Any], source: Sequence[Any], key: str) -> list[Any]:
    """The `merge-by` strategy: `target`'s items, with each map of `source` merged into the map of
    `target` that holds the same value at `key`, and the source's other items appended.

    Items merge with the default strategies, as rules cannot address array items.

    Args:
        target: The subscriber's array.
        source: The source's array.
        key: The key whose value matches a source map to a target map.
    """
    merged = list(copy.deepcopy(target))
    for item in source:
        match = next(
            (i for i, mine in enumerate(merged) if isinstance(mine, Mapping) and isinstance(item, Mapping)
             and key in mine and key in item and mine[key] == item[key]),
            None,
        )  # fmt: skip
        if match is None:
            merged.append(copy.deepcopy(item))
        else:
            merged[match] = merge(merged[match], item, {}, ())
    return merged


_MISSING = object()


def get(document: Any, pointer: Pointer) -> Any:
    """The value at `pointer`, raising `MergeError` if the document has none.

    Args:
        document: The document.
        pointer: Where the value is.
    """
    value = document
    for depth, key in enumerate(pointer):
        if not isinstance(value, Mapping) or key not in value:
            raise MergeError(f"{format_pointer(pointer[: depth + 1])} is not in the document")
        value = value[key]
    return value


def sync(target: Any, source: Any, pointer: Pointer, rules: Rules) -> Any:
    """Return `target` with the source's value at `pointer` merged into it.

    Maps the target lacks on the way to `pointer` are created. Raises `MergeError` if the source has
    no value at `pointer`, or if the target holds something other than a map on the way to it.

    Args:
        target: The subscriber's document.
        source: The source's document.
        pointer: The value sent.
        rules: The strategies, by pointer.
    """
    value = get(source, pointer)
    return _place(target, pointer, 0, value, rules)


def _place(node: Any, pointer: Pointer, depth: int, value: Any, rules: Rules) -> Any:
    """`node` with `value` merged in at `pointer[depth:]`, creating the maps on the way.

    Args:
        node: The target's value at `pointer[:depth]`, or `_MISSING` if it has none.
        pointer: Where the value goes in the whole document.
        depth: How many keys of `pointer` lead to `node`.
        value: The source's value at `pointer`.
        rules: The strategies, by pointer.
    """
    if depth == len(pointer):
        return adopt(value, rules, pointer) if node is _MISSING else merge(node, value, rules, pointer)
    if node is _MISSING:
        node = {}
    if not isinstance(node, Mapping):
        raise MergeError(f"{format_pointer(pointer[:depth]) or '(the document)'} is not a map in the target")
    placed = dict(node)
    key = pointer[depth]
    placed[key] = _place(node.get(key, _MISSING), pointer, depth + 1, value, rules)
    return placed
