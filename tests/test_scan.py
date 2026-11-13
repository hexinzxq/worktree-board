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

    def test_behind_is_stamped_with_the_last_fetch(self) -> None:
        # "0 behind" on a clone nobody fetched for a week is true but useless;
        # the report has to carry the date the number was taken.
        upstream = self.base / "upstream"
        upstream.mkdir()
        git("init", "-b", "main", "--bare", cwd=upstream)
        git("remote", "add", "origin", str(upstream), cwd=self.repo)
        git("push", "-u", "origin", "main", cwd=self.repo)
        other = self.base / "other"
        git("clone", str(upstream), str(other), cwd=self.base)
        git("config", "user.email", "o@example.com", cwd=other)
        git("config", "user.name", "o", cwd=other)
        (other / "theirs.txt").write_text("x\n")
        git("add", ".", cwd=other)
        git("commit", "-m", "their work", cwd=other)
        git("push", "origin", "main", cwd=other)
        git("fetch", "origin", cwd=self.repo)
        tree = scan(self.repo)[0]
        self.assertEqual(tree.behind, 1)
        self.assertNotEqual(tree.fetched_at, "")
        self.assertIn("T", tree.fetched_at)  # ISO 8601, not the raw epoch

    def test_a_worktree_deleted_from_disk_is_prunable_not_healthy(self) -> None:
        # `git worktree list` still shows it; the directory is gone. Calling that
        # "clean" is the one lie the table must not tell.
        path = self.add_worktree("gone")
        shutil.rmtree(path)
        trees = {tree.branch: tree for tree in scan(self.repo)}
        self.assertTrue(trees["gone"].prunable)
        self.assertEqual(trees["gone"].dirty, -1)
        self.assertEqual(report(self.repo)["totals"]["prunable"], 1)
        # The commit survives the checkout: it is in the object database, and
        # "what was this worktree working on" is the question the row must still
        # answer.
        self.assertEqual(trees["gone"].subject, "initial import")
        self.assertNotEqual(trees["gone"].committed_at, "")

    def test_json_keeps_the_whole_commit_id(self) -> None:
        # Truncation belongs to the renderer; a machine reading --json needs the
        # id it can hand to `git show`.
        import json
        from worktree_board.scan import report
        data = json.loads(json.dumps(report(self.repo)))
        head = data["worktrees"][0]["head"]
        self.assertEqual(len(head), 40)
        self.assertTrue(all(char in "0123456789abcdef" for char in head))

    def test_a_missing_path_is_a_git_error_not_a_traceback(self) -> None:
        from worktree_board.scan import GitError, repo_root, survey
        with tempfile.TemporaryDirectory() as tmp:
            ghost = Path(tmp) / "gone"
            with self.assertRaises(GitError) as caught:
                repo_root(ghost)
            self.assertIn("does not exist", str(caught.exception))
            with self.assertRaises(GitError):
                survey(ghost)

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
    def test_a_hung_git_call_becomes_a_sentence_not_a_wait(self) -> None:
        import subprocess
        from unittest import mock
        from worktree_board.scan import GitError, scan
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired(["git"], 10.0)
        ):
            with self.assertRaises(GitError) as caught:
                scan(self.repo)
        message = str(caught.exception)
        self.assertIn("did not finish within", message)
        self.assertIn("WTREE_GIT_TIMEOUT", message)

    def test_a_missing_git_binary_says_so(self) -> None:
        from pathlib import Path
        from unittest import mock

        from worktree_board.scan import GitError, _git
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(GitError) as caught:
                _git(["status"], Path("."))
        self.assertIn("git is not installed", str(caught.exception))

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


