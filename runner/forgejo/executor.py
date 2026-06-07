"""Forgejo/Codeberg Actions executor.

Wraps the GitHub Actions executor with Forgejo-specific context injection:
- forgejo.* / forge.* / gitea.* context aliases available in expressions
- Forgejo-specific predefined variables (FORGEJO_*, GITEA_*)
- Adjusted action URL resolution for Forgejo registries
- Forgejo runner label mapping

Delegates actual execution to the existing workflow executor since the runtime
semantics are identical — only the context and discovery differ.
"""

from __future__ import annotations

from pathlib import Path

from runner.workflow.executor import execute_workflow as _execute_workflow
from runner.workflow.parser import Workflow


def execute_workflow(
    workflow: Workflow,
    *,
    job_filter: str | None = None,
    step_filter: str | None = None,
    root: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    skip_actions: bool = False,
    ignore_runs_on: bool = False,
    env: dict[str, str] | None = None,
    matrix: dict[str, str] | None = None,
    skip_steps: list[str] | None = None,
    skip_jobs: list[str] | None = None,
    inputs: dict[str, str] | None = None,
) -> bool:
    """Execute a Forgejo/Codeberg Actions workflow.

    Injects Forgejo-specific environment variables and then delegates
    to the standard GitHub Actions executor.
    """
    forgejo_env = _build_forgejo_env(root or Path.cwd())
    merged_env = {**forgejo_env, **(env or {})}

    return _execute_workflow(
        workflow,
        job_filter=job_filter,
        step_filter=step_filter,
        root=root,
        dry_run=dry_run,
        verbose=verbose,
        skip_actions=skip_actions,
        ignore_runs_on=ignore_runs_on,
        env=merged_env,
        matrix=matrix,
        skip_steps=skip_steps,
        skip_jobs=skip_jobs,
        inputs=inputs,
    )


def _build_forgejo_env(root: Path) -> dict[str, str]:
    """Build Forgejo/Gitea-specific environment variables.

    These mirror CI_* vars that Forgejo runners inject. They're available
    in addition to the standard GITHUB_* vars that the base executor provides.
    """
    import os
    import subprocess

    env = {
        "GITEA_ACTIONS": "true",
        "FORGEJO_ACTIONS": "true",
    }

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            env["GITEA_REPOSITORY_URL"] = url
            # Try to determine if it's a Forgejo/Codeberg instance
            if "codeberg.org" in url:
                env["FORGEJO_INSTANCE"] = "codeberg.org"
            elif "forgejo" in url.lower() or "gitea" in url.lower():
                env["FORGEJO_INSTANCE"] = url.split("/")[2] if "/" in url else ""
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            env["GITEA_REF_NAME"] = result.stdout.strip()
    except FileNotFoundError:
        pass

    # Include standard RUNNER vars with Forgejo defaults
    env["RUNNER_OS"] = _detect_runner_os()
    env["RUNNER_ARCH"] = os.uname().machine

    return env


def _detect_runner_os() -> str:
    """Detect the runner OS label."""
    import sys
    if sys.platform == "linux":
        return "Linux"
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    return sys.platform


# ──────────────────── Runner Label Mapping ────────────────────

FORGEJO_RUNNER_LABELS = {
    "docker": "ubuntu:latest",
    "ubuntu-latest": "ubuntu:latest",
    "node": "node:lts",
    "python": "python:3",
    "alpine": "alpine:latest",
}


def resolve_runner_label(label: str) -> str | None:
    """Map a Forgejo runner label to a container image.

    Forgejo runners use simpler labels than GitHub Actions.
    Returns None if no mapping exists.
    """
    return FORGEJO_RUNNER_LABELS.get(label.lower())
