"""The wheel must carry the page; a test is the only thing that keeps that true.

`pip install .` cannot be exercised here (no setuptools offline), so the two
facts that decide whether the page ships are asserted directly: the package-data
pattern is declared, and the file it matches exists.
"""
from __future__ import annotations

import unittest
from pathlib import Path

try:  # 3.11+; the package still supports 3.10, where this file is skipped
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


class PackagingTests(unittest.TestCase):
    def test_the_page_is_declared_as_package_data(self) -> None:
        if tomllib is None:
            self.skipTest("tomllib needs Python 3.11+")
        data = tomllib.loads(PYPROJECT.read_text())
        declared = data["tool"]["setuptools"]["package-data"]["worktree_board"]
        self.assertIn("static/*.html", declared)

    def test_the_pages_the_pattern_promises_exist(self) -> None:
        from worktree_board.server import INDEX
        self.assertTrue(INDEX.is_file(), f"{INDEX} is listed as package data but missing")

    def test_the_console_script_points_at_main(self) -> None:
        if tomllib is None:
            self.skipTest("tomllib needs Python 3.11+")
        data = tomllib.loads(PYPROJECT.read_text())
        self.assertEqual(data["project"]["scripts"]["wtree"], "worktree_board.cli:main")

    def test_no_byte_compiled_files_are_tracked(self) -> None:
        # A stray .pyc in the tree is a review hazard and a merge conflict magnet.
        import subprocess
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        tracked = [line for line in out.stdout.splitlines() if line.endswith(".pyc")]
        self.assertEqual(tracked, [])


if __name__ == "__main__":
    unittest.main()
