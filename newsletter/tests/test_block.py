"""Splicing a marked block into a subscriber's file."""

import pytest

from newsletter.sections import SectionError
from newsletter.sections.block import splice

SOURCE = """# London

London's own rules.

<!-- newsletter:begin shared · maintained in london -->
## Shared

Shared rules.
<!-- newsletter:end shared -->
"""

BLOCK = """<!-- newsletter:begin shared · maintained in london -->
## Shared

Shared rules.
<!-- newsletter:end shared -->
"""


def test_replaces_the_subscribers_block_and_keeps_the_rest():
    target = (
        "# Piece\n\nPiece rules.\n\n<!-- newsletter:begin shared -->\nOld.\n<!-- newsletter:end shared -->\n\nAfter.\n"
    )
    text, appended = splice(SOURCE, target, "shared")
    assert text == "# Piece\n\nPiece rules.\n\n" + BLOCK + "\nAfter.\n"
    assert not appended


def test_appends_the_block_to_a_file_without_it():
    text, appended = splice(SOURCE, "# Piece\n\nPiece rules.\n\n\n", "shared")
    assert text == "# Piece\n\nPiece rules.\n\n" + BLOCK
    assert appended


def test_creates_a_missing_file_from_the_block():
    assert splice(SOURCE, None, "shared") == (BLOCK, True)


def test_is_idempotent():
    once, _ = splice(SOURCE, "# Piece\n", "shared")
    assert splice(SOURCE, once, "shared") == (once, False)


def test_leaves_other_blocks_alone():
    other = "<!-- newsletter:begin other -->\nKept.\n<!-- newsletter:end other -->\n"
    text, _ = splice(SOURCE, other, "shared")
    assert text.startswith(other)


def test_refuses_a_source_without_the_block():
    with pytest.raises(SectionError):
        splice("# London\n", "# Piece\n", "shared")


@pytest.mark.parametrize(
    "target",
    [
        "<!-- newsletter:begin shared -->\nUnclosed.\n",
        "<!-- newsletter:end shared -->\n<!-- newsletter:begin shared -->\n",
        "<!-- newsletter:begin shared -->\n<!-- newsletter:end shared -->\n" * 2,
    ],
)
def test_refuses_malformed_markers_in_the_subscriber(target):
    with pytest.raises(SectionError):
        splice(SOURCE, target, "shared")
