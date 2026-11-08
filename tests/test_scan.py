"""scan.py talks to git; these tests build a real repository to talk to."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktree_board.scan import GitError, report, scan


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()


class RepoCase(unittest.TestCase):
    """A repository with one commit and a sibling scratch directory for worktrees."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "app"
        self.repo.mkdir()
        git("init", "-b", "main", cwd=self.repo)
        git("config", "user.email", "t@example.com", cwd=self.repo)
        git("config", "user.name", "t", cwd=self.repo)
        (self.repo / "README.md").write_text("hello\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-m", "initial import", cwd=self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add_worktree(self, name: str, branch: str | None = None) -> Path:
        path = self.base / name
        if branch is None:
            git("worktree", "add", "-b", name, str(path), cwd=self.repo)
        else:
            git("worktree", "add", str(path), branch, cwd=self.repo)
        return path


class FreshRepoCase(unittest.TestCase):
    """`git init` and nothing else: the state every repository starts in."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "fresh"
        self.repo.mkdir()
        git("init", "-b", "main", cwd=self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class ScanTests(RepoCase):
    def test_the_main_worktree_comes_first_and_is_clean(self) -> None:
        trees = scan(self.repo)
        self.assertEqual(len(trees), 1)
        self.assertTrue(trees[0].main)
        self.assertEqual(trees[0].branch, "main")
        self.assertEqual(trees[0].dirty, 0)
        self.assertEqual(trees[0].subject, "initial import")
        self.assertEqual(trees[0].ahead, None)  # no upstream in a fresh repo
        self.assertEqual(trees[0].behind, None)

    def test_a_linked_worktree_is_listed_with_its_branch(self) -> None:
        path = self.add_worktree("feature-login")
        trees = scan(self.repo)
        self.assertEqual([tree.branch for tree in trees], ["main", "feature-login"])
        self.assertFalse(trees[1].main)
        self.assertEqual(Path(trees[1].path).resolve(), path.resolve())
        self.assertEqual(trees[1].dirty, 0)

    def test_uncommitted_changes_are_counted(self) -> None:
        path = self.add_worktree("feature-login")
        (path / "new.txt").write_text("x\n")
        (path / "README.md").write_text("hello again\n")
        trees = scan(self.repo)
        self.assertEqual(trees[1].dirty, 2)
        self.assertEqual(trees[0].dirty, 0)

    def test_ahead_and_behind_come_from_the_upstream(self) -> None:
        upstream = self.base / "upstream"
        upstream.mkdir()
        git("init", "-b", "main", "--bare", cwd=upstream)
        git("remote", "add", "origin", str(upstream), cwd=self.repo)
        git("push", "-u", "origin", "main", cwd=self.repo)
        path = self.add_worktree("feature-login")
        git("push", "-u", "origin", "feature-login", cwd=path)
        (path / "work.txt").write_text("local\n")
        git("add", ".", cwd=path)
        git("commit", "-m", "local work", cwd=path)
        trees = {tree.branch: tree for tree in scan(self.repo)}
        self.assertEqual(trees["feature-login"].ahead, 1)
        self.assertEqual(trees["feature-login"].behind, 0)
        self.assertEqual(trees["feature-login"].subject, "local work")

    def test_a_worktree_deleted_from_disk_is_prunable_not_healthy(self) -> None:
        # `git worktree list` still shows it; the directory is gone. Calling that
        # "clean" is the one lie the table must not tell.
        path = self.add_worktree("gone")
        shutil.rmtree(path)
        trees = {tree.branch: tree for tree in scan(self.repo)}
        self.assertTrue(trees["gone"].prunable)
        self.assertEqual(trees["gone"].dirty, -1)
        self.assertEqual(report(self.repo)["totals"]["prunable"], 1)

    def test_a_path_outside_a_repository_is_a_git_error(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(GitError):
                scan(Path(outside))

    def test_report_counts_are_consistent(self) -> None:
        path = self.add_worktree("feature-login")
        (path / "dirty.txt").write_text("x\n")
        data = report(self.repo)
        self.assertEqual(data["totals"]["count"], 2)
        self.assertEqual(data["totals"]["dirty"], 1)
        self.assertEqual(data["totals"]["behind"], 0)
        self.assertEqual(len(data["worktrees"]), 2)
        self.assertIn("root", data)


class FreshRepoScanTests(FreshRepoCase):
    def test_a_repository_without_commits_says_so(self) -> None:
        trees = scan(self.repo)
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0].branch, "main")
        self.assertEqual(trees[0].head, "")  # not a row of zeroes
        self.assertEqual(trees[0].subject, "(no commits yet)")
        self.assertEqual(trees[0].committed_at, "")
        self.assertEqual(trees[0].dirty, 0)

    def test_the_table_does_not_leave_the_cell_blank(self) -> None:
        from worktree_board.cli import _table
        out = _table(report(self.repo))
        self.assertIn("(no commits yet)", out)
        self.assertNotIn("000000000000", out)


if __name__ == "__main__":
    unittest.main()
