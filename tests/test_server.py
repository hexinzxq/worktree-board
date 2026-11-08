"""server.py is one JSON endpoint and one page; test it over a real socket."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from worktree_board.server import serve

from .test_scan import RepoCase, git  # noqa: F401  (RepoCase builds the fixture)


class ServerTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.httpd = serve(self.repo, "127.0.0.1", 0)
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

    def test_the_page_is_served_at_the_root(self) -> None:
        status, content_type, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"worktree-board", body)

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


if __name__ == "__main__":
    unittest.main()
