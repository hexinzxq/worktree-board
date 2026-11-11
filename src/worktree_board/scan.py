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
    entries: list[dict] = []
    current: dict | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                entries.append(current)
            current = {"path": line[len("worktree "):], "branch": None,
                       "head": "", "main": False, "locked": False, "prunable": False,
                       "bare": False}
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
        elif line == "bare":
            current["bare"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
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

    found: list[dict] = []
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
        if ".git" in files:
            dirs[:] = []  # a linked worktree: its owner repo reports it
            continue
        if ".git" in dirs:
            dirs[:] = []  # the checkout owns its own tree
            try:
                root = repo_root(here)
            except GitError:
                continue
            count = len(scan(root))
            examined += 1
            if count >= min_worktrees:
                found.append({"root": str(root), "count": count})
    found.sort(key=lambda item: (-item["count"], item["root"]))
    return {"root": str(base), "repos": found, "examined": examined, "depth_cut": depth_cut}


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
        ahead, behind, upstream_gone = _ahead_behind(path, entry["branch"])
        subject, when = _last_commit(path)
        unborn = bool(entry["head"]) and set(entry["head"]) == {"0"}
        out.append(Worktree(
            path=str(path),
            branch=entry["branch"],
            head="" if unborn else entry["head"][:12],
            main=index == 0 and not entry.get("bare"),
            locked=entry["locked"],
            prunable=entry["prunable"],
            dirty=_dirty_count(path),
            ahead=ahead,
            behind=behind,
            subject=subject or ("(no commits yet)" if unborn else ""),
            committed_at=when,
            fetched_at=_fetched_at(path),
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
