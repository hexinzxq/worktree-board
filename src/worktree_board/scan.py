"""Read the worktrees of one repository as plain data.

Everything here shells out to `git` and parses porcelain output. No models, no
network, no state on disk: the same repository always yields the same report,
which is what makes it testable (see tests/test_scan.py).
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed where the scan needed it to succeed."""


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


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

    def as_json(self) -> dict:
        return asdict(self)


def repo_root(path: Path) -> Path:
    """The top level of the working tree that contains `path`."""
    return Path(_must(["rev-parse", "--show-toplevel"], path).strip())


def _parse_list(porcelain: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                entries.append(current)
            current = {"path": line[len("worktree "):], "branch": None,
                       "head": "", "main": False, "locked": False, "prunable": False}
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            current["branch"] = None
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
    if current is not None:
        entries.append(current)
    return entries


def _dirty_count(path: Path) -> int:
    """Count changed and untracked entries; a missing directory counts as all-dirty."""
    if not path.is_dir():
        return -1
    out = _git(["status", "--porcelain"], path)
    if out.returncode != 0:
        return -1
    return len([line for line in out.stdout.splitlines() if line.strip()])


def _ahead_behind(path: Path) -> tuple[int | None, int | None]:
    """Commits ahead of / behind the upstream; (None, None) without one.

    A worktree that has just been created for a branch with no upstream is a
    normal state, not an error — that is why this returns a pair of Nones
    instead of raising.
    """
    if not path.is_dir():
        return None, None
    out = _git(["rev-list", "--left-right", "--count", "@{upstream}...HEAD"], path)
    if out.returncode != 0:
        return None, None
    parts = out.stdout.split()
    if len(parts) != 2:
        return None, None
    behind, ahead = int(parts[0]), int(parts[1])
    return ahead, behind


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


def _last_commit(path: Path) -> tuple[str, str]:
    if not path.is_dir():
        return "", ""
    out = _git(["log", "-1", "--format=%s%x00%cI"], path)
    if out.returncode != 0 or not out.stdout.strip():
        return "", ""
    subject, _, when = out.stdout.rstrip("\n").partition("\x00")
    return subject, when


def scan(repo: Path) -> list[Worktree]:
    """Every worktree of the repository at `repo`, main one first (git's order)."""
    root = repo_root(repo)
    listing = _parse_list(_must(["worktree", "list", "--porcelain"], root))
    out: list[Worktree] = []
    for index, entry in enumerate(listing):
        path = Path(entry["path"])
        ahead, behind = _ahead_behind(path)
        subject, when = _last_commit(path)
        unborn = bool(entry["head"]) and set(entry["head"]) == {"0"}
        out.append(Worktree(
            path=str(path),
            branch=entry["branch"],
            head="" if unborn else entry["head"][:12],
            main=index == 0,
            locked=entry["locked"],
            prunable=entry["prunable"],
            dirty=_dirty_count(path),
            ahead=ahead,
            behind=behind,
            subject=subject or ("(no commits yet)" if unborn else ""),
            committed_at=when,
            fetched_at=_fetched_at(path),
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
        },
    }
