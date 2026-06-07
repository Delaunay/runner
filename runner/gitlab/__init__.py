"""Native GitLab CI/CD runner.

A dedicated executor that understands GitLab CI semantics directly,
rather than translating to the GitHub Actions IR. This provides:
- Native stage-based execution ordering
- Proper before_script/after_script lifecycle
- GitLab-native variable expansion ($VAR syntax)
- Artifacts passing between stages
- Cache management with key patterns
- Rules evaluation for conditional job execution
- Service containers with proper aliasing

Reuses lower-level primitives from runner.workflow (workspace, container)
for actual command execution and environment management.
"""

from runner.gitlab.executor import execute_pipeline
from runner.gitlab.parser import GitLabPipeline, parse_pipeline

__all__ = ["GitLabPipeline", "execute_pipeline", "parse_pipeline"]
