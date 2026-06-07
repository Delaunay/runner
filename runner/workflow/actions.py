"""GitHub Actions resolution and execution.

Handles `uses:` references by:
1. Resolving the action source (local path, GitHub repo)
2. Caching downloaded actions in .workspace/actions/
3. Parsing the action.yml manifest
4. Executing based on action type (composite, node, docker)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ActionManifest:
    """Parsed action.yml."""

    name: str
    description: str = ""
    inputs: dict[str, ActionInput] = field(default_factory=dict)
    outputs: dict[str, ActionOutput] = field(default_factory=dict)
    runs: ActionRuns | None = None
    path: Path | None = None


@dataclass
class ActionInput:
    description: str = ""
    required: bool = False
    default: str | None = None


@dataclass
class ActionOutput:
    description: str = ""
    value: str = ""


@dataclass
class ActionRuns:
    """The `runs:` section of action.yml."""

    using: str  # "composite", "node20", "node16", "docker"
    main: str | None = None  # JS entry point
    pre: str | None = None
    post: str | None = None
    image: str | None = None  # Docker image or Dockerfile
    steps: list[dict[str, Any]] = field(default_factory=list)  # Composite steps
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedAction:
    """A fully resolved action ready to execute."""

    manifest: ActionManifest
    path: Path
    ref: str = ""


def resolve_action(uses: str, *, root: Path, cache_dir: Path) -> ResolvedAction | None:
    """Resolve a `uses:` string to a local path with action.yml.

    Supports:
      - ./path/to/action  (local action)
      - owner/repo@ref    (GitHub action)
      - owner/repo/path@ref (GitHub action in subdirectory)
    """
    if uses.startswith("./") or uses.startswith("../"):
        return _resolve_local(uses, root)

    return _resolve_github(uses, cache_dir)


def _resolve_local(uses: str, root: Path) -> ResolvedAction | None:
    """Resolve a local action path."""
    action_dir = root / uses
    manifest = _load_manifest(action_dir)
    if manifest is None:
        return None
    return ResolvedAction(manifest=manifest, path=action_dir)


def _resolve_github(uses: str, cache_dir: Path) -> ResolvedAction | None:
    """Resolve a GitHub action (owner/repo@ref or owner/repo/path@ref)."""
    if "@" not in uses:
        return None

    ref_part = uses.split("@", 1)
    path_part = ref_part[0]
    ref = ref_part[1]

    parts = path_part.split("/")
    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1]
    subpath = "/".join(parts[2:]) if len(parts) > 2 else ""

    cache_key = f"{owner}-{repo}-{ref}"
    cached = cache_dir / cache_key

    if not cached.exists():
        if not _clone_action(owner, repo, ref, cached):
            return None

    action_dir = cached / subpath if subpath else cached
    manifest = _load_manifest(action_dir)
    if manifest is None:
        return None

    return ResolvedAction(manifest=manifest, path=action_dir, ref=ref)


def _clone_action(owner: str, repo: str, ref: str, dest: Path) -> bool:
    """Clone a GitHub action repo at a specific ref."""
    url = f"https://github.com/{owner}/{repo}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["git", "clone", "--depth=1", "--branch", ref, url, str(dest)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # Try without --branch (ref might be a SHA)
        result = subprocess.run(
            ["git", "clone", "--depth=1", url, str(dest)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False

        result = subprocess.run(
            ["git", "checkout", ref],
            cwd=dest,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            return False

    return True


def _load_manifest(action_dir: Path) -> ActionManifest | None:
    """Parse action.yml or action.yaml from a directory."""
    for name in ("action.yml", "action.yaml"):
        manifest_path = action_dir / name
        if manifest_path.exists():
            return _parse_manifest(manifest_path)
    return None


def _parse_manifest(path: Path) -> ActionManifest:
    """Parse an action.yml file."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty action manifest: {path}")

    inputs = {}
    for k, v in raw.get("inputs", {}).items():
        inputs[k] = ActionInput(
            description=v.get("description", ""),
            required=v.get("required", False),
            default=v.get("default"),
        )

    outputs = {}
    for k, v in raw.get("outputs", {}).items():
        outputs[k] = ActionOutput(
            description=v.get("description", ""),
            value=v.get("value", ""),
        )

    runs_raw = raw.get("runs", {})
    runs = ActionRuns(
        using=runs_raw.get("using", ""),
        main=runs_raw.get("main"),
        pre=runs_raw.get("pre"),
        post=runs_raw.get("post"),
        image=runs_raw.get("image"),
        steps=runs_raw.get("steps", []),
        args=runs_raw.get("args", []),
        env={k: str(v) for k, v in runs_raw.get("env", {}).items()},
    )

    return ActionManifest(
        name=raw.get("name", path.parent.name),
        description=raw.get("description", ""),
        inputs=inputs,
        outputs=outputs,
        runs=runs,
        path=path,
    )


