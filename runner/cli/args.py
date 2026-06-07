"""Shared argument dataclasses for all backend CLI commands.

Every backend (github, forgejo, gitlab) uses the same RunArguments and
ListArguments so the user experience is consistent. Backends that don't
support certain options emit a warning and ignore them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunArguments:
    """Unified arguments for running CI workflows across all backends."""

    workflow: str = ""
    """Workflow name, filename, or path (e.g. 'test', 'test.yml', '.gitlab-ci.yml')."""

    event: str = ""
    """Run all workflows triggered by this event (e.g. 'push', 'pr'). GitHub/Forgejo only."""

    job: str = ""
    """Run only this job (by id or name)."""

    step: str = ""
    """Filter steps by name substring. For GitLab: filter by stage name."""

    root: str = ""
    """Project root (defaults to cwd)."""

    dry_run: bool = False
    """Print commands without executing."""

    verbose: bool = False
    """Show command output in real-time."""

    no_container: bool = False
    """Skip container matching and always run locally."""

    matrix: str = ""
    """Matrix combination filter as key=value pairs (comma-separated). GitHub/Forgejo only."""

    env: str = ""
    """Extra environment variables as key=value pairs (comma-separated)."""

    skip: str = ""
    """Steps/jobs to skip (pipe-separated patterns)."""

    skip_jobs: str = ""
    """Jobs to skip (pipe-separated patterns)."""

    inputs: str = ""
    """workflow_dispatch inputs as key=value pairs (comma-separated). GitHub/Forgejo only."""

    backend: str = "auto"
    """CI backend override: auto, github, forgejo, gitlab."""


@dataclass
class ListArguments:
    """Unified arguments for listing CI workflows across all backends."""

    root: str = ""
    """Project root (defaults to cwd)."""

    backend: str = "auto"
    """CI backend override: auto, github, forgejo, gitlab."""


# ──────────────────── Unsupported parameter warnings ────────────────────

GITHUB_UNSUPPORTED: set[str] = set()  # GitHub supports everything

FORGEJO_UNSUPPORTED: set[str] = set()  # Forgejo supports the same as GitHub

GITLAB_UNSUPPORTED: set[str] = {
    "event",
    "matrix",
    "inputs",
}


def warn_unsupported(args: RunArguments, backend: str) -> None:
    """Emit warnings for parameters that the backend doesn't support."""
    if backend == "gitlab":
        unsupported = GITLAB_UNSUPPORTED
    elif backend == "forgejo":
        unsupported = FORGEJO_UNSUPPORTED
    elif backend == "github":
        unsupported = GITHUB_UNSUPPORTED
    else:
        return

    for param in unsupported:
        value = getattr(args, param, "")
        if value:
            print(f"  ⚠ --{param} is not supported by the {backend} backend (ignored)")
