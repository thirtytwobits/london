"""The shared release tool's changelog, version and preparation rules."""

import argparse
import importlib.util
import itertools
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parent.parent / "release.py")
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules["release"] = release
SPEC.loader.exec_module(release)

CHANGELOG = """# Changelog

## Unreleased

### Breaking

- Remove the old call.

### Fixed

- Fix the second thing.

## 0.4.0

### Added

- The first thing.

## 0.3.0

- An entry written before subsections, which history keeps.

## 0.3.0-rc.1

### Changes

- A prerelease.
"""


def test_a_changelog_in_release_form_has_no_problems():
    assert release.changelog_problems(CHANGELOG) == []


@pytest.mark.parametrize(
    ("heading", "named"),
    [("## 0.4.0 — 2026-10-27", "0.4.0 — 2026-10-27"), ("## [0.4.0]", "[0.4.0]"), ("## Next", "Next")],
)
def test_a_heading_that_is_neither_unreleased_nor_a_version_is_refused(heading, named):
    found = release.changelog_problems(CHANGELOG.replace("## 0.4.0", heading))
    assert any(f"`## {named}` is neither" in problem for problem in found)


def test_unreleased_must_come_first():
    moved = CHANGELOG.replace("## Unreleased\n", "").replace(
        "## 0.3.0\n", "## Unreleased\n\n### Added\n\n- X.\n\n## 0.3.0\n"
    )
    assert any("must be the first entry" in problem for problem in release.changelog_problems(moved))


def test_versions_must_descend():
    swapped = CHANGELOG.replace("## 0.4.0", "## 0.2.0")
    assert any("`## 0.3.0` must follow, not precede, `## 0.2.0`" in p for p in release.changelog_problems(swapped))


def test_a_release_follows_its_prereleases():
    assert release.order("0.3.0") > release.order("0.3.0-rc.1")


def test_prereleases_follow_semantic_versioning_precedence():
    ascending = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0-rc.9",
        "1.0.0-rc.10",
        "1.0.0",
    ]
    for lower, higher in itertools.pairwise(ascending):
        assert release.order(lower) < release.order(higher), f"{lower} < {higher}"


def test_a_changelog_may_hold_rc_10_above_rc_9():
    text = "# Changelog\n\n## 1.0.0-rc.10\n\n### Fixed\n\n- X.\n\n## 1.0.0-rc.9\n\n### Fixed\n\n- Y.\n"
    assert release.changelog_problems(text) == []


@pytest.mark.parametrize(
    ("unreleased", "complaint"),
    [
        ("- Untitled.\n", "has text before its first subsection"),
        ("", "has no subsection"),
        ("### Changed\n\n- X.\n", "has `### Changed`"),
        ("### Added\n\n### Fixed\n\n- X.\n", "`### Added` is empty"),
        ("### Fixed\n\n- X.\n\n### Added\n\n- Y.\n", "in the order"),
        ("### Added\n\n- X.\n\n### Added\n\n- Y.\n", "once each"),
    ],
)
def test_the_unreleased_entry_is_checked_in_full(unreleased, complaint):
    text = "# Changelog\n\n## Unreleased\n\n" + unreleased + "\n## 0.4.0\n\n### Added\n\n- X.\n"
    assert any(complaint in problem for problem in release.changelog_problems(text))


def test_the_changelog_begins_with_its_title():
    assert release.changelog_problems("# Changes\n") == ["CHANGELOG.md must begin with `# Changelog`"]


def test_notes_are_the_body_of_the_versions_entry():
    assert release.notes(CHANGELOG, "0.4.0") == "### Added\n\n- The first thing.\n"


def test_notes_refuse_an_absent_or_incomplete_entry():
    with pytest.raises(release.Refusal, match="one `## 0.5.0` entry, found 0"):
        release.notes(CHANGELOG, "0.5.0")
    with pytest.raises(release.Refusal, match="no subsection"):
        release.notes(CHANGELOG, "0.3.0")


def test_releasing_names_the_unreleased_entry():
    text = release.released(CHANGELOG, "0.5.0")
    assert text.startswith("# Changelog\n\n## 0.5.0\n\n### Breaking\n")
    assert release.changelog_problems(text) == []
    assert release.notes(text, "0.5.0").startswith("### Breaking\n\n- Remove the old call.\n")


def test_releasing_refuses_without_a_complete_unreleased_entry():
    with pytest.raises(release.Refusal, match="one `## Unreleased` entry, found 0"):
        release.released(CHANGELOG.replace("## Unreleased", "## 0.5.0"), "0.6.0")
    with pytest.raises(release.Refusal, match="is empty"):
        release.released(CHANGELOG.replace("- Remove the old call.\n", ""), "0.5.0")


