"""Deep merges steered by strategies at JSON Pointers."""

import pytest

from newsletter.structured.merge import MergeError, Strategy, format_pointer, merge, parse_pointer, sync


def rules(**by_pointer: str) -> dict:
    """Strategies by pointer, written with `_` for `/` in keyword names.

    Args:
        **by_pointer: Strategies by pointer.
    """
    return {parse_pointer(p.replace("_", "/")): Strategy.parse(s) for p, s in by_pointer.items()}


def test_pointers_follow_rfc_6901():
    assert not parse_pointer("")
    assert parse_pointer("/cSpell.words") == ("cSpell.words",)
    assert parse_pointer("/a~1b/c~0d") == ("a/b", "c~d")
    assert format_pointer(("a/b", "c~d")) == "/a~1b/c~0d"
    with pytest.raises(ValueError):
        parse_pointer("cSpell.words")


def test_by_default_maps_merge_recursively_and_keep_the_targets_own_keys():
    target = {"a": {"x": 1, "mine": True}, "own": 1}
    source = {"a": {"x": 2, "y": 3}, "new": [1]}
    assert merge(target, source, {}) == {"a": {"x": 2, "mine": True, "y": 3}, "own": 1, "new": [1]}


def test_by_default_arrays_and_scalars_are_replaced():
    assert merge({"l": [1, 2], "s": "old"}, {"l": [3], "s": "new"}, {}) == {"l": [3], "s": "new"}


def test_a_value_whose_type_differs_is_replaced():
    assert merge({"a": {"x": 1}}, {"a": [1]}, {}) == {"a": [1]}


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("replace", ["b", "c"]),
        ("append", ["a", "b", "b", "c"]),
        ("union", ["a", "b", "c"]),
        ("keep", ["a", "b"]),
    ],
)
def test_array_strategies(strategy, expected):
    assert merge({"l": ["a", "b"]}, {"l": ["b", "c"]}, rules(_l=strategy)) == {"l": expected}


def test_merge_by_merges_maps_sharing_a_key_and_appends_the_rest():
    target = [{"name": "x", "keep": 1, "v": 1}, {"name": "mine"}]
    source = [{"name": "x", "v": 2}, {"name": "new"}]
    assert merge({"l": target}, {"l": source}, rules(_l="merge-by:name")) == {
        "l": [{"name": "x", "keep": 1, "v": 2}, {"name": "mine"}, {"name": "new"}]
    }


def test_map_strategies():
    target, source = {"m": {"a": 1, "b": 1}}, {"m": {"b": 2, "c": 2}}
    assert merge(target, source, rules(_m="replace")) == {"m": {"b": 2, "c": 2}}
    assert merge(target, source, rules(_m="keep")) == {"m": {"a": 1, "b": 1}}


def test_keep_seeds_a_value_the_target_lacks():
    assert merge({}, {"m": {"a": 1}}, rules(_m="keep")) == {"m": {"a": 1}}


def test_a_rule_applies_at_its_pointer_inside_merged_maps():
    target = {"outer": {"l": [1], "other": [1]}}
    source = {"outer": {"l": [2], "other": [2]}}
    assert merge(target, source, rules(_outer_l="union")) == {"outer": {"l": [1, 2], "other": [2]}}


def test_a_strategy_that_cannot_merge_the_value_is_refused():
    with pytest.raises(MergeError, match="/m holds a map"):
        merge({"m": {}}, {"m": {"a": 1}}, rules(_m="union"))
    with pytest.raises(MergeError, match="/l holds an array"):
        sync({}, {"l": [1]}, ("l",), rules(_l="merge"))


def test_merging_changes_neither_document():
    target, source = {"m": {"l": [1]}}, {"m": {"l": [2], "n": {"x": 1}}}
    merged = merge(target, source, rules(_m_l="append"))
    merged["m"]["l"].append(3)
    merged["m"]["n"]["x"] = 9
    assert target == {"m": {"l": [1]}} and source == {"m": {"l": [2], "n": {"x": 1}}}


def test_sync_merges_only_the_selected_value_and_creates_maps_on_the_way():
    source = {"a": {"b": {"c": 1}}, "unselected": 1}
    assert sync({"own": 1}, source, ("a", "b"), {}) == {"own": 1, "a": {"b": {"c": 1}}}


def test_sync_refuses_a_value_the_source_lacks_and_a_target_that_is_not_a_map_on_the_way():
    with pytest.raises(MergeError, match="/missing is not in the document"):
        sync({}, {}, ("missing",), {})
    with pytest.raises(MergeError, match="/a is not a map in the target"):
        sync({"a": [1]}, {"a": {"b": 1}}, ("a", "b"), {})


def test_strategies_are_read_as_rules_write_them():
    assert Strategy.parse("merge-by:name") == Strategy("merge-by", "name")
    with pytest.raises(ValueError, match="merge-by"):
        Strategy.parse("merge-by")
    with pytest.raises(ValueError, match="not a strategy"):
        Strategy.parse("splice")
