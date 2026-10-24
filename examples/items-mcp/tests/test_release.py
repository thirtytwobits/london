"""Check the example's release and dependency boundaries against the London Project manifest."""

import json
from pathlib import Path
import subprocess
import tomllib
import unittest


EXAMPLE = Path(__file__).resolve().parents[1]


class ReleaseContract(unittest.TestCase):
    def test_clapham_is_the_only_piece_and_uses_the_declared_release(self):
        pieces = tomllib.loads((EXAMPLE.parents[1] / "london.toml").read_text())["piece"]
        source = f"git+https://github.com/{pieces['clapham']['repository']}?tag={pieces['clapham']['tag']}"
        metadata = json.loads(subprocess.check_output(
            ["cargo", "metadata", "--no-deps", "--locked", "--offline", "--format-version", "1"],
            cwd=EXAMPLE,
            text=True,
        ))
        app = next(package for package in metadata["packages"] if package["name"] == "items-mcp")
        dependencies = {dep["name"]: dep for dep in app["dependencies"]}
        for name in ("clapham", "clapham-mcp-server"):
            self.assertEqual(dependencies[name]["source"], source)
        server = dependencies["clapham-mcp-server"]
        self.assertFalse(server["uses_default_features"])
        self.assertEqual(server["features"], ["stdio"])
        # The lockfile covers every target without needing their packages in Cargo's cache.
        packages = tomllib.loads((EXAMPLE / "Cargo.lock").read_text())["package"]
        for package in packages:
            with self.subTest(package=package["name"]):
                self.assertFalse(any(
                    piece in package["name"] for piece in ("clerkenwell", "walthamstow", "battersea")
                ))
                resolved = package.get("source")
                if resolved is None:
                    self.assertEqual(package["name"], "items-mcp")
                elif resolved.startswith("git+"):
                    self.assertTrue(resolved.startswith(source + "#"), resolved)


if __name__ == "__main__":
    unittest.main()