def build_input_env(manifest: ActionManifest, with_: dict[str, Any]) -> dict[str, str]:
    """Build INPUT_* environment variables for an action.

    GitHub Actions convention: INPUT_{NAME.upper()} with spaces replaced by
    underscores but hyphens preserved. @actions/core getInput() looks up
    `INPUT_${name.replace(/ /g, '_').toUpperCase()}`.
    """
    env = {}

    # First apply defaults from manifest
    for name, input_def in manifest.inputs.items():
        value = with_.get(name)
        if value is None:
            value = input_def.default or ""
        env_key = f"INPUT_{name.upper().replace(' ', '_')}"
        env[env_key] = str(value)

    # Then apply any with: keys not declared in inputs (actions can read arbitrary inputs)
    for name, value in with_.items():
        env_key = f"INPUT_{name.upper().replace(' ', '_')}"
        if env_key not in env or with_.get(name) is not None:
            env[env_key] = str(value)

    return env


def execute_action(
    action: ResolvedAction,
    *,
    with_: dict[str, Any],
    env: dict[str, str],
    root: Path,
    verbose: bool = False,
    capture_output: bool = True,
) -> ActionResult:
    """Execute a resolved action."""
    runs = action.manifest.runs
    if runs is None:
        return ActionResult(returncode=1, error="No 'runs' section in action.yml")

    input_env = build_input_env(action.manifest, with_)
    full_env = {**os.environ, **env, **input_env, **runs.env}

    if runs.using == "composite":
        return _execute_composite(action, full_env, root=root, verbose=verbose)

    if runs.using.startswith("node"):
        return _execute_node(action, full_env, verbose=verbose, capture_output=capture_output)

    if runs.using == "docker":
        return _execute_docker(action, full_env, root=root, verbose=verbose, capture_output=capture_output)

    return ActionResult(returncode=1, error=f"Unsupported action type: {runs.using}")


@dataclass
class ActionResult:
    returncode: int = 0
    error: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    composite_steps: list[dict[str, Any]] | None = None
    exported_env: dict[str, str] = field(default_factory=dict)
    exported_path: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.returncode == 0


def _execute_composite(
    action: ResolvedAction,
    env: dict[str, str],
    *,
    root: Path,
    verbose: bool = False,
) -> ActionResult:
    """Return composite steps to be inlined by the executor."""
    steps = action.manifest.runs.steps if action.manifest.runs else []
    if not steps:
        return ActionResult(returncode=0)

    return ActionResult(returncode=0, composite_steps=steps)


def _execute_node(
    action: ResolvedAction,
    env: dict[str, str],
    *,
    verbose: bool = False,
    capture_output: bool = True,
) -> ActionResult:
    """Execute a JavaScript action with Node.js."""
    import tempfile

    node = shutil.which("node")
    if not node:
        return ActionResult(returncode=1, error="node not found on PATH")

    runs = action.manifest.runs
    if not runs or not runs.main:
        return ActionResult(returncode=1, error="No main entry point in action.yml")

    entry = action.path / runs.main
    if not entry.exists():
        return ActionResult(returncode=1, error=f"Entry point not found: {entry}")

    if verbose:
        print(f"    → node {entry.name}")

    # Create temp files for GITHUB_OUTPUT, GITHUB_STATE, GITHUB_ENV, GITHUB_PATH
    # Actions use these to communicate outputs and state
    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as out_f,
        tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as state_f,
        tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as env_f,
        tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as path_f,
    ):
        output_file = out_f.name
        state_file = state_f.name
        env_file = env_f.name
        path_file = path_f.name

    # RUNNER_TOOL_CACHE: where setup-* actions install tools
    workspace_root = Path(env.get("GITHUB_WORKSPACE", os.getcwd()))
    tool_cache = workspace_root / ".workspace" / "tool-cache"
    tool_cache.mkdir(parents=True, exist_ok=True)

    # RUNNER_TEMP: scratch directory for actions
    runner_temp = workspace_root / ".workspace" / "runner-temp"
    runner_temp.mkdir(parents=True, exist_ok=True)

    action_env = {
        **env,
        "GITHUB_OUTPUT": output_file,
        "GITHUB_STATE": state_file,
        "GITHUB_ENV": env_file,
        "GITHUB_PATH": path_file,
        "GITHUB_ACTION_PATH": str(action.path),
        "GITHUB_WORKSPACE": str(workspace_root),
        "RUNNER_TOOL_CACHE": str(tool_cache),
        "RUNNER_TEMP": str(runner_temp),
        "RUNNER_OS": _runner_os(),
        "RUNNER_ARCH": _runner_arch(),
    }

    try:
        result = subprocess.run(
            [node, str(entry)],
            cwd=action.path,
            env=action_env,
            capture_output=capture_output,
            text=True,
        )

        # Parse outputs from GITHUB_OUTPUT file
        outputs = _parse_output_file(output_file)
        # Also parse legacy ::set-output from stdout
        if result.stdout:
            outputs.update(_parse_node_outputs(result.stdout))

        # Read env vars exported by the action (GITHUB_ENV)
        exported_env = _parse_env_exports(env_file)

        # Read PATH additions (GITHUB_PATH)
        exported_path = _parse_path_exports(path_file)

        return ActionResult(
            returncode=result.returncode,
            outputs=outputs,
            exported_env=exported_env,
            exported_path=exported_path,
            error=result.stderr if result.returncode != 0 else "",
        )
    finally:
        for f in (output_file, state_file, env_file, path_file):
            try:
                os.unlink(f)
            except OSError:
                pass


