"""Loading and validating `newsletter.toml`."""

from pathlib import Path

import pytest

from newsletter.config import ConfigError, load

BASE = """
[source]
repository = "owner/london"

[[subscriber]]
repository = "owner/piece"
"""

BLOCK = """
[[section]]
name = "{name}"
kind = "block"
subject = "Update the block"
path = "{path}"
block = "shared"
"""


def write(tmp_path: Path, text: str) -> Path:
    """Write `text` as `newsletter.toml` in `tmp_path`.

    Args:
        tmp_path: The test's temporary directory.
        text: The configuration.
    """
    path = tmp_path / "newsletter.toml"
    path.write_text(text)
    return path


def test_defaults_fill_what_the_file_omits(tmp_path):
    config = load(write(tmp_path, BASE + BLOCK.format(name="agents", path="AGENTS.md")))
    assert config.source.ref == "origin/main"
    assert config.staging.path == Path(".newsletter")
    assert config.subscriber[0].name == "piece"
    assert config.subscriber[0].remote == "https://github.com/owner/piece.git"
    assert config.paths() == ["AGENTS.md"]


def test_two_sections_never_write_the_same_path(tmp_path):
    text = BASE + BLOCK.format(name="one", path="AGENTS.md") + BLOCK.format(name="two", path="AGENTS.md")
    with pytest.raises(ConfigError, match="AGENTS.md"):
        load(write(tmp_path, text))


def test_an_unknown_kind_is_refused(tmp_path):
    text = BASE + '[[section]]\nname = "x"\nkind = "nonesuch"\nsubject = "s"\n'
    with pytest.raises(ConfigError, match="section"):
        load(write(tmp_path, text))


def test_a_path_outside_the_repository_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="repository-relative"):
        load(write(tmp_path, BASE + BLOCK.format(name="agents", path="../AGENTS.md")))


def test_the_shipped_configuration_loads():
    config = load(Path(__file__).parents[1] / "newsletter.toml")
    assert config.sections
