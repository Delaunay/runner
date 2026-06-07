"""Native GitHub Actions runner.

A dedicated executor for GitHub Actions workflows (.github/workflows/*.yml).
This is the original and most complete runner, supporting:
- Full workflow/job/step execution
- Matrix strategies
- Expression evaluation (${{ }})
- Container support (runs-on)
- Action resolution and execution (uses:)
- Services
- Secrets management
- Reusable workflows
"""

from runner.workflow.executor import execute_workflow
from runner.workflow.parser import discover_workflows, parse_workflow

__all__ = ["discover_workflows", "execute_workflow", "parse_workflow"]