def _execute_docker(
    action: ResolvedAction,
    env: dict[str, str],
    *,
    root: Path,
    verbose: bool = False,
    capture_output: bool = True,
) -> ActionResult:
    """Execute a Docker action."""
    runtime_cmd = shutil.which("podman") or shutil.which("docker")
    if not runtime_cmd:
        return ActionResult(returncode=1, error="No container runtime (podman/docker) found")

    runs = action.manifest.runs
    if not runs:
        return ActionResult(returncode=1, error="No runs section")

    image = runs.image
    if not image:
        return ActionResult(returncode=1, error="No image specified in action.yml")

    # If image is a Dockerfile path, build it
    if image.startswith("Dockerfile") or image.startswith("./"):
        dockerfile = action.path / image
        if not dockerfile.exists():
            return ActionResult(returncode=1, error=f"Dockerfile not found: {dockerfile}")

        tag = f"runner-action-{action.manifest.name}".lower().replace(" ", "-")
        if verbose:
            print(f"    → building {tag} from {image}")

        build_result = subprocess.run(
            [runtime_cmd, "build", "-t", tag, "-f", str(dockerfile), str(action.path)],
            capture_output=capture_output,
            text=True,
        )
        if build_result.returncode != 0:
            return ActionResult(returncode=build_result.returncode, error=build_result.stderr)
        image = tag

    # Run the container
    args = [
        runtime_cmd, "run", "--rm",
        "-v", f"{root}:/github/workspace",
        "-w", "/github/workspace",
    ]

    # Pass INPUT_* and other env vars
    for key, val in env.items():
        if key.startswith("INPUT_") or key.startswith("GITHUB_"):
            args.extend(["-e", f"{key}={val}"])

    args.append(image)

    if runs.args:
        resolved_args = [_resolve_arg(a, env) for a in runs.args]
        args.extend(resolved_args)

    if verbose:
        print(f"    → {runtime_cmd} run {image}")

    result = subprocess.run(args, capture_output=capture_output, text=True)

    return ActionResult(
        returncode=result.returncode,
        error=result.stderr if result.returncode != 0 else "",
    )


def _resolve_arg(arg: str, env: dict[str, str]) -> str:
    """Resolve ${{ inputs.* }} in docker args."""
    import re

    def replacer(m):
        expr = m.group(1).strip()
        if expr.startswith("inputs."):
            key = expr[len("inputs."):]
            env_key = f"INPUT_{key.upper().replace('-', '_')}"
            return env.get(env_key, "")
        return m.group(0)

    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", replacer, arg)


def _parse_output_file(path: str) -> dict[str, str]:
    """Parse a GITHUB_OUTPUT file (key=value pairs, or multiline delimiter format)."""
    outputs = {}
    try:
        with open(path) as f:
            content = f.read()
    except (OSError, FileNotFoundError):
        return outputs

    if not content.strip():
        return outputs

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line:
            # Multiline: key<<DELIMITER\nvalue\nDELIMITER
            key, delimiter = line.split("<<", 1)
            key = key.strip()
            delimiter = delimiter.strip()
            value_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != delimiter:
                value_lines.append(lines[i])
                i += 1
            outputs[key] = "\n".join(value_lines)
        elif "=" in line:
            key, value = line.split("=", 1)
            outputs[key.strip()] = value
        i += 1

    return outputs


def _parse_env_exports(path: str) -> dict[str, str]:
    """Parse a GITHUB_ENV file written by an action.

    Format: KEY=VALUE lines, or multiline KEY<<DELIMITER blocks.
    """
    return _parse_output_file(path)


def _parse_path_exports(path: str) -> list[str]:
    """Parse a GITHUB_PATH file (one directory per line)."""
    try:
        with open(path) as f:
            content = f.read()
    except (OSError, FileNotFoundError):
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def _parse_node_outputs(stdout: str) -> dict[str, str]:
    """Parse ::set-output from stdout (legacy format)."""
    outputs = {}
    for line in stdout.splitlines():
        if line.startswith("::set-output name="):
            rest = line[len("::set-output name="):]
            if "::" in rest:
                key, value = rest.split("::", 1)
                outputs[key] = value
    return outputs


def _runner_os() -> str:
    """Return the RUNNER_OS value matching GitHub's convention."""
    import sys
    if sys.platform == "linux":
        return "Linux"
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    return sys.platform


def _runner_arch() -> str:
    """Return the RUNNER_ARCH value matching GitHub's convention."""
    import platform
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "X64"
    if machine in ("aarch64", "arm64"):
        return "ARM64"
    if machine in ("armv7l",):
        return "ARM"
    return machine.upper()
