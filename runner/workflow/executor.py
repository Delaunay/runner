"""Execute GitHub Actions workflow steps locally."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runner.workflow.expressions import (
    ExpressionContext,
    StepContext,
    build_github_context,
    evaluate_condition,
    evaluate_expression,
)
from runner.workflow.parser import Defaults, Job, Step, Workflow, parse_workflow
from runner.workflow.secrets import SecretStore, create_default_store
from runner.workflow.workspace import JobWorkspace, Workspace


@dataclass
class StepResult:
    step: Step
    returncode: int
    stdout: str = ""
    stderr: str = ""
    skipped: bool = False
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 or self.skipped

    @property
    def outcome(self) -> str:
        if self.skipped:
            return "skipped"
        if self.timed_out:
            return "failure"
        return "success" if self.returncode == 0 else "failure"


@dataclass
class JobResult:
    job: Job
    matrix_values: dict[str, Any] = field(default_factory=dict)
    step_results: list[StepResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(r.success or r.step.continue_on_error for r in self.step_results)


@dataclass
class ExecutionContext:
    """Runtime context for workflow execution."""

    root: Path
    env: dict[str, str] = field(default_factory=dict)
    matrix: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    verbose: bool = False
    skip_actions: bool = False
    ignore_runs_on: bool = False
    container_session: Any = None
    job_workspace: JobWorkspace | None = None

    # Step tracking for outputs and conditions
    steps: dict[str, StepContext] = field(default_factory=dict)
    job_status: str = "success"
    github: dict[str, str] = field(default_factory=dict)
    secret_store: SecretStore | None = None
    workflow_defaults: Defaults | None = None
    needs: dict[str, Any] = field(default_factory=dict)
    skip_steps: list[str] = field(default_factory=list)
    inputs: dict[str, str] = field(default_factory=dict)

    def expr_context(self) -> ExpressionContext:
        """Build expression context from current state."""
        return ExpressionContext(
            matrix=self.matrix,
            env=self.env,
            steps=self.steps,
            github=self.github,
            secrets=_SecretProxy(self.secret_store) if self.secret_store else {},
            job_status=self.job_status,
            runner={"os": sys.platform, "arch": os.uname().machine},
            needs=self.needs,
            inputs=self.inputs,
        )

    def resolve_expr(self, text: str) -> str:
        """Resolve ${{ ... }} expressions in strings."""
        if text is None:
            return text
        return evaluate_expression(str(text), self.expr_context())


class _SecretProxy:
    """Dict-like proxy that lazily resolves secrets through the store."""

    def __init__(self, store: SecretStore):
        self._store = store

    def get(self, key: str, default: str = "") -> str:
        return self._store.get(key) or default

    def __contains__(self, key: str) -> bool:
        return bool(self._store.get(key))

    def __getitem__(self, key: str) -> str:
        return self._store.get(key)


# Known GitHub Actions that have local equivalents
ACTION_HANDLERS: dict[str, Any] = {}


def _action_handler(pattern: str):
    """Decorator to register a handler for a `uses:` action."""
    def decorator(fn):
        ACTION_HANDLERS[pattern] = fn
        return fn
    return decorator


@_action_handler("actions/checkout")
def _handle_checkout(step: Step, ctx: ExecutionContext) -> StepResult:
    if ctx.verbose:
        print("  → checkout: already in repo (noop)")
    return StepResult(step=step, returncode=0, skipped=True)


@_action_handler("actions/setup-python")
def _handle_setup_python(step: Step, ctx: ExecutionContext) -> StepResult:
    version = ctx.resolve_expr(str(step.with_.get("python-version", "")))
    current = f"{sys.version_info.major}.{sys.version_info.minor}"
    if ctx.verbose:
        print(f"  → setup-python: requested={version}, current={current}")
    if version and not current.startswith(version.split(".")[0]):
        print(f"  ⚠ Python {version} requested but running {current}")
    return StepResult(step=step, returncode=0, skipped=True)


@_action_handler("actions/setup-node")
def _handle_setup_node(step: Step, ctx: ExecutionContext) -> StepResult:
    version = ctx.resolve_expr(str(step.with_.get("node-version", "")))
    node = shutil.which("node")
    if ctx.verbose:
        if node:
            print(f"  → setup-node: requested={version}, found={node}")
        else:
            print("  ⚠ setup-node: node not found on PATH")
    return StepResult(step=step, returncode=0, skipped=True)


@_action_handler("astral-sh/setup-uv")
def _handle_setup_uv(step: Step, ctx: ExecutionContext) -> StepResult:
    uv = shutil.which("uv")
    if ctx.verbose:
        if uv:
            print(f"  → setup-uv: found={uv}")
        else:
            print("  ⚠ setup-uv: uv not found on PATH")
    return StepResult(step=step, returncode=0, skipped=True)


@_action_handler("actions/upload-artifact")
def _handle_upload_artifact(step: Step, ctx: ExecutionContext) -> StepResult:
    """Store artifact files in .workspace/artifacts/."""
    name = ctx.resolve_expr(str(step.with_.get("name", "artifact")))
    path_pattern = ctx.resolve_expr(str(step.with_.get("path", "")))

    if not path_pattern:
        return StepResult(step=step, returncode=0, skipped=True)

    import glob as glob_mod

    artifact_dir = ctx.root / ".workspace" / "artifacts" / name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    matches = glob_mod.glob(str(ctx.root / path_pattern), recursive=True)
    for match in matches:
        src = Path(match)
        if src.is_file():
            dest = artifact_dir / src.relative_to(ctx.root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    if ctx.verbose:
        print(f"  → upload-artifact: {name} ({len(matches)} files)")
    return StepResult(step=step, returncode=0, skipped=True)


@_action_handler("actions/download-artifact")
def _handle_download_artifact(step: Step, ctx: ExecutionContext) -> StepResult:
    """Restore artifact files from .workspace/artifacts/."""
    name = ctx.resolve_expr(str(step.with_.get("name", "artifact")))
    dest_path = ctx.resolve_expr(str(step.with_.get("path", ".")))

    artifact_dir = ctx.root / ".workspace" / "artifacts" / name
    if not artifact_dir.exists():
        print(f"  ⚠ artifact '{name}' not found")
        return StepResult(step=step, returncode=0, skipped=True)

    dest = ctx.root / dest_path
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(artifact_dir, dest, dirs_exist_ok=True)

    if ctx.verbose:
        print(f"  → download-artifact: {name} → {dest_path}")
    return StepResult(step=step, returncode=0, skipped=True)


@_action_handler("actions/cache")
def _handle_cache(step: Step, ctx: ExecutionContext) -> StepResult:
    """Local cache using .workspace/cache/."""
    key = ctx.resolve_expr(str(step.with_.get("key", "")))
    path_pattern = ctx.resolve_expr(str(step.with_.get("path", "")))

    if not key or not path_pattern:
        return StepResult(step=step, returncode=0, skipped=True)

    cache_dir = ctx.root / ".workspace" / "cache" / key

    if cache_dir.exists():
        # Restore from cache
        target = ctx.root / path_pattern
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cache_dir, target, dirs_exist_ok=True)
        if ctx.verbose:
            print(f"  → cache hit: {key}")
    else:
        # Save to cache after job (handled at end of job)
        if ctx.verbose:
            print(f"  → cache miss: {key} (will save after job)")

    return StepResult(step=step, returncode=0, skipped=True)


def _find_action_handler(uses: str):
    """Match a uses: string to a registered handler."""
    for pattern, handler in ACTION_HANDLERS.items():
        if uses.startswith(pattern):
            return handler
    return None


def _execute_step_local(step: Step, ctx: ExecutionContext) -> StepResult:
    """Execute a step on the local machine."""
    cmd = ctx.resolve_expr(step.run)

    cwd = ctx.root
    if step.working_directory:
        cwd = ctx.root / ctx.resolve_expr(step.working_directory)

    # Build environment: base + workflow env + GITHUB_* env files from prior steps
    step_env = {**os.environ, **ctx.env, **{k: ctx.resolve_expr(v) for k, v in step.env.items()}}

    # Inject workspace env vars if available
    if ctx.job_workspace:
        step_env.update(ctx.job_workspace.get_github_env_vars())
        # Apply PATH additions from prior steps
        path_additions = ctx.job_workspace.read_path_additions()
        if path_additions:
            step_env["PATH"] = ":".join(path_additions) + ":" + step_env.get("PATH", "")
        # Apply env vars from GITHUB_ENV written by prior steps
        step_env.update(ctx.job_workspace.read_env_file())

    shell = step.shell or "bash"

    if ctx.verbose:
        print(f"  $ {cmd.strip()}")

    shell_args, script = _build_shell_cmd(shell, cmd)
    timeout = step.timeout_minutes * 60 if step.timeout_minutes else None

    try:
        result = subprocess.run(
            shell_args,
            input=script,
            cwd=cwd,
            env=step_env,
            capture_output=not ctx.verbose,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  ⚠ timed out after {step.timeout_minutes}m")
        return StepResult(step=step, returncode=1, timed_out=True)

    if result.returncode != 0 and not ctx.verbose:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

    return StepResult(
        step=step,
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


def _execute_step_container(step: Step, ctx: ExecutionContext) -> StepResult:
    """Execute a step inside the container session."""
    from runner.workflow.container import ContainerSession

    session: ContainerSession = ctx.container_session
    cmd = ctx.resolve_expr(step.run)

    workdir = session.workdir
    if step.working_directory:
        workdir = f"{session.workdir}/{ctx.resolve_expr(step.working_directory)}"

    step_env = {**ctx.env, **{k: ctx.resolve_expr(v) for k, v in step.env.items()}}
    shell = step.shell or "bash"

    if ctx.verbose:
        print(f"  $ {cmd.strip()}")

    result = session.exec(
        cmd,
        shell=shell,
        workdir=workdir,
        env=step_env,
        capture_output=not ctx.verbose,
    )

    if result.returncode != 0 and not ctx.verbose:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

    return StepResult(
        step=step,
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


def _execute_action(step: Step, ctx: ExecutionContext) -> StepResult:
    """Resolve and execute a `uses:` action."""
    from runner.workflow.actions import execute_action, resolve_action
    from runner.workflow.parser import _parse_step
    from runner.workflow.policy import ActionPolicy, PolicyStore

    cache_dir = ctx.root / ".workspace" / "actions"
    cache_dir.mkdir(parents=True, exist_ok=True)

    uses = ctx.resolve_expr(step.uses)

    # Check action policy before doing anything
    policy_store = PolicyStore(ctx.root)
    policy = policy_store.check(uses, verbose=ctx.verbose)

    if policy == ActionPolicy.SKIP:
        if ctx.verbose:
            print(f"  → policy: skip ({uses})")
        return StepResult(step=step, returncode=0, skipped=True)

    if policy == ActionPolicy.FORBID:
        print(f"  ✗ action forbidden by policy: {uses}")
        return StepResult(step=step, returncode=1)

    if ctx.dry_run:
        print(f"  [dry-run] would resolve and execute action: {uses}")
        return StepResult(step=step, returncode=0, skipped=True)

    if ctx.verbose:
        print(f"  → resolving action: {uses}")

    action = resolve_action(uses, root=ctx.root, cache_dir=cache_dir)
    if action is None:
        print(f"  ⚠ could not resolve action: {uses} (skipping)")
        return StepResult(step=step, returncode=0, skipped=True)

    with_ = {k: ctx.resolve_expr(str(v)) for k, v in step.with_.items()}
    result = execute_action(
        action,
        with_=with_,
        env={**ctx.env, **{k: ctx.resolve_expr(v) for k, v in step.env.items()}},
        root=ctx.root,
        verbose=ctx.verbose,
        capture_output=not ctx.verbose,
    )

    # Composite actions return steps to inline
    if result.composite_steps:
        if ctx.verbose:
            print(f"  → composite action ({len(result.composite_steps)} steps)")
        for raw_step in result.composite_steps:
            composite_step = _parse_step(raw_step)
            name = composite_step.display_name
            if ctx.verbose:
                print(f"    ▸ {name}")
            sub_result = execute_step(composite_step, ctx)
            if not sub_result.success and not composite_step.continue_on_error:
                return StepResult(step=step, returncode=sub_result.returncode)
        return StepResult(step=step, returncode=0)

    # Apply env vars exported by the action (e.g. JAVA_HOME from setup-java)
    if result.exported_env:
        ctx.env.update(result.exported_env)
        if ctx.verbose:
            for k in result.exported_env:
                print(f"    → export {k}={result.exported_env[k][:60]}")

    # Apply PATH additions from the action
    if result.exported_path:
        if ctx.job_workspace:
            for p in result.exported_path:
                ctx.job_workspace.append_path(p)
        else:
            # No workspace — apply directly to ctx.env["PATH"]
            current = ctx.env.get("PATH", os.environ.get("PATH", ""))
            ctx.env["PATH"] = ":".join(result.exported_path) + ":" + current
        if ctx.verbose:
            print(f"    → PATH += {len(result.exported_path)} entries")

    # Store outputs if the step has an id
    if step.id and result.outputs:
        from runner.workflow.expressions import StepContext
        ctx.steps[step.id] = StepContext(outputs=result.outputs, outcome="success" if result.success else "failure")

    if not result.success:
        if result.error:
            print(f"  ⚠ {result.error}")

    return StepResult(step=step, returncode=result.returncode)


def execute_step(step: Step, ctx: ExecutionContext) -> StepResult:
    """Execute a single workflow step."""
    # Evaluate `if:` condition
    should_run = evaluate_condition(step.if_, ctx.expr_context())
    if not should_run:
        if ctx.verbose:
            print(f"  → skipped (condition: {step.if_})")
        return StepResult(step=step, returncode=0, skipped=True)

    if step.is_action:
        handler = _find_action_handler(step.uses)
        if handler:
            return handler(step, ctx)

        if ctx.skip_actions:
            if ctx.verbose:
                print(f"  → skipping action: {step.uses}")
            return StepResult(step=step, returncode=0, skipped=True)

        return _execute_action(step, ctx)

    if not step.is_run:
        return StepResult(step=step, returncode=0, skipped=True)

    if ctx.dry_run:
        cmd = ctx.resolve_expr(step.run)
        cwd = ctx.root
        if step.working_directory:
            cwd = ctx.root / ctx.resolve_expr(step.working_directory)
        mode = "container" if ctx.container_session else "local"
        print(f"  [dry-run:{mode}] would execute in {cwd}:")
        for line in cmd.strip().split("\n"):
            print(f"    $ {line}")
        return StepResult(step=step, returncode=0, skipped=True)

    if ctx.container_session:
        result = _execute_step_container(step, ctx)
    else:
        result = _execute_step_local(step, ctx)

    # Collect step outputs if the step has an id
    if step.id and ctx.job_workspace:
        outputs = ctx.job_workspace.read_outputs()
        ctx.steps[step.id] = StepContext(outputs=outputs, outcome=result.outcome)
        ctx.job_workspace.reset_step_files()

    return result


def _build_shell_cmd(shell: str, script: str) -> tuple[list[str], str]:
    """Build the shell args and script to pass to subprocess.

    Returns (argv, stdin_script).
    """
    import shutil as _shutil

    if shell == "bash":
        bash = _shutil.which("bash") or "/bin/bash"
        return [bash, "-eo", "pipefail"], script
    if shell == "sh":
        return ["/bin/sh", "-e"], script
    if shell == "python":
        python = _shutil.which("python3") or _shutil.which("python") or "python3"
        return [python], script
    return ["/bin/sh", "-e"], script


def _step_matches_skip(step: Step, skip_patterns: list[str]) -> bool:
    """Check if a step matches any of the skip patterns (by name, id, or uses)."""
    for pattern in skip_patterns:
        p = pattern.lower()
        if step.name and p in step.name.lower():
            return True
        if step.id and p == step.id.lower():
            return True
        if step.uses and p in step.uses.lower():
            return True
    return False


def _job_matches_skip(job: Job, skip_patterns: list[str]) -> bool:
    """Check if a job matches any of the skip patterns (by id or name)."""
    for pattern in skip_patterns:
        p = pattern.lower()
        if p in job.id.lower():
            return True
        if job.name and p in job.name.lower():
            return True
    return False


def _matrix_matches(combo: dict[str, Any], selector: dict[str, Any]) -> bool:
    """Check if a matrix combination matches the user's selection."""
    for key, val in selector.items():
        if key not in combo:
            return False
        if str(combo[key]) != str(val):
            return False
    return True