@pytest.mark.parametrize(("bump", "version"), [("patch", "0.3.3"), ("minor", "0.4.0"), ("major", "1.0.0")])
def test_bumps(bump, version):
    assert release.bumped("0.3.2", bump) == version


def test_a_prerelease_is_not_bumped():
    with pytest.raises(release.Refusal, match="prerelease"):
        release.bumped("0.4.0-rc.1", "patch")


MANIFEST = """[workspace]
members = ["crates/*"]

[workspace.package]
version = "0.3.2"
edition = "2024"

[workspace.dependencies]
alpha = { version = "0.3.2", path = "crates/alpha" }
beta = { path = "crates/beta", version = "=0.3.2" }
outside = { version = "0.3.2" }
other = { git = "https://github.com/owner/other", tag = "v0.3.2", version = "0.3.2" }

[package]
version = "0.3.2"
"""


def test_a_cargo_version_moves_the_workspace_and_its_path_dependencies_alone():
    moved = release.set_cargo_version(MANIFEST, "0.3.2", "0.4.0")
    assert release.cargo_version(moved) == "0.4.0"
    assert 'alpha = { version = "0.4.0", path = "crates/alpha" }' in moved
    assert 'beta = { path = "crates/beta", version = "=0.4.0" }' in moved
    assert 'outside = { version = "0.3.2" }' in moved
    assert 'tag = "v0.3.2", version = "0.3.2" }' in moved
    assert moved.endswith('[package]\nversion = "0.3.2"\n')


def test_a_cargo_version_refuses_a_workspace_at_another_version():
    with pytest.raises(release.Refusal, match="is not 0.3.1"):
        release.set_cargo_version(MANIFEST, "0.3.1", "0.4.0")


MEMBER = """[package]
name = "gamma"
version.workspace = true

[[bin]]
name = "gamma"
path = "src/main.rs"

[dependencies]
alpha = { path = "../alpha", version = "0.3.2" }
outside = "0.3.2"

[dev-dependencies]
beta = { version = "=0.3.2", path = "../beta" }

[target.'cfg(unix)'.dependencies]
delta = { path = "../delta", version = "0.3.2" }
older = { path = "../older", version = "0.1.0" }
"""


def test_a_member_moves_each_path_dependency_at_the_previous_version():
    moved = release.set_path_dependencies(MEMBER, "0.3.2", "0.4.0")
    assert 'alpha = { path = "../alpha", version = "0.4.0" }' in moved
    assert 'beta = { version = "=0.4.0", path = "../beta" }' in moved
    assert 'delta = { path = "../delta", version = "0.4.0" }' in moved
    assert 'older = { path = "../older", version = "0.1.0" }' in moved
    assert 'outside = "0.3.2"' in moved
    assert 'path = "src/main.rs"' in moved


def test_a_consumer_carries_the_patches_that_do_not_point_into_the_piece():
    manifest = """[workspace]

[patch.crates-io]
tree = { git = "https://github.com/owner/tree", rev = "abc123" }
local = { path = "vendor/local" }

[patch."https://github.com/owner/other"]
other = { git = "https://github.com/owner/fork", branch = "main", default-features = false }
"""
    patches = release.consumer_patches(manifest)
    assert tomllib.loads(patches) == {
        "patch": {
            "crates-io": {"tree": {"git": "https://github.com/owner/tree", "rev": "abc123"}},
            "https://github.com/owner/other": {
                "other": {"git": "https://github.com/owner/fork", "branch": "main", "default-features": False}
            },
        }
    }
    assert release.consumer_patches("[workspace]\n") == ""


def test_a_consumer_is_its_own_workspace_and_carries_the_patches(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/missing"]\n')
    consumer = tmp_path / "consumer"
    (consumer / "src").mkdir(parents=True)
    (consumer / "src" / "lib.rs").write_text("")
    manifest = '[workspace]\n\n[patch.crates-io]\ntree = { git = "https://github.com/owner/tree", rev = "abc123" }\n'
    (consumer / "Cargo.toml").write_text(release.consumer_manifest(manifest))
    parsed = tomllib.loads((consumer / "Cargo.toml").read_text())
    assert parsed["workspace"] == {}
    assert parsed["patch"]["crates-io"]["tree"]["rev"] == "abc123"
    subprocess.run(["cargo", "metadata", "--no-deps", "--format-version", "1", "--offline"], cwd=consumer, check=True)
    assert (tmp_path / "Cargo.toml").read_text() == '[workspace]\nmembers = ["crates/missing"]\n'


def test_checksums_name_each_file(tmp_path):
    (tmp_path / "b.tgz").write_bytes(b"b")
    (tmp_path / "a.crate").write_bytes(b"")
    assert release.sha256sums([tmp_path / "b.tgz", tmp_path / "a.crate"]) == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  a.crate\n"
        "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d  b.tgz\n"
    )


