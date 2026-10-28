"""Check the example's dependencies against the London Project manifest."""

import json
from pathlib import Path
import subprocess
import tomllib
import unittest


EXAMPLE = Path(__file__).resolve().parents[1]
PIECES = ("clerkenwell", "walthamstow", "clapham")


def piece_sources():
    """The git source of each piece's release named in london.toml, by piece."""
    pieces = tomllib.loads((EXAMPLE.parents[1] / "london.toml").read_text())["piece"]
    return {
        name: f"git+https://github.com/{pieces[name]['repository']}?tag={pieces[name]['tag']}"
        for name in PIECES
    }


class ReleaseContract(unittest.TestCase):
    def test_each_piece_is_taken_from_the_declared_release(self):
        sources = piece_sources()
        metadata = json.loads(subprocess.check_output(
            ["cargo", "metadata", "--no-deps", "--locked", "--offline", "--format-version", "1"],
            cwd=EXAMPLE,
            text=True,
        ))
        app = next(package for package in metadata["packages"] if package["name"] == "micro")
        taken = set()
        for dependency in app["dependencies"]:
            piece = dependency["name"].split("-")[0]
            if piece in sources:
                with self.subTest(dependency=dependency["name"]):
                    self.assertEqual(dependency["source"], sources[piece])
                taken.add(piece)
        self.assertEqual(taken, set(PIECES))

    def test_the_lockfile_resolves_every_piece_to_its_release_and_leaves_out_battersea(self):
        sources = piece_sources()
        packages = tomllib.loads((EXAMPLE / "Cargo.lock").read_text())["package"]
        for package in packages:
            with self.subTest(package=package["name"]):
                self.assertFalse(package["name"].startswith("battersea"))
                piece = package["name"].split("-")[0]
                if piece in sources:
                    self.assertTrue(package["source"].startswith(sources[piece] + "#"), package["source"])

    def test_the_lockfile_takes_generic_btree_from_the_override(self):
        manifest = tomllib.loads((EXAMPLE / "Cargo.toml").read_text())
        patch = manifest["patch"]["crates-io"]["generic-btree"]
        packages = tomllib.loads((EXAMPLE / "Cargo.lock").read_text())["package"]
        btree = [package for package in packages if package["name"] == "generic-btree"]
        self.assertEqual(len(btree), 1, btree)
        self.assertEqual(btree[0]["source"], f"git+{patch['git']}?rev={patch['rev']}#{patch['rev']}")


if __name__ == "__main__":
    unittest.main()
