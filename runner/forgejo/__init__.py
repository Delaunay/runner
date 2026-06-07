"""Native Forgejo/Codeberg Actions runner.

A dedicated executor for Forgejo/Codeberg Actions workflows. While the YAML
syntax is nearly identical to GitHub Actions, there are notable differences:

- Workflow files live in .forgejo/workflows/ (or .github/workflows/ as fallback)
- Step-level timeout-minutes only (job-level is ignored)
- permissions: key is ignored (Forgejo has its own permission model)
- forgejo.* / forge.* / gitea.* context aliases alongside github.*
- Actions use fully-qualified URLs (https://code.forgejo.org/..., https://codeberg.org/...)
- No OIDC / id-token support
- No concurrency groups
- Runner labels differ (e.g. 'docker' instead of 'ubuntu-latest')

Reuses the existing GitHub Actions executor and expression engine with
Forgejo-specific context injection and compatibility warnings.
"""

from runner.forgejo.executor import execute_workflow
from runner.forgejo.parser import discover_workflows, parse_workflow

__all__ = ["discover_workflows", "execute_workflow", "parse_workflow"]
