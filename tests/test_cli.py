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


class LsTests(RepoCase):
    def test_the_table_names_the_columns_and_the_totals(self) -> None:
        code, out = run("ls", "-C", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("BRANCH", out)
        self.assertIn("LAST COMMIT", out)
        self.assertIn("1 worktree(s)", out)
        self.assertIn("main *", out)

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
        self.assertIn("main *", out)


if __name__ == "__main__":
    unittest.main()
