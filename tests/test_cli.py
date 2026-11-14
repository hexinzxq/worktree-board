"""cli.py is the part a person actually types; keep its output honest."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from worktree_board.cli import main

from .test_scan import RepoCase, git

ROOT = Path(__file__).resolve().parents[1]


def run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


class DetachedRowTests(unittest.TestCase):
    def test_a_detached_worktree_shows_a_short_sha(self) -> None:
        from worktree_board.cli import _table
        sha = "2913c94ed0ae9c7bf700b1331449c81ec6c5404a"
        out = _table({
            "root": "/tmp/r",
            "totals": {"count": 1, "dirty": 0, "behind": 0},
            "worktrees": [{"branch": None, "main": False, "head": sha, "dirty": 0,
                           "ahead": None, "behind": None, "subject": "one", "path": "/tmp/wt",
                           "committed_at": "2026-11-10T00:00:00+00:00",
                           "prunable": False, "locked": False}],
        })
        self.assertIn("(detached 2913c94)", out)
        self.assertNotIn(sha, out)


class SortTests(unittest.TestCase):
    def payload(self) -> dict:
        def tree(branch, dirty, behind, when):
            return {"branch": branch, "main": False, "head": "abc", "dirty": dirty,
                    "ahead": 0, "behind": behind, "subject": "s", "path": f"/tmp/{branch}",
                    "committed_at": when, "prunable": False, "locked": False,
                    "upstream_gone": False}
        return {"root": "/tmp/r", "totals": {"count": 3, "dirty": 0, "behind": 0},
                "worktrees": [tree("zeta", 1, 0, "2026-11-05T00:00:00+00:00"),
                              tree("alpha", 0, 7, "2026-11-10T00:00:00+00:00"),
                              tree("mid", 3, 2, "2026-11-01T00:00:00+00:00")]}

    def order(self, sort):
        from worktree_board.cli import _sorted
        return [tree["branch"] for tree in _sorted(self.payload(), sort)]

    def test_orders(self) -> None:
        self.assertEqual(self.order(None), ["zeta", "alpha", "mid"])
        self.assertEqual(self.order("branch"), ["alpha", "mid", "zeta"])
        self.assertEqual(self.order("dirty"), ["mid", "zeta", "alpha"])
        self.assertEqual(self.order("behind"), ["alpha", "mid", "zeta"])
        self.assertEqual(self.order("age"), ["mid", "zeta", "alpha"])

    def test_age_order_compares_instants_not_strings(self) -> None:
        # A +08:00 commit one hour old must precede a +00:00 commit one day old,
        # even though "2026-11-12T09:00:00+08:00" < "2026-11-11T09:00:00+00:00"
        from worktree_board.cli import _sorted
        def tree(branch, when):
            return {"branch": branch, "main": False, "head": "abc", "dirty": 0,
                    "ahead": 0, "behind": 0, "subject": "s", "path": f"/tmp/{branch}",
                    "committed_at": when, "prunable": False, "locked": False}
        data = {"root": "/tmp/r", "totals": {}, "worktrees": [
            # 02:00 UTC is 10:00 +08:00 — the opposite of the string order.
            tree("later", "2026-11-12T02:00:00+00:00"),
            tree("earlier", "2026-11-12T09:00:00+08:00"),
        ]}
        self.assertEqual([t["branch"] for t in _sorted(data, "age")], ["earlier", "later"])

    def test_watch_survives_a_repository_that_is_briefly_unreadable(self) -> None:
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from worktree_board.cli import _watch
        from worktree_board.scan import GitError

        calls = [GitError("git status did not finish within 10s"), KeyboardInterrupt()]
        with mock.patch("worktree_board.cli.report", side_effect=calls), \
                mock.patch("worktree_board.cli.time.sleep"):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                # `report` is mocked, so the root is never touched.
                self.assertEqual(_watch(Path("."), 5.0), 0)
        self.assertIn("did not finish within 10s", buffer.getvalue())

    def test_a_bad_order_is_a_usage_error(self) -> None:
        import io as _io
        from contextlib import redirect_stderr
        from worktree_board.cli import main
        with redirect_stderr(_io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(["ls", "--sort", "banana"])
        self.assertEqual(caught.exception.code, 2)


class FindOutputTests(unittest.TestCase):
    def test_an_empty_survey_says_how_many_it_looked_at(self) -> None:
        import io as _io
        from contextlib import redirect_stdout
        from worktree_board.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            buf = _io.StringIO()
            with redirect_stdout(buf):
                code = main(["find", "-C", tmp])
            self.assertEqual(code, 0)
            self.assertIn("(looked at 0 checkouts)", buf.getvalue())


class SkipNoteTests(unittest.TestCase):
    def test_a_broken_repository_is_named_not_silently_dropped(self) -> None:
        import io as _io
        from contextlib import redirect_stderr, redirect_stdout

        from worktree_board.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken"
            (broken / ".git").mkdir(parents=True)
            (broken / ".git" / "HEAD").write_text("garbage\n")
            out, err = _io.StringIO(), _io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["find", "-C", tmp, "--depth", "2"])
            self.assertEqual(code, 0)
            self.assertIn("note: skipped", err.getvalue())
            self.assertIn("broken", err.getvalue())


class AgeTests(unittest.TestCase):
    def test_age_units(self) -> None:
        from datetime import datetime, timedelta, timezone
        from worktree_board.cli import _age
        now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        iso = lambda **kw: (now - timedelta(**kw)).isoformat()
        self.assertEqual(_age(iso(seconds=10), now), "just now")
        self.assertEqual(_age(iso(minutes=5), now), "5m")
        self.assertEqual(_age(iso(hours=3), now), "3h")
        self.assertEqual(_age(iso(days=2), now), "2d")
        self.assertEqual(_age("", now), "?")

    def test_the_table_says_how_old_each_commit_is(self) -> None:
        from worktree_board.cli import _table
        out = _table({
            "root": "/tmp/r",
            "totals": {"count": 1, "dirty": 0, "behind": 0},
            "worktrees": [{"branch": "main", "main": True, "head": "abc", "dirty": 0,
                           "ahead": 0, "behind": 0, "subject": "one", "path": "/tmp/r",
                           "committed_at": "2020-01-01T00:00:00+00:00",
                           "prunable": False, "locked": False}],
        })
        self.assertIn("AGE", out)
        self.assertRegex(out, r"\b(\d+[dh]|just now)\b")


class BranchNameTests(unittest.TestCase):
    def test_two_long_branches_still_look_different(self) -> None:
        # Session branches share the date prefix and differ at the end; cutting
        # them from the right made two rows identical.
        from worktree_board.cli import _cell
        a = _cell("claude/session-2026-11-10-review-fix-flaky-test", 28, keep_tail=True)
        b = _cell("claude/session-2026-11-10-review-fix-docs", 28, keep_tail=True)
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith("flaky-test"))
        self.assertTrue(a.startswith("claude/"))

    def test_a_short_branch_is_left_alone(self) -> None:
        from worktree_board.cli import _cell
        self.assertEqual(_cell("main", 28, keep_tail=True), "main")

    def test_the_home_prefix_collapses_to_a_tilde(self) -> None:
        from pathlib import Path as P
        from worktree_board.cli import _short_path
        self.assertEqual(_short_path("/Users/x/proj/a", home=P("/Users/x")), "~/proj/a")
        self.assertEqual(_short_path("/tmp/x", home=P("/Users/x")), "/tmp/x")


class StarTests(unittest.TestCase):
    """The star must mean "you are here", not "this happens to be main"."""

    def data(self, main_path, other_path):
        def tree(path, main):
            return {"path": path, "branch": "main" if main else "feat", "head": "abc",
                    "main": main, "locked": False, "prunable": False, "dirty": 0,
                    "ahead": None, "behind": None, "subject": "x", "committed_at": "",
                    "fetched_at": "", "upstream_gone": False, "bare": False}
        return {"root": "/tmp/r", "worktrees": [tree(main_path, True), tree(other_path, False)],
                "totals": {"count": 2, "dirty": 0, "behind": 0, "prunable": 0,
                           "locked": 0, "upstream_gone": 0}}

    def test_the_star_follows_the_shell_not_main(self) -> None:
        from worktree_board.cli import _table
        data = self.data("/tmp/r/repo", "/tmp/r/wt")
        table = _table(data, None, Path("/tmp/r/wt"))
        lines = {line.split()[0]: line for line in table.splitlines() if line.startswith(("main", "feat"))}
        self.assertIn("feat", lines["feat"])
        self.assertTrue(lines["feat"].rstrip().endswith("*") or lines["feat"].split()[1] == "*")
        self.assertNotIn("feat *", lines["main"])

    def test_main_is_named_even_without_a_star(self) -> None:
        from worktree_board.cli import _table
        table = _table(self.data("/tmp/r/repo", "/tmp/r/wt"), None, Path("/elsewhere"))
        report = [line for line in table.splitlines() if "(main)" in line]
        self.assertEqual(len(report), 1)
        self.assertIn("/tmp/r/repo", report[0])

    def test_from_outside_every_worktree_nothing_is_starred(self) -> None:
        from worktree_board.cli import _table
        table = _table(self.data("/tmp/r/repo", "/tmp/r/wt"), None, Path("/"))
        self.assertNotIn("*", table)


class ControlCharTests(unittest.TestCase):
    def test_a_newline_in_a_path_does_not_wrap_the_row(self) -> None:
        from worktree_board.cli import _printable
        self.assertEqual(_printable("/tmp/line\nbreak"), "/tmp/line\\nbreak")
        self.assertEqual(_printable("a\tb"), "a\\tb")

    def test_a_path_with_a_newline_stays_one_row(self) -> None:
        # The parser now reads NUL-separated porcelain, and the table must not
        # let that newline break the row apart.
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from worktree_board.cli import _table
        data = {"root": "/tmp/r", "totals": {"count": 1, "dirty": 0, "behind": 0,
                                            "prunable": 0, "locked": 0, "upstream_gone": 0},
                "worktrees": [{"path": "/tmp/r/line\nbreak", "branch": "main", "head": "abc",
                               "main": True, "locked": False, "prunable": False, "dirty": 0,
                               "ahead": None, "behind": None, "subject": "x",
                               "committed_at": "", "fetched_at": "", "upstream_gone": False,
                               "bare": False}]}
        with redirect_stdout(io.StringIO()):
            table = _table(data)
        self.assertIn("/tmp/r/line\\nbreak", table)
        self.assertEqual(len([line for line in table.splitlines() if "main" in line or "line" in line]), 1)


class WideTextTests(unittest.TestCase):
    """CJK counts as two cells; a table that pads by len() leans right of the header."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "中文 仓库"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("config", "user.email", "a@b.c", cwd=self.repo)
        git("config", "user.name", "a", cwd=self.repo)
        (self.repo / "a.txt").write_text("x")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "修复看板里中文提交主题的对齐问题，别让表格歪", cwd=self.repo)
        git("worktree", "add", "-q", "../另一个 worktree", "-b", "feature", cwd=self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_every_row_starts_its_path_at_the_same_cell(self) -> None:
        from worktree_board.cli import _table, _width
        from worktree_board.scan import report
        out = _table(report(self.repo))
        starts = set()
        for line in out.splitlines():
            if "  /private/" not in line and "  /var/" not in line and "  /tmp/" not in line:
                continue
            prefix = line[: line.index("  /") + 2]
            starts.add(_width(prefix))
        self.assertEqual(len(starts), 1, f"path column drifts: {starts}")

    def test_cutting_a_wide_subject_keeps_the_cell_budget(self) -> None:
        from worktree_board.cli import _cell, _width
        cut = _cell("修复看板里中文提交主题的对齐问题，别让表格歪", 20)
        self.assertTrue(cut.endswith("…"))
        self.assertLessEqual(_width(cut), 20)


class LsTests(RepoCase):
    def test_the_table_names_the_columns_and_the_totals(self) -> None:
        code, out = run("ls", "-C", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("BRANCH", out)
        self.assertIn("LAST COMMIT", out)
        self.assertIn("1 worktree(s)", out)
        self.assertIn("main (main)", out)

    def test_json_is_the_same_report_the_server_serves(self) -> None:
        self.add_worktree("feature-login")
        code, out = run("ls", "-C", str(self.repo), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["totals"]["count"], 2)
        self.assertEqual(data["worktrees"][1]["branch"], "feature-login")

    def test_a_prunable_worktree_is_named_in_the_row_and_the_note(self) -> None:
        path = self.add_worktree("gone")
        shutil.rmtree(path)
        code, out = run("ls", "-C", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("gone (prunable)", out)
        self.assertIn("git worktree prune", out)
        self.assertEqual(out.count("note:"), 1)

    def test_watch_refuses_json_and_a_nonpositive_interval(self) -> None:
        code, out = run("ls", "-C", str(self.repo), "--watch", "--json")
        self.assertEqual(code, 2)
        self.assertIn("drop --json", out)
        code, out = run("ls", "-C", str(self.repo), "--watch", "--interval", "0")
        self.assertEqual(code, 2)
        self.assertIn("must be positive", out)

    def test_a_stale_behind_count_says_how_stale_it_is(self) -> None:
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
        code, out = run("ls", "-C", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("behind is measured against the last fetch", out)

    def test_a_deleted_upstream_reads_as_gone(self) -> None:
        upstream = self.base / "upstream2"
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
        code, out = run("ls", "-C", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("gone", out)
        self.assertIn("upstream branch missing", out)
        self.assertIn("1 upstream gone", out)  # the totals line must agree with the row

    def test_a_busy_port_is_a_message_not_a_traceback(self) -> None:
        # A dev server already running on 8765 is the normal way to meet this.
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        try:
            code, out = run("serve", "-C", str(self.repo), "--port", str(port))
        finally:
            holder.close()
        self.assertEqual(code, 2)
        self.assertIn("cannot listen on", out)
        self.assertIn(str(port), out)
        self.assertNotIn("Traceback", out)

    def test_watch_keeps_redrawing_until_it_is_stopped(self) -> None:
        # A terminal already open should not need a browser. Run the real CLI
        # for a moment and count the redraws: one clear sequence per frame.
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            [sys.executable, "-m", "worktree_board.cli", "ls", "-C", str(self.repo),
             "--watch", "--interval", "0.05"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        try:
            time.sleep(0.6)
        finally:
            process.terminate()
            out, _ = process.communicate(timeout=10)
        self.assertGreaterEqual(out.count("\033[2J"), 2)
        self.assertIn("main (main)", out)


if __name__ == "__main__":
    unittest.main()
