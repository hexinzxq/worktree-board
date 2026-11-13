"""server.py is one JSON endpoint and one page; test it over a real socket."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from pathlib import Path

import worktree_board.server as server_module
from worktree_board.server import INDEX, serve

from .test_scan import RepoCase, git  # noqa: F401  (RepoCase builds the fixture)


class ServerTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        # No cache in these tests: they add worktrees after the server starts.
        self.httpd = serve(self.repo, "127.0.0.1", 0, ttl=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def get(self, path: str) -> tuple[int, str, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def test_a_install_without_the_page_says_so(self) -> None:
        # The page lives in package data; a wheel that lost it must explain
        # itself instead of dropping the connection.
        with tempfile.TemporaryDirectory() as tmp:
            server_module.INDEX = Path(tmp) / "index.html"
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.get("/")
                self.assertEqual(caught.exception.code, 500)
                body = json.loads(caught.exception.read())
                self.assertIn("reinstall", body["error"])
            finally:
                server_module.INDEX = INDEX

    def test_the_page_is_served_at_the_root(self) -> None:
        status, content_type, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"worktree-board", body)

    def test_the_page_shows_ages_not_raw_timestamps(self) -> None:
        # #13 taught the table to say "3d"; the page must not fall back to a
        # date that needs mental arithmetic.
        _, _, body = self.get("/")
        text = body.decode()
        self.assertIn("function age(", text)
        self.assertIn('title="${esc(tree.committed_at || \"\")}"', text)
        self.assertNotIn(".slice(0, 16).replace(\"T\", \" \")", text)

    def test_state_is_json_and_matches_the_scanner(self) -> None:
        self.add_worktree("feature-login")
        status, content_type, body = self.get("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(data["totals"]["count"], 2)
        self.assertEqual(data["totals"]["prunable"], 0)
        self.assertEqual(len(data["worktrees"]), 2)
        self.assertEqual(data["worktrees"][0]["branch"], "main")

    def test_unknown_paths_are_404_json(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/nope")
        self.assertEqual(caught.exception.code, 404)
        data = json.loads(caught.exception.read())
        caught.exception.close()
        self.assertEqual(data["error"], "not found")

    def test_no_upstream_serialises_as_null(self) -> None:
        status, _, body = self.get("/api/state")
        self.assertEqual(status, 200)
        tree = json.loads(body)["worktrees"][0]
        self.assertIsNone(tree["ahead"])
        self.assertIsNone(tree["behind"])


class PageTests(unittest.TestCase):
    def test_the_page_carries_the_freshness_caveat(self) -> None:
        # The table explains that `behind` is measured against the last fetch;
        # the page has to say the same thing or the two outputs disagree.
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("fetched_at", html)
        self.assertIn("最后一次 fetch", html)
        self.assertIn('id="fresh"', html)


if __name__ == "__main__":
    unittest.main()


class CacheTests(RepoCase):
    """The page polls every 5s; two tabs must not double the git calls."""

    def setUp(self) -> None:
        super().setUp()
        self.calls = 0
        self.real_report = server_module.report

        def counting(root):
            self.calls += 1
            return self.real_report(root)

        server_module.report = counting
        self.httpd = serve(self.repo, "127.0.0.1", 0, ttl=60.0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server_module.report = self.real_report
        super().tearDown()

    def get(self, path: str) -> bytes:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.read()

    def test_back_to_back_polls_share_one_scan(self) -> None:
        first = self.get("/api/state")
        second = self.get("/api/state")
        self.assertEqual(first, second)
        self.assertEqual(self.calls, 1)

    def test_concurrent_polls_wait_for_one_scan(self) -> None:
        threads = [threading.Thread(target=self.get, args=("/api/state",)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(self.calls, 1)

    def test_the_ttl_expires(self) -> None:
        self.get("/api/state")
        self.assertEqual(self.calls, 1)
        import worktree_board.server as mod
        # second server with a tiny ttl, on the same repo
        httpd = mod.serve(self.repo, "127.0.0.1", 0, ttl=0.01)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
                r.read()
            import time as _time
            _time.sleep(0.05)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
                r.read()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
        self.assertEqual(self.calls, 3)
