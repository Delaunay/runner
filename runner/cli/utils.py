"""Shared CLI utilities."""

from __future__ import annotations

from pathlib import Path


def discover(root: Path):
    from runner.workflow.parser import discover_workflows

    workflows = discover_workflows(root)
    if not workflows:
        print(f"No workflows found under {root / '.github' / 'workflows'}")
    return workflows


def find_workflow(query: str, workflows: dict):
    """Find a workflow by name or filename."""
    if query in workflows:
        return workflows[query]

    for name, wf in workflows.items():
        if wf.path.stem == query or wf.path.name == query:
            return wf
        if query.lower() in name.lower():
            return wf

    return None


def parse_kv(text: str) -> dict[str, str]:
    """Parse 'key=value,key2=value2' into a dict."""
    if not text:
        return {}
    result = {}
    for pair in text.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result
