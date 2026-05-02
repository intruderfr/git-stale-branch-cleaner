# git-stale-branch-cleaner

A small, dependency-free Python CLI for finding and pruning stale or fully-merged Git branches in long-lived repositories. Reports first, deletes only when you ask.

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why

Long-lived monorepos and team repos accumulate hundreds of dead branches. Most are either already merged into `main` or have had no commits in months. This tool gives you a clean, opinionated view of what is safe to delete and lets you remove the cruft locally and on `origin` in a single pass.

It uses only the Python standard library and shells out to your installed `git`, so it is safe to drop into any environment that already has Git available.

## Install

From source (recommended while in beta):

```bash
git clone https://github.com/intruderfr/git-stale-branch-cleaner.git
cd git-stale-branch-cleaner
pip install -e .
```

Or run without installing, straight from a checkout:

```bash
python -m git_stale_cleaner --help
```

## Quick start

Report stale local branches in the current repo (older than 90 days, default):

```bash
git-stale-cleaner
```

Tighten the threshold and only show branches already merged into `main`:

```bash
git-stale-cleaner --days 30 --require-merged
```

Inspect remote branches on `origin`:

```bash
git-stale-cleaner --remote --base main
```

Get a machine-readable JSON report (useful for CI dashboards):

```bash
git-stale-cleaner --json --days 60 > stale-report.json
```

Actually delete (interactive confirmation, dry-run is the default):

```bash
git-stale-cleaner --days 60 --require-merged --delete
```

Delete and also push the deletions to `origin`:

```bash
git-stale-cleaner --days 60 --require-merged --delete --push-remote
```

## How it decides what to delete

A branch is considered deletable when **all** of the following are true:

1. It is not the currently checked-out branch.
2. It is not in the protected list (`main`, `master`, `develop`, `dev`, `trunk`, `production`, `release`) — unless `--include-protected` is passed.
3. Its last commit is at least `--days` old (default 90).
4. If `--require-merged` is passed, it is fully merged into `--base` (default `main`).

The protected list and current-branch check apply even with `--force` unless you also pass `--include-protected`. Forcing protected deletions is intentionally a two-flag operation so accidental keystrokes cannot wipe out `main`.

## All flags

| Flag                  | Purpose                                                         |
| --------------------- | --------------------------------------------------------------- |
| `-r, --repo PATH`     | Path to the repository (default: current directory).            |
| `-d, --days N`        | Stale threshold in days (default: 90).                          |
| `-b, --base BRANCH`   | Branch used for the merged check (default: `main`).             |
| `--remote`            | Operate on remote refs instead of local.                        |
| `--remote-name NAME`  | Remote name when pushing deletions (default: `origin`).         |
| `--require-merged`    | Only consider branches already merged into `--base`.            |
| `--include-protected` | Allow deletion of `main`/`master`/etc. (DANGEROUS).             |
| `--all`               | Show every branch, not just deletable candidates.               |
| `--delete`            | Actually delete instead of dry-running.                         |
| `--push-remote`       | After local deletion, push the deletion to `--remote-name`.     |
| `--force`             | Force-delete unmerged branches (`git branch -D`).               |
| `--yes`               | Skip the confirmation prompt before deletion.                   |
| `--json`              | Emit JSON instead of the human-readable table.                  |
| `--version`           | Print version and exit.                                         |

## JSON output shape

```json
{
  "deletable": [
    {
      "name": "feature/old-thing",
      "short_name": "feature/old-thing",
      "remote": false,
      "age_days": 184,
      "author": "Aslam Ahamed",
      "subject": "WIP: experiment with caching",
      "merged": true,
      "protected": false,
      "current": false,
      "last_commit": "2025-10-30T11:14:22+04:00"
    }
  ],
  "kept": [],
  "summary": {
    "deletable": { "count": 1, "merged": 1, "protected": 0, "oldest_days": 184 },
    "kept":      { "count": 0, "merged": 0, "protected": 0, "oldest_days": 0 }
  }
}
```

## Development

```bash
git clone https://github.com/intruderfr/git-stale-branch-cleaner.git
cd git-stale-branch-cleaner
pip install -e ".[test]"
pytest -v
```

The test suite combines pure-function tests and a real-repo integration test that initialises a temporary Git repo on disk, so it requires `git` on `PATH`.

## A safer cleanup workflow

```bash
# 1) See what would happen on local branches.
git-stale-cleaner --days 60

# 2) Restrict to merged-only and snapshot to a file.
git-stale-cleaner --days 60 --require-merged --json > stale.json

# 3) Skim the file, then run the deletion (still interactive).
git-stale-cleaner --days 60 --require-merged --delete

# 4) Now the same on origin.
git-stale-cleaner --remote --days 60 --require-merged --delete --yes
```

## Author

Aslam Ahamed — Head of IT @ Prestige One Developments, Dubai.
[LinkedIn](https://www.linkedin.com/in/aslam-ahamed/)

## License

[MIT](LICENSE)
