"""CLI entry point — `python -m git_stale_cleaner` or `git-stale-cleaner`."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import List, Optional, Sequence

from .core import (
    BranchInfo,
    classify_branches,
    delete_branch,
    list_branches,
    summarise,
)


# ANSI colour helpers — degraded to no-ops when stdout is not a TTY.
def _colour_factory(stream) -> dict[str, str]:
    if not stream.isatty():
        return {k: "" for k in ("red", "yellow", "green", "dim", "bold", "reset")}
    return {
        "red": "\x1b[31m",
        "yellow": "\x1b[33m",
        "green": "\x1b[32m",
        "dim": "\x1b[2m",
        "bold": "\x1b[1m",
        "reset": "\x1b[0m",
    }


def _format_table(branches: Sequence[BranchInfo], colours: dict[str, str]) -> str:
    if not branches:
        return f"{colours['dim']}(no branches){colours['reset']}"
    headers = ["Branch", "Age (d)", "Author", "Merged", "Last commit"]
    rows: List[List[str]] = []
    for b in branches:
        rows.append(
            [
                b.short_name + ("*" if b.is_current else ""),
                str(b.age_days()),
                b.author or "-",
                "yes" if b.is_merged else "no",
                (b.last_commit_subject or "")[:60],
            ]
        )
    widths = [
        max(len(str(row[i])) for row in [headers] + rows) for i in range(len(headers))
    ]
    line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-" * w for w in widths)
    out = [colours["bold"] + line + colours["reset"], sep]
    for b, row in zip(branches, rows):
        text = " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        if b.is_protected:
            text = colours["yellow"] + text + colours["reset"]
        elif b.is_merged:
            text = colours["green"] + text + colours["reset"]
        out.append(text)
    return "\n".join(out)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-stale-cleaner",
        description=(
            "Find and prune stale or fully-merged Git branches. "
            "By default runs in dry-run / report mode — pass --delete to "
            "actually remove branches."
        ),
    )
    parser.add_argument(
        "-r",
        "--repo",
        default=".",
        help="Path to the git repository (default: current directory).",
    )
    parser.add_argument(
        "-d",
        "--days",
        type=int,
        default=90,
        help="Threshold age in days. Branches with no commits in this many "
        "days are considered stale (default: 90).",
    )
    parser.add_argument(
        "-b",
        "--base",
        default="main",
        help="Base branch to check 'merged' status against (default: main).",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Operate on remote branches (refs/remotes/) instead of local.",
    )
    parser.add_argument(
        "--remote-name",
        default="origin",
        help="Remote name used when pushing deletes (default: origin).",
    )
    parser.add_argument(
        "--require-merged",
        action="store_true",
        help="Only consider branches that are also fully merged into --base.",
    )
    parser.add_argument(
        "--include-protected",
        action="store_true",
        help="Allow deletion of protected branches (main/master/develop/...). "
        "Use with care.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show every branch, not just deletable candidates.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete branches instead of just reporting (DESTRUCTIVE).",
    )
    parser.add_argument(
        "--push-remote",
        action="store_true",
        help="When deleting local branches, also push the deletion to "
        "--remote-name. Implied when --remote is used.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force-delete unmerged or protected branches (uses `git branch -D`).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt before deleting.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON report instead of the human-readable table.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit.",
    )
    return parser


def _confirm(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _emit_json(deletable: Sequence[BranchInfo], kept: Sequence[BranchInfo]) -> str:
    def to_dict(b: BranchInfo) -> dict:
        return {
            "name": b.name,
            "short_name": b.short_name,
            "remote": b.is_remote,
            "age_days": b.age_days(),
            "author": b.author,
            "subject": b.last_commit_subject,
            "merged": b.is_merged,
            "protected": b.is_protected,
            "current": b.is_current,
            "last_commit": b.last_commit_iso,
        }

    payload = {
        "deletable": [to_dict(b) for b in deletable],
        "kept": [to_dict(b) for b in kept],
        "summary": {
            "deletable": summarise(deletable),
            "kept": summarise(kept),
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print(__version__)
        return 0

    try:
        branches = list_branches(
            remote=args.remote,
            base_branch=args.base,
            cwd=args.repo,
        )
    except FileNotFoundError:
        print("error: `git` executable not found on PATH", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(
            f"error: git command failed (exit {exc.returncode}): "
            f"{exc.stderr.strip() if exc.stderr else exc}",
            file=sys.stderr,
        )
        return 2

    deletable, kept = classify_branches(
        branches,
        threshold_days=args.days,
        require_merged=args.require_merged,
        include_protected=args.include_protected,
    )

    if args.json:
        print(_emit_json(deletable, kept))
        return 0

    colours = _colour_factory(sys.stdout)

    if args.all:
        print(f"{colours['bold']}All branches:{colours['reset']}")
        print(_format_table(branches, colours))
        print()

    print(
        f"{colours['bold']}Deletable "
        f"({len(deletable)} of {len(branches)}, threshold {args.days}d):"
        f"{colours['reset']}"
    )
    print(_format_table(deletable, colours))

    if not args.delete:
        if deletable:
            print()
            print(
                f"{colours['dim']}Dry run — pass --delete to actually remove "
                f"these branches.{colours['reset']}"
            )
        return 0

    if not deletable:
        return 0

    if not args.yes:
        confirmed = _confirm(
            f"\nDelete {len(deletable)} branches? [y/N] "
        )
        if not confirmed:
            print("Aborted.")
            return 1

    failures = 0
    for b in deletable:
        try:
            delete_branch(
                b,
                force=args.force,
                push_remote=args.push_remote,
                remote_name=args.remote_name,
                cwd=args.repo,
            )
            print(f"{colours['green']}deleted{colours['reset']} {b.name}")
        except (subprocess.CalledProcessError, PermissionError) as exc:
            failures += 1
            print(
                f"{colours['red']}failed{colours['reset']} {b.name}: {exc}",
                file=sys.stderr,
            )

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
