"""Splicing a marked block into a subscriber's file."""

import asyncio

import pytest

from newsletter.sections import Context, SectionError, Source
from newsletter.sections.block import BlockSection, splice
from newsletter.sections.file import FileSection

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


def test_a_source_ending_at_the_end_marker_still_ends_the_block_with_a_line_ending():
    source = SOURCE.rstrip("\n")
    target = "# Piece\n<!-- newsletter:begin shared -->\nOld.\n<!-- newsletter:end shared -->\nAfter.\n"
    text, _ = splice(source, target, "shared")
    assert text == "# Piece\n" + BLOCK + "After.\n"
    assert splice(source, text, "shared") == (text, False)


class BytesSource(Source):
    """A source commit holding one file.

    Args:
        data: The file's contents.
    """

    repository = "owner/london"
    sha = "0" * 40

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def read(self, path: str) -> bytes:
        """The file, whatever `path` names.

        Args:
            path: Unused.
        """
        del path
        return self.data


def test_a_block_in_a_file_that_is_not_utf8_is_refused(tmp_path):
    section = BlockSection(name="agents", subject="s", path="AGENTS.md", block="shared")
    with pytest.raises(SectionError, match="UTF-8"):
        asyncio.run(section.apply(BytesSource(SOURCE.encode() + b"\xff"), tmp_path, Context("piece")))
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\n")
    with pytest.raises(SectionError, match="UTF-8"):
        asyncio.run(section.apply(BytesSource(SOURCE.encode()), tmp_path, Context("piece")))
    assert (tmp_path / "AGENTS.md").read_bytes() == b"\xff\n"


def test_a_whole_file_section_copies_bytes_unchanged(tmp_path):
    data = bytes(range(256)) + b"\r\n\x89PNG"
    section = FileSection(name="logo", subject="s", path="assets/logo.png")
    asyncio.run(section.apply(BytesSource(data), tmp_path, Context("piece")))
    assert (tmp_path / "assets" / "logo.png").read_bytes() == data


def test_a_block_with_a_target_is_read_from_path_and_written_to_target(tmp_path):
    class OneFile(Source):
        """A source commit holding `SOURCE` at `pieces/RELEASING.md` alone."""

        repository = "owner/london"
        sha = "0" * 40

        async def read(self, path: str) -> bytes:
            assert path == "pieces/RELEASING.md"
            return SOURCE.encode()

    section = BlockSection(
        name="releasing", subject="s", path="pieces/RELEASING.md", target="RELEASING.md", block="shared"
    )
    assert section.paths() == ("RELEASING.md",)
    (tmp_path / "RELEASING.md").write_text("# Releasing\n")
    asyncio.run(section.apply(OneFile(), tmp_path, Context("piece")))
    assert (tmp_path / "RELEASING.md").read_text() == "# Releasing\n\n" + BLOCK
    assert not (tmp_path / "pieces").exists()