def _resolve_container_for_job(job: Job, ctx: ExecutionContext):
    """Determine if this job should run in a container."""
    from runner.workflow.container import (
        ContainerSession,
        detect_runtime,
        local_os_matches,
        resolve_image,
    )

    if ctx.ignore_runs_on:
        return None

    runs_on = job.runs_on
    if not runs_on:
        return None

    if local_os_matches(runs_on):
        if ctx.verbose:
            print(f"  → runs-on '{runs_on}' matches local OS, running natively")
        return None

    image = resolve_image(runs_on)
    if not image:
        print(f"  ⚠ runs-on '{runs_on}' has no container mapping, running locally")
        return None

    runtime = detect_runtime()
    if not runtime:
        print("  ⚠ no container runtime (podman/docker) found, running locally")
        return None

    if not runtime.image_exists(image):
        if ctx.verbose:
            print(f"  → pulling image: {image}")
        if not runtime.pull(image, quiet=not ctx.verbose):
            print(f"  ⚠ failed to pull {image}, running locally")
            return None

    session = ContainerSession.start(
        runtime,
        image,
        mount=ctx.root,
        env=ctx.env,
        verbose=ctx.verbose,
    )
    return session


def _start_services(job: Job, ctx: ExecutionContext) -> list:
    """Start sidecar service containers for a job. Returns container IDs to stop later."""
    if not job.services or ctx.dry_run or ctx.ignore_runs_on:
        return []

    from runner.workflow.container import detect_runtime

    runtime = detect_runtime()
    if not runtime:
        if job.services:
            print("  ⚠ services require podman/docker but none found")
        return []

    containers = []
    for name, svc in job.services.items():
        args = [runtime.executable, "run", "-d", "--rm", f"--name=runner-svc-{name}"]

        for port in svc.ports:
            args.extend(["-p", port])
        for vol in svc.volumes:
            args.extend(["-v", vol])
        for k, v in svc.env.items():
            args.extend(["-e", f"{k}={v}"])
        if svc.options:
            args.extend(svc.options.split())

        args.append(svc.image)

        if ctx.verbose:
            print(f"  → starting service: {name} ({svc.image})")

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode == 0:
            containers.append((runtime.executable, result.stdout.strip(), name))
        else:
            print(f"  ⚠ failed to start service {name}: {result.stderr.strip()}")

    return containers


