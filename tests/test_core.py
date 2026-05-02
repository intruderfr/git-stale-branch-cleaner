"""Unit tests for git_stale_cleaner.core."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from git_stale_cleaner.core import (
    BranchInfo,
    classify_branches,
    list_branches,
    parse_iso_datetime,
    summarise,
)


# ---------- pure-function tests ----------

def test_parse_iso_with_z_suffix():
    dt = parse_iso_datetime("2025-01-01T12:00:00Z")
    assert dt == datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_iso_with_offset():
    dt = parse_iso_datetime("2025-01-01T15:00:00+03:00")
    assert dt.utcoffset() == timedelta(hours=3)


def test_parse_iso_naive_assumes_utc():
    dt = parse_iso_datetime("2025-06-15T08:30:00")
    assert dt.tzinfo == timezone.utc


def test_parse_iso_empty_raises():
    with pytest.raises(ValueError):
        parse_iso_datetime("")


def _make_branch(
    name: str,
    *,
    days_old: int,
    merged: bool = False,
    remote: bool = False,
    author: str = "Aslam",
    now: datetime,
) -> BranchInfo:
    last = now - timedelta(days=days_old)
    b = BranchInfo(
        name=name,
        is_remote=remote,
        last_commit_iso=last.isoformat(),
        last_commit_subject=f"WIP on {name}",
        author=author,
    )
    b.is_merged = merged
    return b


def test_classify_threshold():
    now = datetime.now(timezone.utc)
    branches = [
        _make_branch("feature/old", days_old=120, now=now),
        _make_branch("feature/new", days_old=10, now=now),
    ]
    deletable, kept = classify_branches(branches, threshold_days=90, now=now)
    assert [b.name for b in deletable] == ["feature/old"]
    assert [b.name for b in kept] == ["feature/new"]


def test_classify_protected_branches_kept_by_default():
    now = datetime.now(timezone.utc)
    branches = [
        _make_branch("main", days_old=200, now=now),
        _make_branch("feature/x", days_old=200, now=now),
    ]
    deletable, kept = classify_branches(branches, threshold_days=90, now=now)
    assert [b.name for b in deletable] == ["feature/x"]
    assert any(b.name == "main" for b in kept)


def test_classify_include_protected_overrides():
    now = datetime.now(timezone.utc)
    branches = [
        _make_branch("master", days_old=400, now=now),
        _make_branch("dev", days_old=400, now=now),
    ]
    deletable, _ = classify_branches(
        branches, threshold_days=30, include_protected=True, now=now
    )
    assert {b.name for b in deletable} == {"master", "dev"}


def test_classify_require_merged():
    now = datetime.now(timezone.utc)
    branches = [
        _make_branch("feature/merged", days_old=120, merged=True, now=now),
        _make_branch("feature/unmerged", days_old=120, merged=False, now=now),
    ]
    deletable, kept = classify_branches(
        branches, threshold_days=90, require_merged=True, now=now
    )
    assert [b.name for b in deletable] == ["feature/merged"]
    assert [b.name for b in kept] == ["feature/unmerged"]


def test_classify_current_branch_never_deleted():
    now = datetime.now(timezone.utc)
    b = _make_branch("feature/current", days_old=999, now=now)
    b.is_current = True
    deletable, kept = classify_branches([b], threshold_days=30, now=now)
    assert deletable == []
    assert kept == [b]


def test_classify_negative_threshold_raises():
    with pytest.raises(ValueError):
        classify_branches([], threshold_days=-1)


def test_remote_short_name():
    now = datetime.now(timezone.utc)
    b = _make_branch("origin/feature/foo", days_old=10, remote=True, now=now)
    assert b.short_name == "feature/foo"


def test_summarise_empty():
    assert summarise([]) == {"count": 0, "merged": 0, "protected": 0, "oldest_days": 0}


def test_summarise_basic():
    now = datetime.now(timezone.utc)
    branches = [
        _make_branch("a", days_old=5, merged=True, now=now),
        _make_branch("b", days_old=400, now=now),
        _make_branch("main", days_old=10, now=now),
    ]
    s = summarise(branches)
    assert s["count"] == 3
    assert s["merged"] == 1
    assert s["protected"] == 1
    assert s["oldest_days"] >= 399


# ---------- integration test against a real on-disk repo ----------

def _git(args, cwd, env=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _has_git() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


@pytest.mark.skipif(not _has_git(), reason="git not available on PATH")
def test_list_branches_on_real_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
    )

    _git(["init", "-b", "main"], cwd=repo, env=env)
    (repo / "README.md").write_text("hello", encoding="utf-8")
    _git(["add", "."], cwd=repo, env=env)
    _git(["commit", "-m", "initial"], cwd=repo, env=env)

    # branch already merged into main
    _git(["checkout", "-b", "feature/done"], cwd=repo, env=env)
    _git(["checkout", "main"], cwd=repo, env=env)

    # branch with extra commits, not merged
    _git(["checkout", "-b", "feature/wip"], cwd=repo, env=env)
    (repo / "wip.txt").write_text("work", encoding="utf-8")
    _git(["add", "."], cwd=repo, env=env)
    _git(["commit", "-m", "wip"], cwd=repo, env=env)
    _git(["checkout", "main"], cwd=repo, env=env)

    branches = list_branches(remote=False, base_branch="main", cwd=str(repo))
    by_name = {b.name: b for b in branches}

    assert "main" in by_name
    assert by_name["main"].is_protected
    assert by_name["main"].is_current

    assert "feature/done" in by_name
    assert by_name["feature/done"].is_merged is True

    assert "feature/wip" in by_name
    assert by_name["feature/wip"].is_merged is False
    assert by_name["feature/wip"].is_current is False
