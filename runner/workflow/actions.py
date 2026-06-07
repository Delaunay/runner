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
    """Build INPUT_* environment variables for an action."""
    env = {}
    for name, input_def in manifest.inputs.items():
        value = with_.get(name)
        if value is None:
            value = input_def.default or ""
        env[f"INPUT_{name.upper().replace('-', '_')}"] = str(value)
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

    result = subprocess.run(
        [node, str(entry)],
        cwd=action.path,
        env=env,
        capture_output=capture_output,
        text=True,
    )

    outputs = _parse_node_outputs(result.stdout) if result.stdout else {}

    return ActionResult(
        returncode=result.returncode,
        outputs=outputs,
        error=result.stderr if result.returncode != 0 else "",
    )


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


def _parse_node_outputs(stdout: str) -> dict[str, str]:
    """Parse ::set-output and GITHUB_OUTPUT-style outputs from stdout."""
    outputs = {}
    for line in stdout.splitlines():
        # Legacy ::set-output format
        if line.startswith("::set-output name="):
            rest = line[len("::set-output name="):]
            if "::" in rest:
                key, value = rest.split("::", 1)
                outputs[key] = value
    return outputs
