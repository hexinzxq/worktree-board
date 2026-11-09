"""`wtree` — the command-line half of the board.

Two verbs only: `ls` asks git and prints a table (or JSON), `serve` puts the
same report behind a page. Nothing here writes to the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path

from .scan import GitError, report
from .server import serve


def _width(text: str) -> int:
    """Terminal cells a string occupies — CJK characters take two, not one."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _cell(text: str, width: int) -> str:
    """Cut a string to at most `width` terminal cells, keeping the ellipsis."""
    if _width(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        step = _width(ch)
        if used + step > max(width - 1, 1):
            break
        out.append(ch)
        used += step
    return "".join(out) + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * max(width - _width(text), 0)


def _table(data: dict) -> str:
    rows = []
    header = ("BRANCH", "DIRTY", "AHEAD", "BEHIND", "LAST COMMIT", "PATH")
    widths = (28, 5, 5, 6, 46, 0)
    for tree in data["worktrees"]:
        branch = tree["branch"] or f"(detached {tree['head']})"
        if tree["main"]:
            branch += " *"
        if tree["prunable"]:
            branch += " (prunable)"
        if tree["locked"]:
            branch += " (locked)"
        rows.append((
            _cell(branch, widths[0]),
            "-" if tree["dirty"] < 0 else str(tree["dirty"]),
            "-" if tree["ahead"] is None else str(tree["ahead"]),
            "gone" if tree.get("upstream_gone") else ("-" if tree["behind"] is None else str(tree["behind"])),
            _cell(tree["subject"], widths[4]),
            tree["path"],
        ))
    table = ["  ".join(_pad(header[i], widths[i]) if widths[i] else header[i]
                       for i in range(len(header)))]
    for row in rows:
        table.append("  ".join(_pad(cell, widths[i]) if widths[i] else cell
                               for i, cell in enumerate(row)))
    totals = data["totals"]
    table.append("")
    table.append(f"{totals['count']} worktree(s), {totals['dirty']} dirty, "
                 f"{totals['behind']} behind upstream   ({data['root']})")
    if totals["behind"]:
        newest = max((tree["fetched_at"] for tree in data["worktrees"] if tree["fetched_at"]), default="")
        since = f" (newest fetch {newest[:16].replace('T', ' ')})" if newest else ""
        table.append(f"note: behind is measured against the last fetch{since} — "
                     f"run `git fetch` for a fresher number")
    if any(tree.get("upstream_gone") for tree in data["worktrees"]):
        table.append("note: upstream branch missing (deleted on the remote, or not fetched yet) — "
                     "`git branch -u origin/<branch>` to re-point it")
    if totals.get("prunable"):
        table.append(f"note: {totals['prunable']} worktree(s) no longer exist on disk — "
                     f"run `git worktree prune` to forget them")
    return "\n".join(table)


def _watch(root: Path, interval: float) -> int:
    """Redraw the table until ctrl-c. Clearing first keeps the cursor at home."""
    try:
        while True:
            sys.stdout.write("\033[2J\033[H" + _table(report(root)) + "\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wtree", description="A read-only board over git worktrees.")
    sub = parser.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("ls", help="print every worktree of the repository")
    ls.add_argument("-C", "--root", default=".", metavar="PATH", help="any path inside the repository")
    ls.add_argument("--json", action="store_true", help="machine-readable output")
    ls.add_argument("--watch", action="store_true",
                    help="redraw in place until ctrl-c (for a terminal already open)")
    ls.add_argument("--interval", type=float, default=1.0, metavar="SECONDS",
                    help="seconds between redraws with --watch (default 1.0)")

    srv = sub.add_parser("serve", help="serve the board on localhost")
    srv.add_argument("-C", "--root", default=".", metavar="PATH", help="any path inside the repository")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    try:
        if args.command == "ls":
            if args.watch and args.json:
                print("error: --watch draws a table; drop --json", file=sys.stderr)
                return 2
            if args.watch and args.interval <= 0:
                print("error: --interval must be positive", file=sys.stderr)
                return 2
            if args.watch:
                return _watch(root, args.interval)
            data = report(root)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else _table(data))
            return 0
        try:
            httpd = serve(root, args.host, args.port)
        except OSError as error:
            reason = error.strerror or str(error)
            print(f"error: cannot listen on {args.host}:{args.port} — {reason}. "
                  f"Pass --port to pick another.", file=sys.stderr)
            return 2
        print(f"worktree-board  http://{args.host}:{httpd.server_address[1]}  ({root})")
        print("ctrl-c to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
        return 0
    except GitError as error:
        print(f"wtree: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
