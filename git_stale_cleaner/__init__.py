"""git-stale-branch-cleaner — find and prune stale or merged Git branches."""

from .core import (
    BranchInfo,
    list_branches,
    classify_branches,
    delete_branch,
    parse_iso_datetime,
)

__version__ = "0.2.0"
__all__ = [
    "BranchInfo",
    "list_branches",
    "classify_branches",
    "delete_branch",
    "parse_iso_datetime",
    "__version__",
]