def _stop_services(containers: list):
    """Stop sidecar service containers."""
    for exe, container_id, name in containers:
        subprocess.run([exe, "stop", container_id], capture_output=True)


def _resolve_job_container(job: Job, ctx: ExecutionContext):
    """Handle the job-level `container:` key (explicit image for the job)."""
    if not job.container or ctx.ignore_runs_on or ctx.dry_run:
        return None

    from runner.workflow.container import ContainerSession, detect_runtime

    runtime = detect_runtime()
    if not runtime:
        print("  ⚠ job container requires podman/docker but none found")
        return None

    image = job.container
    if not runtime.image_exists(image):
        if ctx.verbose:
            print(f"  → pulling job container: {image}")
        if not runtime.pull(image, quiet=not ctx.verbose):
            print(f"  ⚠ failed to pull {image}")
            return None

    return ContainerSession.start(
        runtime, image, mount=ctx.root, env=ctx.env, verbose=ctx.verbose
    )


def execute_job(job: Job, ctx: ExecutionContext) -> list[JobResult]:
    """Execute a job, expanding matrix if present."""
    results = []

    if job.strategy:
        combinations = job.strategy.expand()
    else:
        combinations = [{}]

    if ctx.matrix:
        combinations = [c for c in combinations if _matrix_matches(c, ctx.matrix)]
        if not combinations:
            print(f"  ⚠ no matrix combination matches: {ctx.matrix}")
            return results

    workspace = Workspace(ctx.root)
    workspace.setup()

    # Resolve defaults (job-level overrides workflow-level)
    default_shell = None
    default_wd = None
    if ctx.workflow_defaults:
        default_shell = ctx.workflow_defaults.run_shell
        default_wd = ctx.workflow_defaults.run_working_directory
    if job.defaults:
        default_shell = job.defaults.run_shell or default_shell
        default_wd = job.defaults.run_working_directory or default_wd

    # Start services
    services = _start_services(job, ctx)

    try:
        for matrix_values in combinations:
            matrix_label = "-".join(f"{v}" for v in matrix_values.values()) if matrix_values else ""

            job_ws = workspace.create_job_dir(job.id, matrix_label)
            job_ws.setup(clone=False, verbose=ctx.verbose)

            job_ctx = ExecutionContext(
                root=ctx.root,
                env={**ctx.env, **job.env},
                matrix=matrix_values,
                dry_run=ctx.dry_run,
                verbose=ctx.verbose,
                skip_actions=ctx.skip_actions,
                ignore_runs_on=ctx.ignore_runs_on,
                github=ctx.github,
                secret_store=ctx.secret_store,
                job_workspace=job_ws,
                workflow_defaults=ctx.workflow_defaults,
                skip_steps=ctx.skip_steps,
            )

            # Resolve container: job-level container key takes precedence over runs-on
            container = None
            if not ctx.dry_run:
                if job.container:
                    container = _resolve_job_container(job, job_ctx)
                else:
                    container = _resolve_container_for_job(job, job_ctx)
                job_ctx.container_session = container

            job_result = JobResult(job=job, matrix_values=matrix_values)

            if matrix_values:
                label = ", ".join(f"{k}={v}" for k, v in matrix_values.items())
                print(f"\n  [{label}]")

            try:
                for step in job.steps:
                    # Apply defaults to steps that don't specify shell/working-directory
                    if default_shell and not step.shell:
                        step.shell = default_shell
                    if default_wd and not step.working_directory:
                        step.working_directory = default_wd

                    name = step.display_name

                    # Check if step should be skipped via --skip
                    if job_ctx.skip_steps and _step_matches_skip(step, job_ctx.skip_steps):
                        print(f"  ▸ {name} (skipped)")
                        job_result.step_results.append(StepResult(step=step, returncode=0, skipped=True))
                        continue

                    print(f"  ▸ {name}")

                    step_result = execute_step(step, job_ctx)
                    job_result.step_results.append(step_result)

                    if not step_result.success:
                        if step.continue_on_error:
                            print(f"  ⚠ {name} failed (continue-on-error)")
                        else:
                            print(f"  ✗ {name} (exit {step_result.returncode})")
                            job_ctx.job_status = "failure"
                            if job.strategy and job.strategy.fail_fast:
                                break
                            break
                    elif not step_result.skipped and ctx.verbose:
                        print(f"  ✓ {name}")
            finally:
                if container:
                    container.stop()

            results.append(job_result)

            if not job_result.success and (not job.strategy or job.strategy.fail_fast):
                break
    finally:
        _stop_services(services)

    return results


