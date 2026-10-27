"""Loading and validating `london.toml`."""

from pathlib import Path

import pytest

from newsletter.config import ConfigError, load

BASE = """
[london]
repository = "owner/london"

[piece.released]
repository = "owner/released"
tag = "v1.2.3"

[piece.unreleased]
repository = "owner/unreleased"

[newsletter]
"""

BLOCK = """
[[newsletter.section]]
name = "{name}"
kind = "block"
subject = "Update the block"
path = "{path}"
block = "shared"
"""


def write(tmp_path: Path, text: str) -> Path:
    """Write `text` as `london.toml` in `tmp_path`.

    Args:
        tmp_path: The test's temporary directory.
        text: The configuration.
    """
    path = tmp_path / "london.toml"
    path.write_text(text)
    return path


def test_defaults_fill_what_the_file_omits(tmp_path):
    config = load(write(tmp_path, BASE + BLOCK.format(name="agents", path="AGENTS.md")))
    assert config.newsletter.ref == "origin/main"
    assert config.newsletter.staging == Path(".newsletter")
    assert config.newsletter.branch_prefix == "newsletter/"
    assert config.pieces[0].remote == "https://github.com/owner/released.git"
    assert config.pieces[0].base == "main"
    assert config.paths() == ["AGENTS.md"]


def test_pieces_are_named_by_their_tables_in_file_order(tmp_path):
    config = load(write(tmp_path, BASE + BLOCK.format(name="agents", path="AGENTS.md")))
    assert [(p.name, p.repository, p.tag) for p in config.pieces] == [
        ("released", "owner/released", "v1.2.3"),
        ("unreleased", "owner/unreleased", None),
    ]


def test_a_piece_table_naming_itself_is_refused(tmp_path):
    text = BASE.replace('repository = "owner/released"', 'name = "other"\nrepository = "owner/released"')
    with pytest.raises(ConfigError, match="key"):
        load(write(tmp_path, text + BLOCK.format(name="agents", path="AGENTS.md")))


def test_a_tag_that_is_not_a_release_version_is_refused(tmp_path):
    text = BASE.replace('tag = "v1.2.3"', 'tag = "main"')
    with pytest.raises(ConfigError, match="tag"):
        load(write(tmp_path, text + BLOCK.format(name="agents", path="AGENTS.md")))


def test_two_pieces_never_share_a_repository(tmp_path):
    text = BASE.replace('"owner/unreleased"', '"owner/released"')
    with pytest.raises(ConfigError, match="owner/released"):
        load(write(tmp_path, text + BLOCK.format(name="agents", path="AGENTS.md")))


def test_two_sections_never_write_the_same_path(tmp_path):
    text = BASE + BLOCK.format(name="one", path="AGENTS.md") + BLOCK.format(name="two", path="AGENTS.md")
    with pytest.raises(ConfigError, match="AGENTS.md"):
        load(write(tmp_path, text))


def test_an_unknown_kind_is_refused(tmp_path):
    text = BASE + '[[newsletter.section]]\nname = "x"\nkind = "nonesuch"\nsubject = "s"\n'
    with pytest.raises(ConfigError, match="section"):
        load(write(tmp_path, text))


def test_a_path_outside_the_repository_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="repository-relative"):
        load(write(tmp_path, BASE + BLOCK.format(name="agents", path="../AGENTS.md")))


def test_the_checked_in_configuration_loads():
    config = load(Path(__file__).parents[2] / "london.toml")
    assert config.pieces
    assert config.sections


def test_a_piece_is_titled_by_its_name_unless_it_names_a_title(tmp_path):
    text = BASE.replace('tag = "v1.2.3"', 'tag = "v1.2.3"\ntitle = "The Released Piece"')
    config = load(write(tmp_path, text + BLOCK.format(name="agents", path="AGENTS.md")))
    assert [p.title for p in config.pieces] == ["The Released Piece", "Unreleased"]


def test_tokens_describe_the_piece_the_release_set_and_the_variables(tmp_path):
    text = (
        BASE.replace('repository = "owner/unreleased"', 'repository = "owner/unreleased"\nvars = { rust = "1.88.0" }')
        + 'vars = { rust = "1.99.0", node = "22" }\n'
        + BLOCK.format(name="agents", path="AGENTS.md")
    )
    config = load(write(tmp_path, text))
    released, unreleased = (config.context(p) for p in config.pieces)
    assert released.subscriber == "released"
    assert dict(released.tokens) == {
        "piece.name": "released",
        "piece.title": "Released",
        "piece.repository": "owner/released",
        "piece.base": "main",
        "piece.tag": "v1.2.3",
        "set.released.tag": "v1.2.3",
        "vars.rust": "1.99.0",
        "vars.node": "22",
    }
    assert "piece.tag" not in unreleased.tokens
    assert "set.unreleased.tag" not in unreleased.tokens
    assert unreleased.tokens["set.released.tag"] == "v1.2.3"
    assert unreleased.tokens["vars.rust"] == "1.88.0"
    assert unreleased.tokens["vars.node"] == "22"


def test_a_variable_name_that_is_not_a_plain_word_is_refused(tmp_path):
    text = BASE + 'vars = { "Rust.Version" = "1.99.0" }\n' + BLOCK.format(name="agents", path="AGENTS.md")
    with pytest.raises(ConfigError, match="vars"):
        load(write(tmp_path, text))


def test_a_section_is_sent_to_the_pieces_it_names(tmp_path):
    only = BLOCK.format(name="agents", path="AGENTS.md") + 'pieces = ["unreleased"]\n'
    config = load(write(tmp_path, BASE + only + BLOCK.format(name="readme", path="README.md")))
    released, unreleased = config.pieces
    assert [s.name for s in config.sections_for(released)] == ["readme"]
    assert [s.name for s in config.sections_for(unreleased)] == ["agents", "readme"]
    assert config.paths(released) == ["README.md"]
    assert config.paths() == ["AGENTS.md", "README.md"]


def test_a_section_naming_an_unknown_piece_is_refused(tmp_path):
    text = BASE + BLOCK.format(name="agents", path="AGENTS.md") + 'pieces = ["released", "nonesuch"]\n'
    with pytest.raises(ConfigError, match="nonesuch"):
        load(write(tmp_path, text))


@pytest.mark.parametrize("pieces", ["[]", '["released", "released"]'])
def test_an_empty_or_repeated_list_of_pieces_is_refused(tmp_path, pieces):
    text = BASE + BLOCK.format(name="agents", path="AGENTS.md") + f"pieces = {pieces}\n"
    with pytest.raises(ConfigError, match="pieces"):
        load(write(tmp_path, text))


def test_sections_sent_to_different_pieces_may_write_the_same_path(tmp_path):
    text = (
        BASE
        + BLOCK.format(name="one", path="AGENTS.md")
        + 'pieces = ["released"]\n'
        + BLOCK.format(name="two", path="AGENTS.md")
        + 'pieces = ["unreleased"]\n'
    )
    config = load(write(tmp_path, text))
    assert [config.paths(p) for p in config.pieces] == [["AGENTS.md"], ["AGENTS.md"]]


def test_sections_sent_to_the_same_piece_never_write_the_same_path(tmp_path):
    text = (
        BASE
        + BLOCK.format(name="one", path="AGENTS.md")
        + BLOCK.format(name="two", path="AGENTS.md")
        + 'pieces = ["unreleased"]\n'
    )
    with pytest.raises(ConfigError, match="unreleased"):
        load(write(tmp_path, text))
