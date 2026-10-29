"""The release set: the release of each piece that `london.toml` names, and whether they compose.

Cargo treats each Git tag as a separate source, so a set composes only when every manifest that
takes a piece takes the release the set names. Workspace patches do not propagate, so a manifest
that takes a piece also carries the piece's own Git `[patch]` entries at that release.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from . import process
from .config import Config

_GITHUB = re.compile(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?")
_DEPENDENCIES = ("dependencies", "dev-dependencies", "build-dependencies")

Manifest = Mapping[str, Any]


@dataclass(frozen=True)
class Pin:
    """A dependency on a piece's repository by Git source.

    Attributes:
        where: The table and key that declare the dependency, such as `workspace.dependencies.clapham`.
        piece: The piece whose repository the dependency names.
        tag: The tag the dependency takes, or `None` when it takes a revision or a branch.
    """

    where: str
    piece: str
    tag: str | None


def _tables(manifest: Manifest) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Each dependency and patch table of `manifest`, with its dotted name.

    Args:
        manifest: A parsed `Cargo.toml`.
    """
    for name in _DEPENDENCIES:
        if name in manifest:
            yield name, manifest[name]
    if "dependencies" in manifest.get("workspace", {}):
        yield "workspace.dependencies", manifest["workspace"]["dependencies"]
    for target, tables in manifest.get("target", {}).items():
        for name in _DEPENDENCIES:
            if name in tables:
                yield f"target.{target}.{name}", tables[name]
    for source, entries in manifest.get("patch", {}).items():
        yield f"patch.{source}", entries


def repository_of(url: str) -> str | None:
    """The `owner/name` of a GitHub repository URL, in lower case, or `None` for any other URL.

    Args:
        url: A Git URL.
    """
    match = _GITHUB.fullmatch(url)
    return match.group(1).lower() if match else None


def pins(manifest: Manifest, pieces: Mapping[str, str]) -> list[Pin]:
    """Each dependency of `manifest` on a piece's repository.

    Args:
        manifest: A parsed `Cargo.toml`.
        pieces: The piece each repository belongs to, keyed by `owner/name` in lower case.
    """
    found = []
    for table, entries in _tables(manifest):
        for key, spec in entries.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("git"), str):
                continue
            piece = pieces.get(repository_of(spec["git"]) or "")
            if piece is not None:
                tag = spec.get("tag")
                found.append(Pin(f"{table}.{key}", piece, tag if isinstance(tag, str) else None))
    return found


def patches(manifest: Manifest) -> dict[tuple[str, str], Any]:
    """The Git entries of `manifest`'s `[patch]` tables, keyed by source and crate.

    Args:
        manifest: A parsed `Cargo.toml`.
    """
    return {
        (source, name): entry
        for source, entries in manifest.get("patch", {}).items()
        for name, entry in entries.items()
        if isinstance(entry, dict) and "git" in entry
    }


def problems(
    tags: Mapping[str, str],
    pieces: Mapping[str, str],
    manifests: Mapping[str, Manifest],
    roots: Mapping[str, Manifest],
) -> list[str]:
    """Where `manifests` do not compose with the release set.

    Args:
        tags: The tag of each piece in the set.
        pieces: The piece each repository belongs to, keyed by `owner/name` in lower case.
        manifests: Each manifest to check, keyed by the name a problem gives it.
        roots: Each piece's root manifest at its tag in the set.
    """
    found = []
    for label, manifest in manifests.items():
        taken = pins(manifest, pieces)
        for pin in taken:
            wanted = tags.get(pin.piece)
            if wanted is None:
                found.append(f"{label}: `{pin.where}` takes {pin.piece}, which has no release in the set")
            elif pin.tag != wanted:
                took = pin.tag or "a revision or branch"
                found.append(f"{label}: `{pin.where}` takes {pin.piece} {took}, not {wanted}")
        carried = patches(manifest)
        for piece in sorted({pin.piece for pin in taken} & roots.keys()):
            found += [
                f"{label}: takes {piece} but does not carry its `[patch.{source}]` entry for {name} at {tags[piece]}"
                for (source, name), entry in patches(roots[piece]).items()
                if carried.get((source, name)) != entry
            ]
    return found


def notes(config: Config) -> str:
    """The notes of a London Project release: the release of each piece in the set.

    Args:
        config: The configuration whose set the notes list.
    """
    rows = [
        f"| [{piece.title}](https://github.com/{piece.repository}) "
        f"| [{piece.tag}](https://github.com/{piece.repository}/releases/tag/{piece.tag}) |"
        for piece in config.pieces
        if piece.tag
    ]
    return "The London Project's examples build against these releases.\n\n| Piece | Release |\n|---|---|\n" + "".join(
        f"{row}\n" for row in rows
    )


class ManifestSource(Protocol):
    """Reads a file from a repository at a tag. `GhManifests` implements the protocol over the `gh` CLI."""

    async def read(self, repository: str, tag: str, path: str, /) -> str:
        """The text of `path` in `repository` at `tag`.

        Args:
            repository: The repository as `owner/name`.
            tag: The tag.
            path: The file's path in the repository.
        """
        ...


class GhManifests:  # pylint: disable=too-few-public-methods  # a ManifestSource has one method
    """`ManifestSource` through the GitHub contents API and the `gh` CLI's sign-in."""

    async def read(self, repository: str, tag: str, path: str) -> str:
        """The text of `path` in `repository` at `tag`, read with `gh api`.

        Args:
            repository: The repository as `owner/name`.
            tag: The tag.
            path: The file's path in the repository.
        """
        _, out = await process.run(
            ["gh", "api", "-H", "Accept: application/vnd.github.raw", f"repos/{repository}/contents/{path}?ref={tag}"]
        )
        return out


async def check(config: Config, examples: Mapping[str, str], source: ManifestSource) -> list[str]:
    """Where the set's releases, and the examples, do not compose with the set.

    Args:
        config: The configuration whose set is checked.
        examples: The text of each example manifest, keyed by its path in the London Project.
        source: Where each piece's root manifest at its tag is read from.
    """
    released = [piece for piece in config.pieces if piece.tag]
    texts = {piece.name: await source.read(piece.repository, piece.tag or "", "Cargo.toml") for piece in released}
    roots = {name: tomllib.loads(text) for name, text in texts.items()}
    manifests: dict[str, Manifest] = {f"{piece.name} {piece.tag} Cargo.toml": roots[piece.name] for piece in released}
    manifests |= {path: tomllib.loads(text) for path, text in examples.items()}
    return problems(
        {piece.name: piece.tag or "" for piece in released},
        {piece.repository.lower(): piece.name for piece in config.pieces},
        manifests,
        roots,
    )