def _execute_reusable_workflow(
    job: Job,
    ctx: ExecutionContext,
    *,
    root: Path,
    dry_run: bool,
    verbose: bool,
    skip_actions: bool,
    ignore_runs_on: bool,
) -> bool:
    """Execute a reusable workflow referenced by a job's `uses:` field."""
    uses = job.uses
    if not uses:
        return True

    # Resolve the workflow path (local reference ./.github/workflows/xxx.yml)
    if uses.startswith("./"):
        wf_path = root / uses[2:]
    elif uses.startswith(".github/"):
        wf_path = root / uses
    else:
        print(f"  ⚠ remote reusable workflows not yet supported: {uses}")
        return True

    if not wf_path.exists():
        print(f"  ✗ reusable workflow not found: {wf_path}")
        return False

    if verbose:
        print(f"  → calling reusable workflow: {uses}")

    reusable_wf = parse_workflow(wf_path)

    # Pass inputs (with:) as env variables prefixed INPUT_
    input_env = {}
    for k, v in job.with_.items():
        input_env[f"INPUT_{k.upper().replace('-', '_')}"] = str(v)

    return execute_workflow(
        reusable_wf,
        root=root,
        dry_run=dry_run,
        verbose=verbose,
        skip_actions=skip_actions,
        ignore_runs_on=ignore_runs_on,
        env={**ctx.env, **input_env},
    )


