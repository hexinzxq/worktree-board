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
from datetime import datetime, timezone
from pathlib import Path

from .scan import GitError, report, survey
from .server import serve


def _printable(text: str) -> str:
    """One table row must stay one line: spell control characters out."""
    out = []
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def _width(text: str) -> int:
    """Terminal cells a string occupies — CJK characters take two, not one."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _cut(text: str, cells: int, from_start: bool = True) -> str:
    """As many characters as fit in `cells`, from one end or the other."""
    chars = text if from_start else reversed(text)
    out, used = [], 0
    for ch in chars:
        step = _width(ch)
        if used + step > cells:
            break
        out.append(ch)
        used += step
    return "".join(out) if from_start else "".join(reversed(out))


def _cell(text: str, width: int, keep_tail: bool = False) -> str:
    """Cut a string to at most `width` terminal cells, keeping the ellipsis.

    Branch names share long prefixes (`claude/session-2026-11-10-…`) and differ
    at the end, so cutting them from the right hides the only part that tells
    two rows apart. Those keep both ends.
    """
    if _width(text) <= width:
        return text
    room = max(width - 1, 1)  # one cell for the ellipsis
    if not keep_tail:
        return _cut(text, room) + "…"
    head = room * 3 // 5
    tail = room - head
    return _cut(text, head) + "…" + _cut(text, tail, from_start=False)


def _age(iso: str, now: datetime | None = None) -> str:
    """How long ago a commit landed, in the units a glance needs."""
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return "?"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    seconds = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    if seconds < 90:
        return "just now"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return "just now"


def _short_path(path: str, home: Path | None = None) -> str:
    """`~` instead of the home prefix: four columns saved on every row."""
    home = home or Path.home()
    prefix = str(home).rstrip("/") + "/"
    return "~/" + path[len(prefix):] if path.startswith(prefix) else path


def _pad(text: str, width: int) -> str:
    return text + " " * max(width - _width(text), 0)


def _stamp(iso: str) -> float:
    """A comparable instant; unparsable or empty sorts as the oldest thing."""
    try:
        stamp = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return float("-inf")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _sorted(data: dict, order: str | None) -> list[dict]:
    """Rows in the order the question wants them.

    Default keeps git's order (main first), which answers "what is here".
    `age` answers "what is stale", `dirty` and `behind` answer "what is a mess".
    """
    trees = list(data["worktrees"])
    if order == "branch":
        return sorted(trees, key=lambda tree: (tree["branch"] or "", tree["path"]))
    if order == "path":
        return sorted(trees, key=lambda tree: tree["path"])
    if order == "age":
        # %cI keeps the commit's own offset, so comparing the strings would put
        # an hour-old +08:00 commit after a day-old +00:00 one.
        return sorted(trees, key=lambda tree: (_stamp(tree["committed_at"]), tree["path"]))
    if order == "dirty":
        return sorted(trees, key=lambda tree: (-max(tree["dirty"], 0), tree["path"]))
    if order == "behind":
        return sorted(trees, key=lambda tree: (-(tree["behind"] or 0), tree["path"]))
    return trees


def _inside(here: Path | None, path: str) -> bool:
    """Is `here` inside this worktree? Both sides are resolved first."""
    if here is None:
        return False
    try:
        here = Path(here).resolve()
        base = Path(path).resolve()
    except OSError:
        return False
    return here == base or base in here.parents


def _table(data: dict, order: str | None = None, here: Path | None = None) -> str:
    rows = []
    header = ("BRANCH", "DIRTY", "AHEAD", "BEHIND", "LAST COMMIT", "AGE", "PATH")
    widths = (28, 5, 5, 6, 46, 8, 0)
    for tree in _sorted(data, order):
        branch = tree["branch"] or ("(bare repository)" if tree.get("bare") else f"(detached {tree['head'][:7]})")
        if tree["main"]:  # a bare entry is never the main worktree
            branch += " (main)"
        if _inside(here, tree["path"]):  # the one your shell is standing in
            branch += " *"
        if tree["prunable"]:
            branch += " (prunable)"
        if tree["locked"]:
            branch += " (locked)"
        rows.append((
            _cell(_printable(branch), widths[0], keep_tail=True),
            "-" if tree["dirty"] < 0 else str(tree["dirty"]),
            "-" if tree["ahead"] is None else str(tree["ahead"]),
            "gone" if tree.get("upstream_gone") else ("-" if tree["behind"] is None else str(tree["behind"])),
            _cell(_printable(tree["subject"]), widths[4]),
            _age(tree["committed_at"]),
            _printable(_short_path(tree["path"])),
        ))
    table = ["  ".join(_pad(header[i], widths[i]) if widths[i] else header[i]
                       for i in range(len(header)))]
    for row in rows:
        table.append("  ".join(_pad(cell, widths[i]) if widths[i] else cell
                               for i, cell in enumerate(row)))
    totals = data["totals"]
    table.append("")
    gone = totals.get("upstream_gone", 0)
    table.append(f"{totals['count']} worktree(s), {totals['dirty']} dirty, "
                 f"{totals['behind']} behind upstream"
                 f"{f', {gone} upstream gone' if gone else ''}   ({_short_path(data['root'])})")
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


def _watch(root: Path, interval: float, order: str | None = None) -> int:
    """Redraw the table until ctrl-c. Clearing first keeps the cursor at home."""
    try:
        while True:
            try:
                body = _table(report(root), order, Path.cwd())
            except GitError as error:
                # A repository that is briefly unreadable (a worktree being
                # removed, a lock) must not end the watch — say it and go on.
                body = f"wtree: {error}"
            sys.stdout.write("\033[2J\033[H" + body + "\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _find(root: Path, minimum: int, depth: int, as_json: bool) -> int:
    data = survey(root, minimum, depth)
    repos = data["repos"]
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not repos:
        seen = data["examined"]
        print(f"no repository with {minimum}+ worktree(s) under {_short_path(str(root))} "
              f"(looked at {seen} checkout{'s' if seen != 1 else ''})")
    for repo in repos:
        print(f"{repo['count']:>3} worktree(s)  {_short_path(repo['root'])}")
    for miss in data.get("skipped", []):
        print(f"note: skipped {_printable(_short_path(miss['path']))} — "
              f"{_printable(miss['reason'])}", file=sys.stderr)
    if data["depth_cut"]:
        # "not found" and "did not look" must not read the same.
        print(f"note: stopped at {depth} level(s) in {data['depth_cut']} director"
              f"{'y' if data['depth_cut'] == 1 else 'ies'} — pass --depth to go deeper",
              file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wtree", description="A read-only board over git worktrees.")
    sub = parser.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("ls", help="print every worktree of the repository")
    ls.add_argument("-C", "--root", default=".", metavar="PATH", help="any path inside the repository")
    ls.add_argument("--json", action="store_true", help="machine-readable output")
    ls.add_argument("--sort", choices=("branch", "dirty", "behind", "age", "path"),
                    help="row order: branch, dirty (most first), behind (most first), "
                         "age (oldest first), path")
    ls.add_argument("--watch", action="store_true",
                    help="redraw in place until ctrl-c (for a terminal already open)")
    ls.add_argument("--interval", type=float, default=1.0, metavar="SECONDS",
                    help="seconds between redraws with --watch (default 1.0)")

    find = sub.add_parser("find", help="survey a directory for repositories that have worktrees")
    find.add_argument("-C", "--root", default=".", metavar="PATH", help="directory to walk")
    find.add_argument("--min", type=int, default=2, metavar="N",
                      help="only report repositories with at least N worktrees (default 2)")
    find.add_argument("--depth", type=int, default=6, metavar="N",
                      help="how many directory levels to walk (default 6)")
    find.add_argument("--json", action="store_true", help="machine-readable output")

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
                return _watch(root, args.interval, args.sort)
            data = report(root)
            print(json.dumps(data, ensure_ascii=False, indent=2)
                  if args.json else _table(data, args.sort, Path.cwd()))
            return 0
        if args.command == "find":
            return _find(root, max(args.min, 1), max(args.depth, 1), args.json)
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
