"""Secret provider API.

Secrets are resolved through a chain of providers. The first provider
that returns a value for a key wins. Default chain:

  1. Environment variables (RUNNER_SECRET_<KEY>)
  2. .workspace/.secrets file
  3. Any registered plugin providers (e.g. vault, 1password, aws-sm)

Custom providers implement the SecretProvider protocol and are registered
via `register_provider()` or discovered as runner plugins.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretProvider(Protocol):
    """Protocol for secret providers."""

    @property
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    def get(self, key: str) -> str | None:
        """Retrieve a secret by key. Return None if not found."""
        ...

    def available(self) -> bool:
        """Return True if this provider is configured and usable."""
        ...


class FileSecretProvider:
    """Load secrets from a KEY=VALUE file."""

    def __init__(self, path: Path):
        self._path = path
        self._cache: dict[str, str] | None = None

    @property
    def name(self) -> str:
        return f"file:{self._path}"

    def available(self) -> bool:
        return self._path.exists()

    def get(self, key: str) -> str | None:
        if self._cache is None:
            self._cache = self._load()
        return self._cache.get(key)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}

        secrets = {}
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                secrets[k] = v
        return secrets


class EnvSecretProvider:
    """Read secrets from environment variables prefixed with RUNNER_SECRET_."""

    PREFIX = "RUNNER_SECRET_"

    @property
    def name(self) -> str:
        return "env"

    def available(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return os.environ.get(f"{self.PREFIX}{key}")


class CommandSecretProvider:
    """Fetch secrets by running an external command.

    The command receives the key as an argument and should print the
    secret value to stdout. Exit code 0 = found, non-zero = not found.

    Example: `pass show ci/{key}` or `op read "op://vault/{key}"`
    """

    def __init__(self, command_template: str):
        self._template = command_template

    @property
    def name(self) -> str:
        return f"cmd:{self._template.split()[0]}"

    def available(self) -> bool:
        import shutil
        binary = self._template.split()[0]
        return shutil.which(binary) is not None

    def get(self, key: str) -> str | None:
        cmd = self._template.replace("{key}", key)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None


@dataclass
class SecretStore:
    """Chain of secret providers. First match wins."""

    providers: list[SecretProvider] = field(default_factory=list)
    _cache: dict[str, str] = field(default_factory=dict, init=False)

    def get(self, key: str) -> str:
        """Resolve a secret by key. Returns empty string if not found."""
        if key in self._cache:
            return self._cache[key]

        for provider in self.providers:
            if not provider.available():
                continue
            value = provider.get(key)
            if value is not None:
                self._cache[key] = value
                return value

        return ""

    def get_all(self) -> dict[str, str]:
        """Return all cached secrets resolved so far."""
        return dict(self._cache)

    def register(self, provider: SecretProvider):
        """Add a provider to the end of the chain."""
        self.providers.append(provider)

    def register_first(self, provider: SecretProvider):
        """Add a provider to the beginning of the chain (highest priority)."""
        self.providers.insert(0, provider)


def create_default_store(root: Path) -> SecretStore:
    """Create a SecretStore with the default provider chain."""
    store = SecretStore()

    # Highest priority: environment variables
    store.register(EnvSecretProvider())

    # Second: .workspace/.secrets file
    store.register(FileSecretProvider(root / ".workspace" / ".secrets"))

    # Check for a configured command provider
    cmd = os.environ.get("RUNNER_SECRET_CMD")
    if cmd:
        store.register(CommandSecretProvider(cmd))

    return store
