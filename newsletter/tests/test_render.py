"""Rendering `{{london:NAME}}` tokens into what a section sends each subscriber."""

import asyncio
import json

import pytest

from newsletter.sections import Context, SectionError, Source, render
from newsletter.sections.block import BlockSection
from newsletter.sections.file import FileSection
from newsletter.sections.structured import StructuredSection

CLAPHAM = Context("clapham", {"piece.title": "Clapham", "piece.repository": "owner/clapham", "vars.rust": "1.99.0"})


class TextSource(Source):
    """A source commit holding one file.

    Args:
        data: The file's contents.
    """

    repository = "owner/london"
    sha = "0" * 40

    def __init__(self, data: str | bytes) -> None:
        self.data = data.encode() if isinstance(data, str) else data

    async def read(self, path: str) -> bytes:
        """The file, whatever `path` names.

        Args:
            path: Unused.
        """
        del path
        return self.data


def test_each_token_is_replaced_with_its_value():
    text = "# Releasing {{london:piece.title}}\n\nTake `{{london:piece.repository}}` at {{ london:vars.rust }}.\n"
    assert render(text, CLAPHAM, "RELEASING.md") == "# Releasing Clapham\n\nTake `owner/clapham` at 1.99.0.\n"


def test_text_that_is_not_a_london_token_is_left_as_it_is():
    text = "tag: ${{ github.ref_name }}\nversion: {{version}}\nother: {{other:piece.title}}\n"
    assert render(text, CLAPHAM, "release.yml") == text


def test_every_token_without_a_value_is_named_with_the_subscriber_and_the_file():
    with pytest.raises(SectionError) as raised:
        render("{{london:piece.tag}} {{london:vars.node}} {{london:piece.tag}}", CLAPHAM, "RELEASING.md")
    message = str(raised.value)
    assert "RELEASING.md" in message
    assert "piece.tag, vars.node" in message
    assert "clapham" in message


SOURCE = """# London

{{london:unknown.outside}}

<!-- newsletter:begin releasing -->
## Releasing {{london:piece.title}}
<!-- newsletter:end releasing -->
"""


def block(**keys) -> BlockSection:
    """A block section for `RELEASING.md`, with `keys` added.

    Args:
        **keys: Further configuration keys.
    """
    return BlockSection(name="releasing", subject="s", path="RELEASING.md", block="releasing", **keys)


def test_a_rendering_block_section_renders_the_block_alone(tmp_path):
    (tmp_path / "RELEASING.md").write_text("# Clapham\n\n{{london:piece.title}} is kept.\n")
    asyncio.run(block(render=True).apply(TextSource(SOURCE), tmp_path, CLAPHAM))
    assert (tmp_path / "RELEASING.md").read_text() == (
        "# Clapham\n\n{{london:piece.title}} is kept.\n\n"
        "<!-- newsletter:begin releasing -->\n## Releasing Clapham\n<!-- newsletter:end releasing -->\n"
    )


def test_a_section_that_does_not_render_sends_its_tokens_unchanged(tmp_path):
    asyncio.run(block().apply(TextSource(SOURCE), tmp_path, CLAPHAM))
    assert "## Releasing {{london:piece.title}}\n" in (tmp_path / "RELEASING.md").read_text()


def test_check_refuses_a_token_the_subscriber_has_no_value_for():
    source = TextSource(SOURCE.replace("{{london:piece.title}}", "{{london:piece.tag}}"))
    with pytest.raises(SectionError, match=r"releasing: 000000000000:RELEASING.md: no value for piece.tag"):
        asyncio.run(block(render=True).check(source, CLAPHAM))


def test_a_rendering_file_section_renders_the_whole_file(tmp_path):
    workflow = "on: push\n# Sent to {{london:piece.title}}.\nrun: rustup toolchain install {{london:vars.rust}}\n"
    section = FileSection(name="workflow", subject="s", path=".github/workflows/release.yml", render=True)
    asyncio.run(section.apply(TextSource(workflow), tmp_path, CLAPHAM))
    assert (tmp_path / ".github" / "workflows" / "release.yml").read_text() == (
        "on: push\n# Sent to Clapham.\nrun: rustup toolchain install 1.99.0\n"
    )


def test_a_rendering_file_section_refuses_a_file_that_is_not_utf8():
    section = FileSection(name="logo", subject="s", path="logo.png", render=True)
    with pytest.raises(SectionError, match="UTF-8"):
        asyncio.run(section.check(TextSource(b"\x89PNG\xff"), CLAPHAM))


def test_a_rendering_structured_section_merges_the_rendered_values(tmp_path):
    source = '{"rust": "{{london:vars.rust}}", "owner": "{{london:piece.title}}"}'
    section = StructuredSection(name="toolchain", subject="s", path="toolchain.json", select=["/rust"], render=True)
    (tmp_path / "toolchain.json").write_text('{"node": "22"}')
    asyncio.run(section.apply(TextSource(source), tmp_path, CLAPHAM))
    assert json.loads((tmp_path / "toolchain.json").read_text()) == {"node": "22", "rust": "1.99.0"}
