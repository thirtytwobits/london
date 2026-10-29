"""Whether the releases a release set names, and the examples, compose."""

import asyncio
import tomllib

from newsletter.config import Config
from newsletter.releaseset import Pin, check, notes, pins, problems

PIECES = {"owner/store": "store", "owner/ops": "ops", "owner/kernel": "kernel"}
TAGS = {"store": "v0.2.0", "ops": "v0.5.0", "kernel": "v1.0.0"}
FORK = 'tree = { git = "https://github.com/owner/tree-fork.git", rev = "abc123" }'

STORE = tomllib.loads(f"""
[workspace.dependencies]
ops = {{ git = "https://github.com/owner/ops", tag = "v0.5.0" }}

[patch.crates-io]
{FORK}
local = {{ path = "vendor/local" }}
""")


def test_dependencies_on_a_piece_are_found_in_every_table():
    manifest = tomllib.loads("""
[dependencies]
store = { git = "https://github.com/Owner/Store.git", tag = "v0.2.0" }
serde = "1"
other = { git = "https://github.com/owner/other", tag = "v9.0.0" }

[dev-dependencies]
ops = { git = "https://github.com/owner/ops", rev = "0123abc" }

[target.'cfg(unix)'.build-dependencies]
kernel = { git = "https://github.com/owner/kernel/", tag = "v1.0.0" }

[workspace.dependencies]
store-cli = { git = "https://github.com/owner/store", tag = "v0.2.0", package = "store" }
""")
    assert pins(manifest, PIECES) == [
        Pin("dependencies.store", "store", "v0.2.0"),
        Pin("dev-dependencies.ops", "ops", None),
        Pin("workspace.dependencies.store-cli", "store", "v0.2.0"),
        Pin("target.cfg(unix).build-dependencies.kernel", "kernel", "v1.0.0"),
    ]


def test_a_set_whose_manifests_take_the_set_and_carry_its_patches_composes():
    example = tomllib.loads(f"""
[dependencies]
store = {{ git = "https://github.com/owner/store", tag = "v0.2.0" }}
ops = {{ git = "https://github.com/owner/ops", tag = "v0.5.0" }}

[patch.crates-io]
{FORK}
""")
    assert not problems(TAGS, PIECES, {"store": STORE, "example": example}, {"store": STORE, "ops": {}})


def test_another_tag_a_revision_or_an_unreleased_piece_is_refused():
    manifest = tomllib.loads("""
[dependencies]
ops = { git = "https://github.com/owner/ops", tag = "v0.4.0" }
kernel = { git = "https://github.com/owner/kernel", branch = "main" }
""")
    assert problems({"ops": "v0.5.0"}, PIECES, {"example": manifest}, {}) == [
        "example: `dependencies.ops` takes ops v0.4.0, not v0.5.0",
        "example: `dependencies.kernel` takes kernel, which has no release in the set",
    ]
    assert problems(TAGS, PIECES, {"example": manifest}, {}) == [
        "example: `dependencies.ops` takes ops v0.4.0, not v0.5.0",
        "example: `dependencies.kernel` takes kernel a revision or branch, not v1.0.0",
    ]


def test_a_manifest_that_takes_a_piece_carries_its_git_patches_at_the_set():
    bare = tomllib.loads('[dependencies]\nstore = { git = "https://github.com/owner/store", tag = "v0.2.0" }\n')
    moved = tomllib.loads(
        '[dependencies]\nstore = { git = "https://github.com/owner/store", tag = "v0.2.0" }\n\n'
        '[patch.crates-io]\ntree = { git = "https://github.com/owner/tree-fork.git", rev = "def456" }\n'
    )
    expected = "takes store but does not carry its `[patch.crates-io]` entry for tree at v0.2.0"
    assert problems(TAGS, PIECES, {"bare": bare, "moved": moved}, {"store": STORE}) == [
        f"bare: {expected}",
        f"moved: {expected}",
    ]


def config() -> Config:
    """A set of two released pieces and one that has no release."""
    return Config.model_validate(
        {
            "london": {"repository": "owner/london"},
            "piece": {
                "store": {"repository": "owner/store", "tag": "v0.2.0"},
                "ops": {"repository": "owner/ops", "tag": "v0.5.0", "title": "Ops"},
                "kernel": {"repository": "owner/kernel"},
            },
            "newsletter": {"section": [{"name": "agents", "kind": "file", "subject": "s", "path": "AGENTS.md"}]},
        }
    )


def test_the_notes_list_each_released_piece_with_its_release():
    assert notes(config()) == (
        "The London Project's examples build against these releases.\n\n"
        "| Piece | Release |\n|---|---|\n"
        "| [Store](https://github.com/owner/store) | [v0.2.0](https://github.com/owner/store/releases/tag/v0.2.0) |\n"
        "| [Ops](https://github.com/owner/ops) | [v0.5.0](https://github.com/owner/ops/releases/tag/v0.5.0) |\n"
    )


class Releases:  # pylint: disable=too-few-public-methods  # a ManifestSource has one method
    """Each released piece's root `Cargo.toml`, read at the tag the set names."""

    def __init__(self) -> None:
        self.read_at: list[tuple[str, str, str]] = []

    async def read(self, repository: str, tag: str, path: str) -> str:
        """The root manifest of `repository` at `tag`.

        Args:
            repository: The repository as `owner/name`.
            tag: The tag.
            path: The file's path in the repository.
        """
        self.read_at.append((repository, tag, path))
        if repository == "owner/store":
            return '[workspace.dependencies]\nops = { git = "https://github.com/owner/ops", tag = "v0.4.0" }\n'
        return "[workspace]\n"


def test_check_reads_each_release_at_its_tag_and_checks_it_with_the_examples():
    releases = Releases()
    example = '[dependencies]\nops = { git = "https://github.com/owner/ops", tag = "v0.5.0" }\n'
    found = asyncio.run(check(config(), {"examples/one/Cargo.toml": example}, releases))
    assert releases.read_at == [("owner/store", "v0.2.0", "Cargo.toml"), ("owner/ops", "v0.5.0", "Cargo.toml")]
    assert found == ["store v0.2.0 Cargo.toml: `workspace.dependencies.ops` takes ops v0.4.0, not v0.5.0"]