def execute_workflow(
    workflow: Workflow,
    *,
    job_filter: str | None = None,
    step_filter: str | None = None,
    root: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    skip_actions: bool = False,
    ignore_runs_on: bool = False,
    env: dict[str, str] | None = None,
    matrix: dict[str, str] | None = None,
    skip_steps: list[str] | None = None,
    skip_jobs: list[str] | None = None,
    inputs: dict[str, str] | None = None,
) -> bool:
    """Execute a workflow (or a subset of its jobs/steps).

    Returns True if all executed jobs succeeded.
    """
    if root is None:
        root = Path.cwd()

    github_ctx = build_github_context(root)
    secret_store = create_default_store(root)

    # Resolve workflow_dispatch inputs: user-provided values override defaults
    resolved_inputs = {}
    for name, defn in workflow.dispatch_inputs.items():
        resolved_inputs[name] = defn.default
    if inputs:
        resolved_inputs.update(inputs)

    ctx = ExecutionContext(
        root=root,
        env={**workflow.env, **(env or {})},
        matrix=matrix or {},
        dry_run=dry_run,
        verbose=verbose,
        skip_actions=skip_actions,
        ignore_runs_on=ignore_runs_on,
        github=github_ctx,
        secret_store=secret_store,
        workflow_defaults=workflow.defaults,
        skip_steps=skip_steps or [],
        inputs=resolved_inputs,
    )

    job_ids = workflow.job_order()

    if job_filter:
        job_ids = [j for j in job_ids if j == job_filter or workflow.jobs[j].display_name == job_filter]
        if not job_ids:
            available = ", ".join(workflow.jobs.keys())
            print(f"Job '{job_filter}' not found. Available: {available}")
            return False

    all_success = True
    job_outputs: dict[str, dict[str, str]] = {}  # job_id -> {key: value}
    job_statuses: dict[str, str] = {}  # job_id -> "success" | "failure" | "skipped"

    _skip_jobs = skip_jobs or []

    for job_id in job_ids:
        job = workflow.jobs[job_id]

        # Check if job should be skipped via --skip_jobs
        if _skip_jobs and _job_matches_skip(job, _skip_jobs):
            print(f"\n{'─' * 60}")
            print(f"Job: {job.display_name} (skipped by --skip_jobs)")
            print(f"{'─' * 60}")
            job_statuses[job_id] = "success"
            continue

        # Evaluate job-level if: condition
        if job.if_ is not None:
            job_expr_ctx = ctx.expr_context()
            should_run = evaluate_condition(job.if_, job_expr_ctx)
            if not should_run:
                print(f"\n{'─' * 60}")
                print(f"Job: {job.display_name} (skipped: {job.if_})")
                print(f"{'─' * 60}")
                job_statuses[job_id] = "skipped"
                continue

        # Check if all dependencies succeeded
        deps_ok = all(job_statuses.get(dep) == "success" for dep in job.needs)
        if not deps_ok:
            print(f"\n{'─' * 60}")
            print(f"Job: {job.display_name} (skipped: dependency failed)")
            print(f"{'─' * 60}")
            job_statuses[job_id] = "skipped"
            continue

        print(f"\n{'─' * 60}")
        print(f"Job: {job.display_name}")
        print(f"{'─' * 60}")

        # Make dependency outputs available via needs.<job>.outputs.*
        ctx.needs = {
            dep_id: {"outputs": job_outputs.get(dep_id, {}), "result": job_statuses.get(dep_id, "")}
            for dep_id in job.needs
        }

        # Handle reusable workflow calls (job uses: instead of steps:)
        if job.uses:
            job_success = _execute_reusable_workflow(
                job, ctx, root=root, dry_run=dry_run, verbose=verbose,
                skip_actions=skip_actions, ignore_runs_on=ignore_runs_on,
            )
        else:
            if step_filter:
                job.steps = [s for s in job.steps if s.name and step_filter.lower() in s.name.lower()]

            results = execute_job(job, ctx)
            job_success = all(r.success for r in results)

        if job_success:
            job_statuses[job_id] = "success"
            # Collect job outputs (resolve expressions against step outputs)
            if job.outputs and results:
                last_ctx = ctx  # Use the workflow ctx for resolving
                job_outputs[job_id] = {
                    k: last_ctx.resolve_expr(v) for k, v in job.outputs.items()
                }
        else:
            job_statuses[job_id] = "failure"
            all_success = False

    print(f"\n{'═' * 60}")
    if all_success:
        print("✓ All jobs passed")
    else:
        print("✗ Some jobs failed")
    print(f"{'═' * 60}")

    return all_success


