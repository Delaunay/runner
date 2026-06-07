"""GitHub Actions backend — wraps the existing parser."""

from __future__ import annotations

from pathlib import Path

from runner.workflow.parser import Workflow, discover_workflows, parse_workflow


class GitHubBackend:
    """Backend for GitHub Actions (.github/workflows/*.yml)."""

    name: str = "github"

    def detect(self, root: Path) -> bool:
        return (root / ".github" / "workflows").is_dir()

    def discover(self, root: Path) -> dict[str, Workflow]:
        return discover_workflows(root)

    def parse(self, path: Path) -> Workflow:
        return parse_workflow(path)
