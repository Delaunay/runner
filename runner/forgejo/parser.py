"""Forgejo/Codeberg Actions workflow parser.

Reuses the GitHub Actions parser since the YAML syntax is nearly identical,
but applies Forgejo-specific discovery paths and compatibility checks.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from runner.workflow.parser import Workflow
from runner.workflow.parser import parse_workflow as _github_parse


def parse_workflow(path: Path) -> Workflow:
    """Parse a single Forgejo Actions workflow file.

    Same format as GitHub Actions but emits warnings for unsupported features.
    """
    wf = _github_parse(path)
    _check_compatibility(wf)
    return wf


def discover_workflows(root: Path) -> dict[str, Workflow]:
    """Discover Forgejo workflows from .forgejo/workflows/ or .github/workflows/.

    Forgejo looks in .forgejo/workflows/ first, falling back to .github/workflows/.
    """
    forgejo_dir = root / ".forgejo" / "workflows"
    if forgejo_dir.is_dir():
        return _discover_from(forgejo_dir)

    github_dir = root / ".github" / "workflows"
    if github_dir.is_dir():
        return _discover_from(github_dir)

    return {}


def _discover_from(workflows_dir: Path) -> dict[str, Workflow]:
    """Parse all workflow files from a directory."""
    workflows = {}
    for f in sorted(workflows_dir.iterdir()):
        if f.suffix in (".yml", ".yaml"):
            try:
                wf = parse_workflow(f)
                workflows[wf.name] = wf
            except Exception as e:
                print(f"Warning: skipping {f.name}: {e}")
    return workflows


# ──────────────────── Forgejo-specific features not in GitHub ────────────────────

FORGEJO_IGNORED_JOB_KEYS = {
    "permissions",
    "concurrency",
}

FORGEJO_IGNORED_STEP_FEATURES = {
    "id-token",
}


def _check_compatibility(wf: Workflow):
    """Emit warnings for GitHub Actions features that Forgejo ignores or handles differently."""
    for job_id, job in wf.jobs.items():
        if job.timeout_minutes is not None:
            warnings.warn(
                f"[forgejo] {wf.name}/{job_id}: job-level timeout-minutes is ignored "
                f"by Forgejo (only step-level timeout works)",
                stacklevel=3,
            )

        if job.concurrency:
            warnings.warn(
                f"[forgejo] {wf.name}/{job_id}: concurrency groups are not supported "
                f"by Forgejo runners",
                stacklevel=3,
            )
