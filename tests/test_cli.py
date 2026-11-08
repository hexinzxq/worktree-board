"""cli.py is the part a person actually types; keep its output honest."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from worktree_board.cli import main

from .test_scan import RepoCase

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

    def test_watch_refuses_json_and_a_nonpositive_interval(self) -> None:
        code, out = run("ls", "-C", str(self.repo), "--watch", "--json")
        self.assertEqual(code, 2)
        self.assertIn("drop --json", out)
        code, out = run("ls", "-C", str(self.repo), "--watch", "--interval", "0")
        self.assertEqual(code, 2)
        self.assertIn("must be positive", out)

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
