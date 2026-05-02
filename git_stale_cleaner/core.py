"""Core branch-analysis logic.

Pure functions where possible so the CLI layer stays thin and the
unit tests can cover edge cases without touching a real git repo.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple


# Branches we never want to consider for deletion, even when stale.
PROTECTED_BRANCHES = frozenset(
    {"main", "master", "develop", "dev", "trunk", "production", "release"}
)


@dataclass
class BranchInfo:
    """Snapshot of a single branch as reported by git."""

    name: str
    is_remote: bool
    last_commit_iso: str
    last_commit_subject: str
    author: str
    last_committer_date: datetime = field(init=False)
    is_merged: bool = False
    is_protected: bool = False
    is_current: bool = False

    def __post_init__(self) -> None:
        self.last_committer_date = parse_iso_datetime(self.last_commit_iso)
        if self.short_name in PROTECTED_BRANCHES:
            self.is_protected = True

    @property
    def short_name(self) -> str:
        """Branch name without the `origin/` prefix for remote branches."""
        if self.is_remote and "/" in self.name:
            return self.name.split("/", 1)[1]
        return self.name

    def age_days(self, now: Optional[datetime] = None) -> int:
        """Days since the last commit on this branch."""
        reference = now or datetime.now(timezone.utc)
        return (reference - self.last_committer_date).days

    def is_stale(self, threshold_days: int, now: Optional[datetime] = None) -> bool:
        """True when last commit is older than threshold_days."""
        return self.age_days(now) >= threshold_days


def parse_iso_datetime(value: str) -> datetime:
    """Parse the ISO-8601 timestamp git produces (`%cI`)."""
    if not value:
        raise ValueError("empty datetime string")
    text = value.strip()
    # Python <3.11 cannot parse trailing 'Z'.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _run(cmd: Sequence[str], cwd: Optional[str] = None) -> str:
    """Execute a git command and return stdout, raising on failure."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _current_branch(cwd: Optional[str] = None) -> str:
    out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).strip()
    return out


def _merged_branches(
    base_branch: str,
    remote: bool,
    cwd: Optional[str] = None,
) -> set[str]:
    """Return the set of branch names already merged into base_branch."""
    args = ["git", "branch", "--merged", base_branch]
    if remote:
        args.insert(2, "-r")
    out = _run(args, cwd=cwd)
    merged: set[str] = set()
    for raw in out.splitlines():
        name = raw.replace("*", "").strip()
        if not name or name.startswith("("):  # e.g. "(HEAD detached at ...)"
            continue
        if " -> " in name:  # symbolic ref like origin/HEAD -> origin/main
            continue
        merged.add(name)
    return merged


def list_branches(
    *,
    remote: bool = False,
    base_branch: str = "main",
    cwd: Optional[str] = None,
) -> List[BranchInfo]:
    """Return BranchInfo entries for the given repo.

    Uses `git for-each-ref` because it lets us request structured fields with
    a custom format string and avoids the messy parsing of `git branch -v`.
    """
    ref_prefix = "refs/remotes/" if remote else "refs/heads/"
    fmt = "%(refname:short)%09%(committerdate:iso-strict)%09%(authorname)%09%(subject)"
    out = _run(
        ["git", "for-each-ref", "--format", fmt, ref_prefix],
        cwd=cwd,
    )
    branches: List[BranchInfo] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            # Subject was empty — pad it to keep things sane.
            parts = parts + [""] * (4 - len(parts))
        name, iso, author, subject = parts[0], parts[1], parts[2], "\t".join(parts[3:])
        if remote and name.endswith("/HEAD"):
            continue
        branches.append(
            BranchInfo(
                name=name,
                is_remote=remote,
                last_commit_iso=iso,
                last_commit_subject=subject,
                author=author,
            )
        )

    try:
        merged = _merged_branches(base_branch, remote=remote, cwd=cwd)
    except subprocess.CalledProcessError:
        # base branch may not exist locally — skip the merged annotation
        merged = set()

    if not remote:
        try:
            current = _current_branch(cwd=cwd)
        except subprocess.CalledProcessError:
            current = ""
        for b in branches:
            if b.name == current:
                b.is_current = True

    for b in branches:
        if b.name in merged or (remote and b.short_name in merged):
            b.is_merged = True
        # Never consider the merge base itself merged-into-itself for deletion.
        if b.short_name == base_branch:
            b.is_protected = True
    return branches


def classify_branches(
    branches: Iterable[BranchInfo],
    *,
    threshold_days: int,
    require_merged: bool = False,
    include_protected: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[List[BranchInfo], List[BranchInfo]]:
    """Split branches into (deletable, kept) based on the policy.

    A branch is *deletable* when:
      - it is not protected (unless include_protected is True),
      - it is not the current checked-out branch,
      - its last commit is older than threshold_days, and
      - if require_merged is True, it is fully merged into the base branch.
    """
    if threshold_days < 0:
        raise ValueError("threshold_days must be >= 0")

    deletable: List[BranchInfo] = []
    kept: List[BranchInfo] = []
    for b in branches:
        if b.is_current:
            kept.append(b)
            continue
        if b.is_protected and not include_protected:
            kept.append(b)
            continue
        if not b.is_stale(threshold_days, now=now):
            kept.append(b)
            continue
        if require_merged and not b.is_merged:
            kept.append(b)
            continue
        deletable.append(b)
    return deletable, kept


def delete_branch(
    branch: BranchInfo,
    *,
    force: bool = False,
    push_remote: bool = False,
    remote_name: str = "origin",
    cwd: Optional[str] = None,
) -> str:
    """Delete a branch locally, or push the deletion to remote.

    Returns the git command's stdout for logging purposes.
    Raises subprocess.CalledProcessError on failure.
    """
    if branch.is_protected and not force:
        raise PermissionError(
            f"Refusing to delete protected branch '{branch.name}' without force."
        )

    if branch.is_remote or push_remote:
        # Use `git push origin --delete <branch>` — works on the short name.
        target = branch.short_name if branch.is_remote else branch.name
        return _run(
            ["git", "push", remote_name, "--delete", target],
            cwd=cwd,
        )

    flag = "-D" if force else "-d"
    return _run(["git", "branch", flag, branch.name], cwd=cwd)


def summarise(branches: Sequence[BranchInfo]) -> dict:
    """Quick stats dict — handy for JSON output and tests."""
    if not branches:
        return {"count": 0, "merged": 0, "protected": 0, "oldest_days": 0}
    return {
        "count": len(branches),
        "merged": sum(1 for b in branches if b.is_merged),
        "protected": sum(1 for b in branches if b.is_protected),
        "oldest_days": max(b.age_days() for b in branches),
    }
