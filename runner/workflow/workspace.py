"""Workspace management for isolated CI-like execution.

Creates a .workspace directory containing:
- A shared git clone of the project (--shared for CoW efficiency)
- Per-job working directories
- Python virtualenvs
- Environment file scratch space (GITHUB_ENV, GITHUB_OUTPUT, etc.)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Workspace:
    """An isolated workspace for running CI jobs."""

    root: Path
    base_dir: Path = field(init=False)
    job_dir: Path | None = None

    def __post_init__(self):
        self.base_dir = self.root / ".workspace"

    def setup(self):
        """Create the workspace directory structure."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "jobs").mkdir(exist_ok=True)
        (self.base_dir / "artifacts").mkdir(exist_ok=True)
        (self.base_dir / "cache").mkdir(exist_ok=True)

    def create_job_dir(self, job_id: str, matrix_label: str = "") -> JobWorkspace:
        """Create an isolated directory for a single job run."""
        suffix = f"-{matrix_label}" if matrix_label else ""
        name = f"{job_id}{suffix}"
        job_dir = self.base_dir / "jobs" / name

        if job_dir.exists():
            shutil.rmtree(job_dir)
        job_dir.mkdir(parents=True)

        return JobWorkspace(
            workspace=self,
            path=job_dir,
            job_id=job_id,
        )

    def artifact_dir(self, name: str) -> Path:
        """Get/create a directory for storing artifacts between jobs."""
        d = self.base_dir / "artifacts" / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def cache_dir(self, key: str) -> Path:
        """Get/create a cache directory by key."""
        d = self.base_dir / "cache" / key
        d.mkdir(parents=True, exist_ok=True)
        return d

    def clean(self):
        """Remove the entire workspace."""
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)


@dataclass
class JobWorkspace:
    """Per-job isolated workspace with its own clone and env files."""

    workspace: Workspace
    path: Path
    job_id: str

    _env_file: Path | None = field(default=None, init=False)
    _output_file: Path | None = field(default=None, init=False)
    _path_file: Path | None = field(default=None, init=False)
    _step_summary_file: Path | None = field(default=None, init=False)

    def setup(self, *, clone: bool = True, venv: bool = False, verbose: bool = False):
        """Set up the job workspace."""
        self._env_file = self.path / "env"
        self._output_file = self.path / "output"
        self._path_file = self.path / "path"
        self._step_summary_file = self.path / "step_summary"

        self._env_file.touch()
        self._output_file.touch()
        self._path_file.touch()
        self._step_summary_file.touch()

        if clone:
            self._clone_repo(verbose=verbose)

        if venv:
            self._create_venv(verbose=verbose)

    def _clone_repo(self, verbose: bool = False):
        """Create a shared git clone of the source repo."""
        clone_dir = self.clone_path
        if clone_dir.exists():
            return

        source = self.workspace.root
        args = [
            "git", "clone",
            "--shared",
            "--no-checkout",
            str(source),
            str(clone_dir),
        ]

        if verbose:
            print(f"  → cloning repo (shared) into {clone_dir.name}")

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ⚠ git clone failed: {result.stderr.strip()}")
            return

        # Checkout current HEAD
        subprocess.run(
            ["git", "checkout", "--force", "HEAD"],
            cwd=clone_dir,
            capture_output=True,
        )

    def _create_venv(self, verbose: bool = False):
        """Create a Python virtualenv in the job workspace."""
        venv_path = self.venv_path
        if venv_path.exists():
            return

        if verbose:
            print(f"  → creating venv at {venv_path.name}")

        uv = shutil.which("uv")
        if uv:
            subprocess.run(
                [uv, "venv", str(venv_path)],
                capture_output=True,
            )
        else:
            import venv
            venv.create(str(venv_path), with_pip=True)

    @property
    def clone_path(self) -> Path:
        return self.path / "repo"

    @property
    def venv_path(self) -> Path:
        return self.path / ".venv"

    @property
    def env_file(self) -> Path:
        if self._env_file is None:
            self._env_file = self.path / "env"
            self._env_file.touch()
        return self._env_file

    @property
    def output_file(self) -> Path:
        if self._output_file is None:
            self._output_file = self.path / "output"
            self._output_file.touch()
        return self._output_file

    @property
    def path_file(self) -> Path:
        if self._path_file is None:
            self._path_file = self.path / "path"
            self._path_file.touch()
        return self._path_file

    @property
    def step_summary_file(self) -> Path:
        if self._step_summary_file is None:
            self._step_summary_file = self.path / "step_summary"
            self._step_summary_file.touch()
        return self._step_summary_file

    def read_env_file(self) -> dict[str, str]:
        """Parse GITHUB_ENV-style file (KEY=VALUE or KEY<<DELIM multiline)."""
        return _parse_env_file(self.env_file)

    def read_outputs(self) -> dict[str, str]:
        """Parse GITHUB_OUTPUT-style file."""
        return _parse_env_file(self.output_file)

    def read_path_additions(self) -> list[str]:
        """Read paths added via GITHUB_PATH."""
        text = self.path_file.read_text()
        return [line.strip() for line in text.splitlines() if line.strip()]

    def append_path(self, path: str) -> None:
        """Add a directory to the GITHUB_PATH file."""
        with open(self.path_file, "a") as f:
            f.write(f"{path}\n")

    def get_github_env_vars(self) -> dict[str, str]:
        """Environment variables that mimic GitHub Actions runner env."""
        return {
            "GITHUB_WORKSPACE": str(self.clone_path),
            "GITHUB_ENV": str(self.env_file),
            "GITHUB_OUTPUT": str(self.output_file),
            "GITHUB_PATH": str(self.path_file),
            "GITHUB_STEP_SUMMARY": str(self.step_summary_file),
            "RUNNER_TEMP": str(self.path / "tmp"),
            "RUNNER_TOOL_CACHE": str(self.path / "tool-cache"),
        }

    def reset_step_files(self):
        """Clear per-step files (output) between steps."""
        self.output_file.write_text("")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a GitHub Actions environment file.

    Supports:
      KEY=VALUE
      KEY<<DELIMITER
      multiline value
      DELIMITER
    """
    result = {}
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line:
            key, delimiter = line.split("<<", 1)
            key = key.strip()
            delimiter = delimiter.strip()
            value_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != delimiter:
                value_lines.append(lines[i])
                i += 1
            result[key] = "\n".join(value_lines)
        elif "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
        i += 1
    return result
