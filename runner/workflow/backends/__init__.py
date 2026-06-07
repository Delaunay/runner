"""Backend abstraction for multi-CI format support.

Each backend is a parser that produces the shared IR (Workflow/Job/Step).
Auto-detection picks the right backend based on repository file layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from runner.workflow.parser import Workflow


@runtime_checkable
class Backend(Protocol):
    """Protocol for CI workflow backends."""

    name: str

    def detect(self, root: Path) -> bool:
        """Return True if this backend's workflow files exist under root."""
        ...

    def discover(self, root: Path) -> dict[str, Workflow]:
        """Find and parse all workflows for this backend."""
        ...

    def parse(self, path: Path) -> Workflow:
        """Parse a single workflow file into the shared IR."""
        ...


_BACKENDS: list[Backend] = []


def register_backend(backend: Backend):
    """Register a backend for auto-detection."""
    _BACKENDS.append(backend)


def get_backends() -> list[Backend]:
    """Return all registered backends."""
    _ensure_loaded()
    return list(_BACKENDS)


def detect_backend(root: Path) -> Backend:
    """Auto-detect which backend to use based on repo file layout.

    Detection order (first match wins):
    1. .forgejo/workflows/ exists -> ForgejoBackend
    2. .gitlab-ci.yml exists -> GitLabBackend
    3. .github/workflows/ exists -> GitHubBackend
    4. Fallback: GitHubBackend
    """
    _ensure_loaded()
    for backend in _BACKENDS:
        if backend.detect(root):
            return backend
    # Fallback to GitHub (always last registered)
    return _BACKENDS[-1]


def get_backend_by_name(name: str) -> Backend | None:
    """Look up a backend by name."""
    _ensure_loaded()
    for backend in _BACKENDS:
        if backend.name == name:
            return backend
    return None


_loaded = False


def _ensure_loaded():
    """Lazily import and register all backends on first use."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    from runner.workflow.backends.forgejo import ForgejoBackend
    from runner.workflow.backends.github import GitHubBackend
    from runner.workflow.backends.gitlab import GitLabBackend

    # Order matters: first match wins in detect_backend()
    register_backend(ForgejoBackend())
    register_backend(GitLabBackend())
    register_backend(GitHubBackend())