class GoneUpstreamTests(RepoCase):
    def test_a_branch_whose_upstream_was_deleted_says_so(self) -> None:
        # push, delete on the remote, prune: the config still names an upstream
        # that no longer exists, and "0 behind" would be a lie.
        upstream = self.base / "upstream"
        upstream.mkdir()
        git("init", "-q", "--bare", cwd=upstream)
        git("remote", "add", "origin", str(upstream), cwd=self.repo)
        git("push", "-qu", "origin", "main", cwd=self.repo)
        git("checkout", "-qb", "feature", cwd=self.repo)
        (self.repo / "new.txt").write_text("x\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "work", cwd=self.repo)
        git("push", "-qu", "origin", "feature", cwd=self.repo)
        git("push", "origin", "--delete", "feature", cwd=self.repo)
        git("fetch", "--prune", "origin", cwd=self.repo)
        tree = scan(self.repo)[0]
        self.assertTrue(tree.upstream_gone)
        self.assertIsNone(tree.behind)
        self.assertIsNone(tree.ahead)
        self.assertEqual(report(self.repo)["totals"]["upstream_gone"], 1)


class FindReposTests(RepoCase):
    def test_only_repositories_with_two_worktrees_are_reported(self) -> None:
        from worktree_board.scan import find_repos
        git("worktree", "add", "-q", str(self.base / "wt"), "-b", "feat", cwd=self.repo)
        lonely = self.base / "lonely"
        lonely.mkdir()
        git("init", "-q", "-b", "main", cwd=lonely)
        git("config", "user.email", "t@example.com", cwd=lonely)
        git("config", "user.name", "t", cwd=lonely)
        (lonely / "a").write_text("x")
        git("add", ".", cwd=lonely)
        git("commit", "-qm", "one", cwd=lonely)

        found = find_repos(self.base)
        self.assertEqual([item["root"] for item in found], [str(self.repo.resolve())])
        self.assertEqual(found[0]["count"], 2)

        everything = find_repos(self.base, min_worktrees=1)
        self.assertEqual(len(everything), 2)

    def test_starting_inside_a_linked_worktree_still_finds_its_repository(self) -> None:
        # `wtree find -C .` from inside a checkout is the obvious invocation, and
        # that checkout's `.git` is a file.
        from worktree_board.scan import survey
        wt = self.add_worktree("feat")
        data = survey(wt, min_worktrees=2)
        self.assertEqual(len(data["repos"]), 1)
        self.assertEqual(data["repos"][0]["count"], 2)

    def test_the_walk_does_not_descend_into_a_repository(self) -> None:
        from worktree_board.scan import find_repos
        git("worktree", "add", "-q", str(self.base / "wt"), "-b", "feat", cwd=self.repo)
        inside = self.repo / "vendor" / "nested"
        inside.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=inside)
        # The inner repository is the outer one's business: one row, not two.
        self.assertEqual(len(find_repos(self.base, min_worktrees=1)), 1)


class SurveyCoverageTests(RepoCase):
    def test_a_swallowed_directory_is_counted(self) -> None:
        from worktree_board.scan import survey
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a" / "b" / "c").mkdir(parents=True)
            self.assertEqual(survey(base, 1, max_depth=1)["depth_cut"], 1)
            self.assertEqual(survey(base, 1, max_depth=6)["depth_cut"], 0)


class RepoRootErrorTests(FreshRepoCase):
    """Three ways rev-parse says no, three sentences that say something."""

    def test_a_plain_directory_is_not_a_repository(self) -> None:
        from worktree_board.scan import GitError, repo_root
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        with self.assertRaises(GitError) as caught:
            repo_root(plain)
        self.assertIn("is not inside a git repository", str(caught.exception))

    def test_a_bare_repository_is_a_repository_not_an_error(self) -> None:
        from worktree_board.scan import repo_root
        bare = Path(self.tmp.name) / "bare.git"
        bare.mkdir()
        git("init", "-q", "--bare", cwd=bare)
        self.assertEqual(repo_root(bare).resolve(), bare.resolve())

    def test_a_bare_repository_can_own_worktrees(self) -> None:
        # `git worktree add` works from a bare repo, and the board has to show
        # the linked checkout without calling the bare entry "main".
        bare = Path(self.tmp.name) / "hosting.git"
        bare.mkdir()
        git("init", "-q", "--bare", cwd=bare)
        git("config", "user.email", "t@example.com", cwd=self.repo)
        git("config", "user.name", "t", cwd=self.repo)
        (self.repo / "a.txt").write_text("x\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "one", cwd=self.repo)
        git("push", "-q", str(bare), "main", cwd=self.repo)
        checkout = Path(self.tmp.name) / "site"
        git("worktree", "add", "-q", str(checkout), "main", cwd=bare)
        trees = scan(bare)
        self.assertEqual(len(trees), 2)
        self.assertTrue(trees[0].bare)
        self.assertFalse(trees[0].main)
        self.assertFalse(trees[1].bare)
        self.assertEqual(trees[1].branch, "main")

    def test_the_git_directory_is_not_the_working_tree(self) -> None:
        from worktree_board.scan import repo_root
        with self.assertRaises(Exception) as caught:
            repo_root(self.repo / ".git")
        self.assertIn("git directory", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
