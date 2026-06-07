"""Forgejo/Codeberg Actions backend.

Nearly identical to GitHub Actions syntax but:
- Workflow files in .forgejo/workflows/ (falls back to .github/workflows/)
- Job-level timeout-minutes, permissions, continue-on-error are ignored (step-level only)
- forgejo.* / forge.* context aliases available alongside github.*
- Actions should use fully-qualified URLs (https://data.forgejo.org/...)
"""

from __future__ import annotations

import warnings
from pathlib import Path

from runner.workflow.parser import Workflow, parse_workflow


class ForgejoBackend:
    """Backend for Forgejo/Codeberg Actions (.forgejo/workflows/ or .github/workflows/)."""

    name: str = "forgejo"

    def detect(self, root: Path) -> bool:
        return (root / ".forgejo" / "workflows").is_dir()

    def discover(self, root: Path) -> dict[str, Workflow]:
        workflows_dir = root / ".forgejo" / "workflows"
        if not workflows_dir.is_dir():
            workflows_dir = root / ".github" / "workflows"
        if not workflows_dir.is_dir():
            return {}

        workflows = {}
        for f in sorted(workflows_dir.iterdir()):
            if f.suffix in (".yml", ".yaml"):
                try:
                    wf = parse_workflow(f)
                    _warn_forgejo_compat(wf)
                    workflows[wf.name] = wf
                except Exception as e:
                    print(f"Warning: skipping {f.name}: {e}")

        return workflows

    def parse(self, path: Path) -> Workflow:
        wf = parse_workflow(path)
        _warn_forgejo_compat(wf)
        return wf


def _warn_forgejo_compat(wf: Workflow):
    """Emit warnings for GitHub Actions features that Forgejo ignores."""
    for job_id, job in wf.jobs.items():
        if job.timeout_minutes is not None:
            warnings.warn(
                f"[forgejo] {wf.name}/{job_id}: job-level timeout-minutes is ignored "
                f"by Forgejo (use step-level instead)",
                stacklevel=2,
            )
