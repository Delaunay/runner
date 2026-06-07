"""Shared CLI utilities."""

from __future__ import annotations

from pathlib import Path


def discover(root: Path, backend_name: str = "auto"):
    """Discover workflows using the appropriate backend."""
    from runner.workflow.backends import detect_backend, get_backend_by_name

    if backend_name and backend_name != "auto":
        backend = get_backend_by_name(backend_name)
        if backend is None:
            print(f"Unknown backend: {backend_name}")
            print("Available: github, forgejo, gitlab, auto")
            return {}
    else:
        backend = detect_backend(root)

    workflows = backend.discover(root)
    if not workflows:
        print(f"No workflows found (backend: {backend.name})")
    return workflows, backend


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