def test_release_files_must_match_every_pattern(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "piece-docs-0.4.0.tar.gz").write_bytes(b"")
    (tmp_path / "piece-0.4.0.crate").write_bytes(b"")
    (tmp_path / "unlisted.txt").write_bytes(b"")
    chosen = release.matched(tmp_path, ["*.crate", "piece-docs-*.tar.gz"])
    assert [path.name for path in chosen] == ["piece-0.4.0.crate", "piece-docs-0.4.0.tar.gz"]
    with pytest.raises(release.Refusal, match=r"nothing matching \*.tgz"):
        release.matched(tmp_path, ["*.tgz"])


def piece(root: Path, steps: dict[str, list[list[str]]] | None = None) -> Path:
    """A piece's repository on a release branch, with `steps` in its release.json."""
    record = {"version": "0.4.0", "repository": "https://github.com/owner/piece", "license": "MIT"}
    if steps:
        record["steps"] = steps
    (root / "release.json").write_text(json.dumps(record, indent=2) + "\n")
    (root / "CHANGELOG.md").write_text(CHANGELOG)
    for command in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "Start"],
        ["switch", "-q", "-c", "release"],
    ):
        subprocess.run(["git", *command], cwd=root, check=True)
    return root


def test_prepare_names_the_entry_sets_the_version_and_runs_the_pieces_steps(tmp_path):
    script = "import sys, pathlib; pathlib.Path('prepared').write_text(' '.join(sys.argv[1:]))"
    root = piece(tmp_path, {"prepare": [[sys.executable, "-c", script, "{previous}", "{version}", "{tag}"]]})
    release.prepare(argparse.Namespace(root=root, bump="minor"))
    record = json.loads((root / "release.json").read_text())
    assert record == {"version": "0.5.0", "repository": "https://github.com/owner/piece", "license": "MIT"} | {
        "steps": record["steps"]
    }
    assert (root / "CHANGELOG.md").read_text().startswith("# Changelog\n\n## 0.5.0\n")
    assert (root / "prepared").read_text() == "0.4.0 0.5.0 v0.5.0"
    assert release.problems(root, release.load(root)[1], "v0.5.0") == []


def test_prepare_moves_the_version_of_a_cargo_workspace_and_its_members(tmp_path):
    for name, manifest in {
        "": '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n\n[workspace.package]\nversion = "0.4.0"\n',
        "crates/alpha/": '[package]\nname = "alpha"\nversion.workspace = true\nedition = "2024"\n',
        "crates/beta/": (
            '[package]\nname = "beta"\nversion.workspace = true\nedition = "2024"\n\n'
            '[dependencies]\nalpha = { path = "../alpha", version = "0.4.0" }\n'
        ),
    }.items():
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "Cargo.toml").write_text(manifest)
        if name:
            (tmp_path / name / "src").mkdir()
            (tmp_path / name / "src" / "lib.rs").write_text("")
    subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=tmp_path, check=True)
    root = piece(tmp_path)
    release.prepare(argparse.Namespace(root=root, bump="patch"))
    assert release.cargo_version((root / "Cargo.toml").read_text()) == "0.4.1"
    assert 'alpha = { path = "../alpha", version = "0.4.1" }' in (root / "crates/beta/Cargo.toml").read_text()
    lock = tomllib.loads((root / "Cargo.lock").read_text())
    assert {package["name"]: package["version"] for package in lock["package"]} == {"alpha": "0.4.1", "beta": "0.4.1"}
    assert release.problems(root, release.load(root)[1], "v0.4.1") == []


def test_prepare_refuses_main_and_a_dirty_tree(tmp_path):
    root = piece(tmp_path)
    (root / "stray").write_text("")
    with pytest.raises(release.Refusal, match="work tree has changes"):
        release.prepare(argparse.Namespace(root=root, bump="patch"))
    (root / "stray").unlink()
    subprocess.run(["git", "switch", "-q", "main"], cwd=root, check=True)
    with pytest.raises(release.Refusal, match="not on main"):
        release.prepare(argparse.Namespace(root=root, bump="patch"))


def test_the_tag_must_name_the_version_and_cargo_must_agree(tmp_path):
    root = piece(tmp_path)
    (root / "Cargo.toml").write_text(MANIFEST)
    found = release.problems(root, release.load(root)[1], "v0.3.2")
    assert "Cargo.toml version 0.3.2 is not release.json version 0.4.0" in found
    assert "v0.3.2 does not name release.json version 0.4.0" in found


def test_unknown_steps_are_refused(tmp_path):
    root = piece(tmp_path, {"publish": [["true"]]})
    with pytest.raises(release.Refusal, match="unknown names: publish"):
        release.load(root)
