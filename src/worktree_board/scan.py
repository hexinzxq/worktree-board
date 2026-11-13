"""Read the worktrees of one repository as plain data.

Everything here shells out to `git` and parses porcelain output. No models, no
network, no state on disk: the same repository always yields the same report,
which is what makes it testable (see tests/test_scan.py).
"""
from __future__ import annotations

import math
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed where the scan needed it to succeed."""


DEFAULT_TIMEOUT = 10.0


def _timeout() -> float:
    """How long one git call may take; `WTREE_GIT_TIMEOUT=0` waits forever."""
    raw = os.environ.get("WTREE_GIT_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else math.inf


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    limit = _timeout()
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=None if limit == math.inf else limit,
        )
    except subprocess.TimeoutExpired:
        raise GitError(
            f"git {args[0]} did not finish within {limit:g}s in {cwd} — "
            f"the repository may be locked, on a slow mount, or waiting on a "
            f"credential prompt (raise WTREE_GIT_TIMEOUT to wait longer)"
        ) from None
    except FileNotFoundError:
        raise GitError("git is not installed — worktree-board reads worktrees by running it") from None


def _must(args: list[str], cwd: Path) -> str:
    done = _git(args, cwd)
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {done.stderr.strip()}")
    return done.stdout


@dataclass(frozen=True)
class Worktree:
    """One entry of `git worktree list`, plus what a person wants to see."""

    path: str
    branch: str | None  # None when detached
    head: str
    main: bool
    locked: bool
    prunable: bool
    dirty: int  # changed/untracked entries
    ahead: int | None  # None when the branch has no upstream
    behind: int | None
    subject: str  # last commit subject
    committed_at: str  # ISO 8601 of the last commit
    fetched_at: str  # ISO 8601 of the last fetch; "" when never fetched
    upstream_gone: bool = False  # the tracked branch was deleted on the remote
    bare: bool = False  # this entry is the bare repository itself, not a checkout

    def as_json(self) -> dict:
        return asdict(self)


def repo_root(path: Path) -> Path:
    """The repository that contains `path`: a working tree or a bare repo.

    A bare repository is a perfectly good answer — it can own worktrees even
    though it has none itself, and `git worktree list` works inside it. Only
    "not a repository at all" is an error, and its two flavours (a plain
    directory, a `.git` directory) get their own sentences.
    """
    if not path.exists():
        raise GitError(f"{path} does not exist — pass an existing path")
    if not path.is_dir():
        raise GitError(f"{path} is not a directory — pass the repository root")
    done = _git(["rev-parse", "--show-toplevel"], path)
    if done.returncode == 0:
        return Path(done.stdout.strip())
    bare = _git(["rev-parse", "--is-bare-repository"], path)
    if bare.returncode == 0 and bare.stdout.strip() == "true":
        git_dir = _git(["rev-parse", "--absolute-git-dir"], path)
        return Path(git_dir.stdout.strip()) if git_dir.returncode == 0 else path
    if path.name == ".git":
        raise GitError(f"{path} is a git directory, not a working tree — pass the repository root")
    raise GitError(f"{path} is not inside a git repository")


def _parse_list(porcelain: str) -> list[dict]:
    """Parse `git worktree list --porcelain -z`.

    NUL-separated on purpose: a checkout path may contain a newline, and the
    line-based form would cut such a path in two and report a directory that
    does not exist. Fields are `<key> <value>`; an empty record ends an entry.
    """
    entries: list[dict] = []
    current: dict | None = None

    def fresh(path: str) -> dict:
        return {"path": path, "branch": None, "head": "", "main": False,
                "locked": False, "prunable": False, "bare": False}

    for token in porcelain.split("\0"):
        if not token:
            if current is not None:
                entries.append(current)
                current = None
            continue
        key, _, value = token.partition(" ")
        if key == "worktree":
            if current is not None:
                entries.append(current)
            current = fresh(value)
        elif current is None:
            continue
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            ref = value
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif key == "detached":
            current["branch"] = None
        elif key == "locked":
            current["locked"] = True
        elif key == "prunable":
            current["prunable"] = True
        elif key == "bare":
            current["bare"] = True
    if current is not None:
        entries.append(current)
    return entries


def find_repos(base: Path, min_worktrees: int = 2, max_depth: int = 6) -> list[dict]:
    """Walk `base` for repositories that have at least `min_worktrees` worktrees."""
    return survey(base, min_worktrees, max_depth)["repos"]


def survey(base: Path, min_worktrees: int = 2, max_depth: int = 6) -> dict:
    """Like `find_repos`, but also say where the walk stopped.

    A checkout is a directory with a `.git` directory; a linked worktree (a
    `.git` file) is skipped because its owner reports it. Found checkouts are
    not descended into: their submodules and vendored copies belong to them,
    not to the survey. Directories the depth limit closed are counted, because
    "nothing found" and "did not look that deep" are different answers.
    """
    import os

    if not base.is_dir():
        raise GitError(f"{base} is not a directory — pass a directory to walk")
    found: list[dict] = []
    skipped: list[dict] = []
    examined = 0
    depth_cut = 0
    base = base.resolve()
    for current, dirs, files in os.walk(base):
        here = Path(current)
        depth = len(here.relative_to(base).parts)
        if depth >= max_depth:
            if dirs:
                depth_cut += 1
            dirs[:] = []
        if ".git" in files and depth:
            dirs[:] = []  # a linked worktree: its owner repo reports it
            continue
        if ".git" in files or ".git" in dirs:
            dirs[:] = []  # the checkout owns its own tree
            try:
                root = repo_root(here)
            except GitError as error:
                # Something here looks like a repository and git refuses it.
                # Dropping it silently would make "not found" and "not read"
                # the same answer, which is what this whole verb exists to fix.
                skipped.append({"path": str(here), "reason": str(error)})
                continue
            count = len(scan(root))
            examined += 1
            if count >= min_worktrees:
                found.append({"root": str(root), "count": count})
    found.sort(key=lambda item: (-item["count"], item["root"]))
    return {"root": str(base), "repos": found, "examined": examined,
            "depth_cut": depth_cut, "skipped": skipped}


def _dirty_count(path: Path) -> int:
    """Count changed and untracked entries; a missing directory counts as all-dirty."""
    if not path.is_dir():
        return -1
    out = _git(["status", "--porcelain"], path)
    if out.returncode != 0:
        return -1
    return len([line for line in out.stdout.splitlines() if line.strip()])


def _ahead_behind(path: Path, branch: str | None = None) -> tuple[int | None, int | None, bool]:
    """Commits ahead of / behind the upstream, and whether the upstream is gone.

    A worktree that has just been created for a branch with no upstream is a
    normal state, not an error — that is why this returns a pair of Nones
    instead of raising. A branch whose upstream *was* configured and is now
    missing (deleted on the remote, before the next fetch) is a different
    thing: that is worth a word, because "0 behind" is then a lie.
    """
    if not path.is_dir():
        return None, None, False
    out = _git(["rev-list", "--left-right", "--count", "@{upstream}...HEAD"], path)
    if out.returncode != 0:
        # No usable upstream. Either none was ever configured (fine), or one
        # was and its remote-tracking ref is missing — after `fetch --prune`
        # that means the branch was deleted on the remote.
        if branch:
            merge = _git(["config", "--get", f"branch.{branch}.merge"], path)
            remote = _git(["config", "--get", f"branch.{branch}.remote"], path)
            if merge.returncode == 0 and merge.stdout.strip() and remote.returncode == 0 and remote.stdout.strip():
                return None, None, True
        return None, None, False
    parts = out.stdout.split()
    if len(parts) != 2:
        return None, None, False
    behind, ahead = int(parts[0]), int(parts[1])
    return ahead, behind, False


def _fetched_at(path: Path) -> str:
    """When this clone last talked to a remote — the age of the ahead/behind numbers.

    `git rev-list @{upstream}...HEAD` compares local refs only. On a clone that
    has not fetched for a week, "0 behind" is a true statement about a stale
    snapshot, which is why the number is printed with the date it was taken.
    """
    if not path.is_dir():
        return ""
    done = _git(["rev-parse", "--git-common-dir"], path)
    if done.returncode != 0:
        return ""
    common = Path(done.stdout.strip())
    if not common.is_absolute():
        common = (path / common)
    stamp = common / "FETCH_HEAD"
    try:
        return datetime.fromtimestamp(stamp.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return ""


def _last_commit(path: Path, fallback_cwd: Path | None = None, head: str = "") -> tuple[str, str]:
    """Subject and time of the commit a worktree points at.

    A checkout that was deleted still has its commit in the repository's object
    database, so a missing directory falls back to asking the repository about
    the recorded HEAD: that is exactly the row a reader wants to identify.
    """
    if path.is_dir():
        out = _git(["log", "-1", "--format=%s%x00%cI"], path)
        if out.returncode == 0 and out.stdout.strip():
            subject, _, when = out.stdout.rstrip("\n").partition("\x00")
            return subject, when
    if fallback_cwd is not None and head:
        out = _git(["log", "-1", "--format=%s%x00%cI", head], fallback_cwd)
        if out.returncode == 0 and out.stdout.strip():
            subject, _, when = out.stdout.rstrip("\n").partition("\x00")
            return subject, when
    return "", ""


def scan(repo: Path) -> list[Worktree]:
    """Every worktree of the repository at `repo`, main one first (git's order)."""
    root = repo_root(repo)
    listing = _parse_list(_must(["worktree", "list", "--porcelain", "-z"], root))
    out: list[Worktree] = []
    for index, entry in enumerate(listing):
        path = Path(entry["path"])
        ahead, behind, upstream_gone = _ahead_behind(path, entry["branch"])
        subject, when = _last_commit(path, root, entry["head"])
        unborn = bool(entry["head"]) and set(entry["head"]) == {"0"}
        out.append(Worktree(
            path=str(path),
            branch=entry["branch"],
            head="" if unborn else entry["head"],  # full id: --json must be exact
            main=index == 0 and not entry.get("bare"),
            locked=entry["locked"],
            prunable=entry["prunable"],
            dirty=_dirty_count(path),
            ahead=ahead,
            behind=behind,
            subject=subject or ("(no commits yet)" if unborn else ""),
            committed_at=when,
            fetched_at=_fetched_at(path) or _fetched_at(root),
            upstream_gone=upstream_gone,
            bare=entry.get("bare", False),
        ))
    return out


def report(repo: Path) -> dict:
    """The JSON the HTTP API serves and the CLI prints."""
    trees = scan(repo)
    return {
        "root": str(repo_root(repo)),
        "worktrees": [tree.as_json() for tree in trees],
        "totals": {
            "count": len(trees),
            "dirty": sum(1 for tree in trees if tree.dirty > 0),
            "behind": sum(1 for tree in trees if (tree.behind or 0) > 0),
            "prunable": sum(1 for tree in trees if tree.prunable),
            "locked": sum(1 for tree in trees if tree.locked),
            "upstream_gone": sum(1 for tree in trees if tree.upstream_gone),
        },
    }
