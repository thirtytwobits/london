"""The `structured` section kind: chosen values of a document, deep-merged into a subscriber's copy."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from newsletter.sections import Context, SectionError, Source
from newsletter.sections.structured import StructuredSection

LONDON = """{
    "python.testing.pytestEnabled": true,
    "cSpell.language": "en-GB",
    "cSpell.allowCompoundWords": true,
    "cSpell.words": ["london"],
}
"""

PIECE = """{
    // Comments explain the settings.
    "rust-analyzer.cargo.targetDir": true,
    "cSpell.language": "en-GB",
    "cSpell.words": [
        "piece"
    ]
}"""


SUBSCRIBER = Context("piece")


class TextSource(Source):
    """A source commit holding one file.

    Args:
        text: The file's contents.
    """

    repository = "owner/london"
    sha = "0" * 40

    def __init__(self, text: str) -> None:
        self.text = text

    async def read(self, path: str) -> bytes:
        """The file, whatever `path` names.

        Args:
            path: Unused.
        """
        del path
        return self.text.encode()


def section(**keys) -> StructuredSection:
    """A structured section for `.vscode/settings.json`, with `keys` added.

    Args:
        **keys: Further configuration keys.
    """
    return StructuredSection(name="settings", subject="s", path=".vscode/settings.json", **keys)


def test_selected_values_merge_into_the_subscribers_document_keeping_its_comments(tmp_path):
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "settings.json").write_text(PIECE)
    chosen = section(select=["/cSpell.allowCompoundWords", "/cSpell.words"], rules={"/cSpell.words": "union"})
    asyncio.run(chosen.apply(TextSource(LONDON), tmp_path, SUBSCRIBER))
    text = (tmp_path / ".vscode" / "settings.json").read_text()
    assert text.startswith('{\n    // Comments explain the settings.\n    "rust-analyzer.cargo.targetDir": true,')
    assert json.loads(text.replace("    // Comments explain the settings.\n", "")) == {
        "rust-analyzer.cargo.targetDir": True,
        "cSpell.language": "en-GB",
        "cSpell.words": ["piece", "london"],
        "cSpell.allowCompoundWords": True,
    }


def test_a_subscriber_without_the_document_receives_the_selected_values(tmp_path):
    asyncio.run(section(select=["/cSpell.language"]).apply(TextSource(LONDON), tmp_path, SUBSCRIBER))
    assert json.loads((tmp_path / ".vscode" / "settings.json").read_text()) == {"cSpell.language": "en-GB"}


def test_a_subscriber_without_the_document_receives_the_whole_document_a_root_keep_rule_selects(tmp_path):
    keep = section(select=[""], rules={"": "keep"})
    asyncio.run(keep.apply(TextSource(LONDON), tmp_path, SUBSCRIBER))
    target = tmp_path / ".vscode" / "settings.json"
    assert json.loads(target.read_text()) == json.loads(LONDON.replace(",\n}", "\n}"))
    target.write_text('{"cSpell.language": "en-US"}')
    asyncio.run(keep.apply(TextSource(LONDON), tmp_path, SUBSCRIBER))
    assert json.loads(target.read_text()) == {"cSpell.language": "en-US"}


def test_a_subscriber_already_up_to_date_is_left_byte_for_byte(tmp_path):
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "settings.json").write_text(PIECE)
    asyncio.run(section(select=["/cSpell.language"]).apply(TextSource(LONDON), tmp_path, SUBSCRIBER))
    assert (tmp_path / ".vscode" / "settings.json").read_text() == PIECE


def test_check_refuses_a_selected_value_the_source_lacks():
    with pytest.raises(SectionError, match="/cSpell.missing is not in the document"):
        asyncio.run(section(select=["/cSpell.missing"]).check(TextSource(LONDON), SUBSCRIBER))


def test_check_refuses_a_rule_that_cannot_merge_the_sources_value():
    with pytest.raises(SectionError, match="cannot merge"):
        asyncio.run(
            section(select=["/cSpell.words"], rules={"/cSpell.words": "merge"}).check(TextSource(LONDON), SUBSCRIBER)
        )


def test_a_malformed_subscriber_document_is_refused(tmp_path):
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "settings.json").write_text("{")
    with pytest.raises(SectionError, match="not JSON"):
        asyncio.run(section(select=["/cSpell.language"]).apply(TextSource(LONDON), tmp_path, SUBSCRIBER))


@pytest.mark.parametrize(
    "keys",
    [
        {"select": ["cSpell.words"]},
        {"rules": {"/cSpell.words": "splice"}},
        {"format": "ini"},
    ],
)
def test_configuration_errors_are_refused_when_loaded(keys):
    with pytest.raises(ValidationError):
        section(**keys)


def test_a_path_whose_suffix_chooses_no_format_needs_one_named():
    with pytest.raises(ValidationError, match="format"):
        StructuredSection(name="s", subject="s", path="settings.cfg")
    assert StructuredSection(name="s", subject="s", path="settings.cfg", format="toml").paths() == ("settings.cfg",)
