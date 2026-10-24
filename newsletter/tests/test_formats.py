"""Structured formats: reading documents and writing values back with the original's layout."""

import pytest

from newsletter.structured import formats

SETTINGS = """{
    // Comments explain the settings.
    "rust-analyzer.cargo.targetDir": true,
    "cSpell.language": "en-GB",
    "cSpell.words": [
        "piece"
    ]
}"""


def test_formats_are_chosen_by_suffix():
    assert formats.for_path(".vscode/settings.json").name == "json"
    assert formats.for_path("project.code-workspace").name == "json"
    assert formats.for_path("pyproject.toml").name == "toml"
    assert formats.for_path(".github/dependabot.yml").name == "yaml"
    assert formats.for_path("README.md") is None


def test_json_reads_comments_and_trailing_commas():
    assert formats.named("json").load('{\n  // note\n  "a": [1, 2,],\n}') == {"a": [1, 2]}


def test_json_unchanged_is_returned_byte_for_byte():
    json = formats.named("json")
    assert json.update(SETTINGS, json.load(SETTINGS)) == SETTINGS


def test_json_changes_a_member_in_place_and_keeps_comments_elsewhere():
    json = formats.named("json")
    value = json.load(SETTINGS) | {"cSpell.words": ["piece", "london"]}
    assert json.update(SETTINGS, value) == SETTINGS.replace(
        '[\n        "piece"\n    ]', '[\n        "piece",\n        "london"\n    ]'
    )


def test_json_adds_members_at_the_end_with_the_files_indentation():
    json = formats.named("json")
    value = json.load(SETTINGS) | {"cSpell.allowCompoundWords": True, "files.exclude": {"x": True}}
    updated = json.update(SETTINGS, value)
    assert updated.startswith(SETTINGS[: SETTINGS.rindex("\n}")])
    assert updated.endswith(
        '    ],\n    "cSpell.allowCompoundWords": true,\n    "files.exclude": {\n        "x": true\n    }\n}'
    )
    assert json.load(updated) == value


def test_json_keeps_a_trailing_comma_after_added_members():
    json = formats.named("json")
    text = '{\n  "a": 1,\n}\n'
    assert json.update(text, {"a": 1, "b": 2}) == '{\n  "a": 1,\n  "b": 2,\n}\n'


def test_json_writes_a_new_document():
    assert formats.named("json").update(None, {"a": [1]}) == '{\n    "a": [\n        1\n    ]\n}\n'


def test_toml_keeps_comments_and_layout_where_values_are_unchanged():
    toml = formats.named("toml")
    text = '# The tool.\n[tool]\nname = "x"  # its name\nlist = [1]\n\n[other]\nkeep = true\n'
    updated = toml.update(text, toml.load(text) | {"tool": {"name": "x", "list": [1, 2]}})
    assert updated == '# The tool.\n[tool]\nname = "x"  # its name\nlist = [1, 2]\n\n[other]\nkeep = true\n'


def test_yaml_keeps_comments_where_values_are_unchanged():
    yaml = formats.named("yaml")
    text = "# Updates.\nversion: 2\nupdates:\n  - ecosystem: cargo  # Rust\n"
    updated = yaml.update(text, yaml.load(text) | {"version": 3})
    assert updated == text.replace("version: 2", "version: 3")


@pytest.mark.parametrize(("name", "text"), [("json", "{"), ("toml", "a = "), ("yaml", "a: [")])
def test_a_malformed_document_raises_format_error(name, text):
    with pytest.raises(formats.FormatError):
        formats.named(name).load(text)


def test_yaml_keeps_quotes_and_nested_indentation():
    yaml = formats.named("yaml")
    text = (
        'version: 2\nupdates:\n  - package-ecosystem: "github-actions"\n    directory: "/"\n'
        '    schedule:\n      interval: "weekly"\n    groups:\n      actions:\n        patterns:\n          - "*"\n'
    )
    assert yaml.update(text, yaml.load(text) | {"version": 3}) == text.replace("version: 2", "version: 3")
