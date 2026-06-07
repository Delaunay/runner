"""Native GitLab CI/CD executor.

Executes pipelines using GitLab-native semantics:
- Stage-based parallelism/ordering
- before_script → script → after_script lifecycle
- GitLab variable expansion ($VAR, ${VAR})
- Artifacts passing between jobs
- Service containers with alias-based hostnames
- Rules-based job inclusion/exclusion
- retry on failure
- allow_failure handling

Reuses lower-level primitives from runner.workflow for:
- Subprocess execution
- Container management (podman/docker)
- Workspace / .workspace directory structure
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runner.gitlab.parser import (
    GitLabJob,
    GitLabPipeline,
    GitLabRule,
    GitLabService,
)


@dataclass
class ScriptResult:
    """Result of running a script block."""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass
class JobResult:
    """Result of a single job execution."""

    job: GitLabJob
    status: str = "success"  # success | failed | skipped | manual
    allow_failure: bool = False
    before_script_result: ScriptResult | None = None
    script_result: ScriptResult | None = None
    after_script_result: ScriptResult | None = None

    @property
    def success(self) -> bool:
        if self.status in ("success", "skipped", "manual"):
            return True
        if self.status == "failed" and self.allow_failure:
            return True
        return False


@dataclass
class PipelineResult:
    """Result of executing an entire pipeline."""

    pipeline: GitLabPipeline
    job_results: dict[str, JobResult] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return all(r.success for r in self.job_results.values())

    @property
    def failed_jobs(self) -> list[str]:
        return [name for name, r in self.job_results.items() if not r.success]


@dataclass
class PipelineContext:
    """Runtime context for pipeline execution."""

    root: Path
    variables: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False
    verbose: bool = False
    no_container: bool = False
    skip_jobs: list[str] = field(default_factory=list)
    only_jobs: list[str] = field(default_factory=list)
    only_stage: str = ""

    # CI predefined variables
    ci_vars: dict[str, str] = field(default_factory=dict)

    # Collected artifacts from previous jobs
    artifacts: dict[str, list[Path]] = field(default_factory=dict)

    # Job results for needs-based dependencies
    job_statuses: dict[str, str] = field(default_factory=dict)


def execute_pipeline(
    pipeline: GitLabPipeline,
    *,
    root: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    no_container: bool = False,
    variables: dict[str, str] | None = None,
    skip_jobs: list[str] | None = None,
    only_jobs: list[str] | None = None,
    only_stage: str = "",
) -> PipelineResult:
    """Execute a GitLab CI/CD pipeline.

    Jobs are executed stage-by-stage. Within a stage, jobs are independent.
    """
    if root is None:
        root = Path.cwd()

    ci_vars = _build_ci_variables(root, pipeline)

    ctx = PipelineContext(
        root=root,
        variables={**pipeline.variables, **(variables or {})},
        dry_run=dry_run,
        verbose=verbose,
        no_container=no_container,
        skip_jobs=skip_jobs or [],
        only_jobs=only_jobs or [],
        only_stage=only_stage,
        ci_vars=ci_vars,
    )

    result = PipelineResult(pipeline=pipeline)

    # Ensure .workspace exists
    workspace_dir = root / ".workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "artifacts").mkdir(exist_ok=True)
    (workspace_dir / "cache").mkdir(exist_ok=True)

    stages = pipeline.active_stages()
    if only_stage:
        if only_stage not in stages:
            print(f"Stage '{only_stage}' not found. Available: {', '.join(stages)}")
            return result
        stages = [only_stage]

    print(f"Pipeline: {pipeline.name}")
    print(f"Stages: {' → '.join(stages)}")
    print()

    for stage in stages:
        jobs = pipeline.jobs_for_stage(stage)
        if not jobs:
            continue

        print(f"{'─' * 60}")
        print(f"Stage: {stage}")
        print(f"{'─' * 60}")

        stage_success = True
        for job in jobs:
            job_result = _execute_job(job, ctx, pipeline)
            result.job_results[job.name] = job_result
            ctx.job_statuses[job.name] = job_result.status

            if not job_result.success:
                stage_success = False

        if not stage_success:
            # Stage failed — remaining stages are skipped by default
            print(f"\n  Stage '{stage}' failed. Stopping pipeline.")
            break

    print(f"\n{'═' * 60}")
    if result.success:
        print("Pipeline: PASSED")
    else:
        failed = result.failed_jobs
        print(f"Pipeline: FAILED ({', '.join(failed)})")
    print(f"{'═' * 60}")

    return result


def _execute_job(job: GitLabJob, ctx: PipelineContext, pipeline: GitLabPipeline) -> JobResult:
    """Execute a single GitLab CI job."""
    result = JobResult(job=job, allow_failure=job.allow_failure)

    # Check skip/only filters
    if ctx.skip_jobs and any(p.lower() in job.name.lower() for p in ctx.skip_jobs):
        print(f"\n  Job: {job.name} (skipped by filter)")
        result.status = "skipped"
        return result

    if ctx.only_jobs and not any(p.lower() in job.name.lower() for p in ctx.only_jobs):
        print(f"\n  Job: {job.name} (filtered out)")
        result.status = "skipped"
        return result

    # Evaluate rules
    if not _should_run_job(job, ctx):
        print(f"\n  Job: {job.name} (excluded by rules)")
        result.status = "skipped"
        return result

    # Check if job's `when:` is 'manual' — skip unless explicitly included
    if job.when == "manual" and job.name not in ctx.only_jobs:
        print(f"\n  Job: {job.name} (manual — skipped)")
        result.status = "manual"
        return result

    # Check needs-based dependencies
    if job.needs_explicit and job.needs:
        for dep in job.needs:
            dep_status = ctx.job_statuses.get(dep)
            if dep_status not in ("success", None):
                print(f"\n  Job: {job.name} (dependency '{dep}' {dep_status})")
                result.status = "skipped"
                return result

    print(f"\n  Job: {job.name}")

    # Build effective variables for this job
    job_vars = _build_job_variables(job, ctx, pipeline)
    job_env = {**os.environ, **job_vars}

    # Restore artifacts from dependencies
    _restore_artifacts(job, ctx)

    # Restore cache
    _restore_cache(job, ctx)

    # Determine execution strategy
    container = None
    if job.image and not ctx.no_container:
        container = _start_job_container(job, ctx)

    services = []
    if job.services and not ctx.no_container:
        services = _start_services(job.services, ctx)

    try:
        # Execute with retry support
        attempts = max(1, job.retry + 1)
        for attempt in range(attempts):
            if attempt > 0:
                print(f"    ↻ retry ({attempt}/{job.retry})")

            success = _run_job_scripts(job, job_env, ctx, container, result)
            if success:
                break
    finally:
        if container:
            _stop_container(container)
        _stop_services(services)

    # Save artifacts
    if job.artifacts and result.status == "success":
        _save_artifacts(job, ctx)
    elif job.artifacts and job.artifacts.when == "always":
        _save_artifacts(job, ctx)
    elif job.artifacts and job.artifacts.when == "on_failure" and result.status == "failed":
        _save_artifacts(job, ctx)

    # Save cache
    if job.cache:
        _save_cache(job, ctx)

    # Print result
    if result.status == "success":
        print(f"    ✓ {job.name}")
    elif result.allow_failure:
        print(f"    ⚠ {job.name} (allowed failure)")
    else:
        print(f"    ✗ {job.name}")

    return result


def _run_job_scripts(
    job: GitLabJob,
    job_env: dict[str, str],
    ctx: PipelineContext,
    container: Any | None,
    result: JobResult,
) -> bool:
    """Run before_script → script → after_script lifecycle. Returns success."""
    timeout = job.timeout_seconds

    # before_script
    if job.before_script:
        if ctx.verbose:
            print("    [before_script]")
        sr = _run_script_block(
            job.before_script, job_env, ctx, container,
            timeout=timeout, label="before_script",
        )
        result.before_script_result = sr
        if sr.returncode != 0:
            result.status = "failed"
            # after_script still runs even if before_script fails
            if job.after_script:
                _run_after_script(job, job_env, ctx, container)
            return False

    # script (main)
    if job.script:
        if ctx.verbose:
            print("    [script]")
        sr = _run_script_block(
            job.script, job_env, ctx, container,
            timeout=timeout, label="script",
        )
        result.script_result = sr
        if sr.returncode != 0:
            result.status = "failed"
            if job.after_script:
                _run_after_script(job, job_env, ctx, container)
            return False

    # after_script (always runs, failures don't affect job status)
    if job.after_script:
        _run_after_script(job, job_env, ctx, container)

    result.status = "success"
    return True


def _run_after_script(
    job: GitLabJob,
    job_env: dict[str, str],
    ctx: PipelineContext,
    container: Any | None,
):
    """Run after_script — always runs, failures are non-fatal."""
    if ctx.verbose:
        print("    [after_script]")
    _run_script_block(
        job.after_script, job_env, ctx, container,
        timeout=None, label="after_script",
    )


def _run_script_block(
    commands: list[str],
    env: dict[str, str],
    ctx: PipelineContext,
    container: Any | None,
    *,
    timeout: float | None = None,
    label: str = "",
) -> ScriptResult:
    """Execute a list of commands as a script block."""
    # Join all commands into a single script (GitLab behavior)
    script = "\n".join(commands)

    # Expand GitLab-style variables ($VAR, ${VAR})
    script = _expand_variables(script, env)

    if ctx.dry_run:
        for line in commands:
            print(f"      $ {line}")
        return ScriptResult(returncode=0)

    if container:
        return _exec_in_container(script, env, ctx, container, timeout=timeout)
    return _exec_local(script, env, ctx, timeout=timeout)


def _exec_local(
    script: str,
    env: dict[str, str],
    ctx: PipelineContext,
    *,
    timeout: float | None = None,
) -> ScriptResult:
    """Execute a script locally."""
    shell = shutil.which("bash") or "/bin/sh"
    shell_args = [shell, "-eo", "pipefail"] if "bash" in shell else [shell, "-e"]

    if ctx.verbose:
        for line in script.strip().split("\n"):
            print(f"      $ {line}")

    try:
        proc = subprocess.run(
            shell_args,
            input=script,
            cwd=ctx.root,
            env=env,
            capture_output=not ctx.verbose,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print("      ⚠ timed out")
        return ScriptResult(returncode=1, timed_out=True)

    if proc.returncode != 0 and not ctx.verbose:
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)

    return ScriptResult(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def _exec_in_container(
    script: str,
    env: dict[str, str],
    ctx: PipelineContext,
    container: dict,
    *,
    timeout: float | None = None,
) -> ScriptResult:
    """Execute a script inside a container."""
    runtime = container["runtime"]
    container_id = container["id"]

    cmd = [runtime, "exec"]
    for k, v in env.items():
        if k not in os.environ:  # Only pass non-system env vars
            cmd.extend(["-e", f"{k}={v}"])
    cmd.extend(["-w", "/workspace", container_id, "bash", "-eo", "pipefail", "-c", script])

    if ctx.verbose:
        for line in script.strip().split("\n"):
            print(f"      $ {line}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=not ctx.verbose,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print("      ⚠ timed out")
        return ScriptResult(returncode=1, timed_out=True)

    if proc.returncode != 0 and not ctx.verbose:
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)

    return ScriptResult(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


# ──────────────────── Variable Expansion ────────────────────


def _expand_variables(text: str, env: dict[str, str]) -> str:
    """Expand GitLab-style $VAR and ${VAR} references."""
    def replacer(m):
        var_name = m.group(1) or m.group(2)
        return env.get(var_name, m.group(0))

    # Match ${VAR} or $VAR (word characters)
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", replacer, text)


def _build_ci_variables(root: Path, pipeline: GitLabPipeline) -> dict[str, str]:
    """Build GitLab CI predefined variables."""
    ci = {
        "CI": "true",
        "CI_PROJECT_DIR": str(root),
        "CI_PROJECT_PATH": str(root),
        "CI_PIPELINE_SOURCE": "local",
        "CI_JOB_NAME": "",
        "CI_JOB_STAGE": "",
        "GITLAB_CI": "true",
    }

    # Try to get git info
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            ci["CI_COMMIT_SHA"] = result.stdout.strip()
            ci["CI_COMMIT_SHORT_SHA"] = result.stdout.strip()[:8]
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            ci["CI_COMMIT_BRANCH"] = result.stdout.strip()
            ci["CI_COMMIT_REF_NAME"] = result.stdout.strip()
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            ci["CI_REPOSITORY_URL"] = url
            # Extract project name from URL
            name = url.rstrip("/").rstrip(".git").split("/")[-1]
            ci["CI_PROJECT_NAME"] = name
    except FileNotFoundError:
        pass

    return ci


def _build_job_variables(
    job: GitLabJob, ctx: PipelineContext, pipeline: GitLabPipeline,
) -> dict[str, str]:
    """Build the complete variable set for a job."""
    vars_ = {}
    vars_.update(ctx.ci_vars)
    vars_.update(pipeline.variables)
    vars_.update(ctx.variables)
    vars_.update(job.variables)
    vars_["CI_JOB_NAME"] = job.name
    vars_["CI_JOB_STAGE"] = job.stage
    return vars_


# ──────────────────── Rules Evaluation ────────────────────


def _should_run_job(job: GitLabJob, ctx: PipelineContext) -> bool:
    """Evaluate whether a job should run based on rules/only/except."""
    if job.rules:
        return _evaluate_rules(job.rules, ctx)
    if job.only is not None or job.except_ is not None:
        return _evaluate_only_except(job.only, job.except_, ctx)
    return True


def _evaluate_rules(rules: list[GitLabRule], ctx: PipelineContext) -> bool:
    """Evaluate GitLab rules: list. First matching rule wins."""
    all_vars = {**ctx.ci_vars, **ctx.variables}

    for rule in rules:
        if rule.if_:
            if _eval_rule_condition(rule.if_, all_vars):
                return rule.when != "never"
        elif not rule.changes and not rule.exists:
            # Rule with no condition matches unconditionally
            return rule.when != "never"

    # No rule matched → job is excluded
    return False


def _eval_rule_condition(condition: str, variables: dict[str, str]) -> bool:
    """Evaluate a GitLab CI rule condition (simplified).

    Supports:
    - $VAR == "value"
    - $VAR != "value"
    - $VAR (truthy check)
    - $VAR =~ /pattern/
    - $VAR !~ /pattern/
    - && and || combinators
    """
    # Handle || (lower precedence)
    if "||" in condition:
        parts = condition.split("||")
        return any(_eval_rule_condition(p.strip(), variables) for p in parts)

    # Handle &&
    if "&&" in condition:
        parts = condition.split("&&")
        return all(_eval_rule_condition(p.strip(), variables) for p in parts)

    condition = condition.strip()

    # Regex match: $VAR =~ /pattern/
    m = re.match(r'(\$\w+)\s*=~\s*/(.+)/', condition)
    if m:
        var_val = _resolve_var(m.group(1), variables)
        pattern = m.group(2)
        return bool(re.search(pattern, var_val))

    # Regex not-match: $VAR !~ /pattern/
    m = re.match(r'(\$\w+)\s*!~\s*/(.+)/', condition)
    if m:
        var_val = _resolve_var(m.group(1), variables)
        pattern = m.group(2)
        return not bool(re.search(pattern, var_val))

    # Equality: $VAR == "value" or $VAR == 'value'
    m = re.match(r'(\$\w+)\s*==\s*["\'](.+?)["\']', condition)
    if m:
        var_val = _resolve_var(m.group(1), variables)
        return var_val == m.group(2)

    # Inequality: $VAR != "value"
    m = re.match(r'(\$\w+)\s*!=\s*["\'](.+?)["\']', condition)
    if m:
        var_val = _resolve_var(m.group(1), variables)
        return var_val != m.group(2)

    # Null check: $VAR == null
    m = re.match(r'(\$\w+)\s*==\s*null', condition)
    if m:
        var_val = _resolve_var(m.group(1), variables)
        return var_val == ""

    # Truthy check: $VAR (variable is set and non-empty)
    m = re.match(r'^\$(\w+)$', condition)
    if m:
        return bool(variables.get(m.group(1), ""))

    return True


def _resolve_var(ref: str, variables: dict[str, str]) -> str:
    """Resolve a $VAR reference."""
    name = ref.lstrip("$")
    return variables.get(name, "")


def _evaluate_only_except(
    only: list | dict | None,
    except_: list | dict | None,
    ctx: PipelineContext,
) -> bool:
    """Evaluate only/except (legacy) rules."""
    # Simplified: always run unless we explicitly exclude
    if only is not None:
        refs = only if isinstance(only, list) else only.get("refs", [])
        branch = ctx.ci_vars.get("CI_COMMIT_BRANCH", "")
        if refs and branch and branch not in refs:
            return False

    if except_ is not None:
        refs = except_ if isinstance(except_, list) else except_.get("refs", [])
        branch = ctx.ci_vars.get("CI_COMMIT_BRANCH", "")
        if refs and branch and branch in refs:
            return False

    return True


# ──────────────────── Container Management ────────────────────


def _start_job_container(job: GitLabJob, ctx: PipelineContext) -> dict | None:
    """Start a container for the job's image."""
    image = job.image
    if not image:
        return None

    runtime = _find_runtime()
    if not runtime:
        if ctx.verbose:
            print(f"    ⚠ no container runtime for image: {image}")
        return None

    # Pull image if needed
    check = subprocess.run(
        [runtime, "image", "exists", image],
        capture_output=True,
    )
    if check.returncode != 0:
        if ctx.verbose:
            print(f"    → pulling: {image}")
        pull = subprocess.run([runtime, "pull", image], capture_output=True)
        if pull.returncode != 0:
            print(f"    ⚠ failed to pull {image}")
            return None

    # Start container
    cmd = [
        runtime, "run", "-d", "--rm",
        "-v", f"{ctx.root}:/workspace:Z",
        "-w", "/workspace",
        image,
        "sleep", "infinity",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"    ⚠ failed to start container: {proc.stderr.strip()}")
        return None

    container_id = proc.stdout.strip()
    if ctx.verbose:
        print(f"    → container: {image} ({container_id[:12]})")

    return {"runtime": runtime, "id": container_id, "image": image}


def _stop_container(container: dict):
    """Stop and remove a job container."""
    subprocess.run(
        [container["runtime"], "stop", container["id"]],
        capture_output=True,
    )


def _start_services(services: list[GitLabService], ctx: PipelineContext) -> list[dict]:
    """Start service containers."""
    runtime = _find_runtime()
    if not runtime:
        return []

    started = []
    for svc in services:
        cmd = [runtime, "run", "-d", "--rm", f"--name=gitlab-svc-{svc.hostname}"]

        for k, v in svc.variables.items():
            cmd.extend(["-e", f"{k}={v}"])
        if svc.entrypoint:
            cmd.extend(["--entrypoint", svc.entrypoint[0]])
        cmd.append(svc.name)
        if svc.command:
            cmd.extend(svc.command)

        if ctx.verbose:
            print(f"    → service: {svc.hostname} ({svc.name})")

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            started.append({"runtime": runtime, "id": proc.stdout.strip(), "name": svc.hostname})
        else:
            print(f"    ⚠ service {svc.hostname} failed: {proc.stderr.strip()}")

    return started


def _stop_services(services: list[dict]):
    """Stop service containers."""
    for svc in services:
        subprocess.run(
            [svc["runtime"], "stop", svc["id"]],
            capture_output=True,
        )


def _find_runtime() -> str | None:
    """Find podman or docker."""
    for rt in ("podman", "docker"):
        if shutil.which(rt):
            return rt
    return None


# ──────────────────── Artifacts ────────────────────


def _save_artifacts(job: GitLabJob, ctx: PipelineContext):
    """Save job artifacts to .workspace/artifacts/<job_name>/."""
    if not job.artifacts or not job.artifacts.paths:
        return

    import glob as glob_mod

    artifact_dir = ctx.root / ".workspace" / "artifacts" / job.name
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True)

    count = 0
    for pattern in job.artifacts.paths:
        matches = glob_mod.glob(str(ctx.root / pattern), recursive=True)
        for match in matches:
            src = Path(match)
            if src.is_file():
                # Exclude patterns
                rel = src.relative_to(ctx.root)
                if job.artifacts.exclude and any(
                    rel.match(ex) for ex in job.artifacts.exclude
                ):
                    continue
                dest = artifact_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                count += 1

    if ctx.verbose:
        print(f"    → artifacts: saved {count} files")
    ctx.artifacts[job.name] = list(artifact_dir.rglob("*"))


def _restore_artifacts(job: GitLabJob, ctx: PipelineContext):
    """Restore artifacts from dependency jobs."""
    # If dependencies is explicitly set, use that list
    # Otherwise, use all previous jobs in the same or earlier stages
    deps = job.dependencies if job.dependencies is not None else job.needs

    for dep_name in deps:
        artifact_dir = ctx.root / ".workspace" / "artifacts" / dep_name
        if artifact_dir.is_dir():
            shutil.copytree(artifact_dir, ctx.root, dirs_exist_ok=True)
            if ctx.verbose:
                print(f"    → restored artifacts from: {dep_name}")


# ──────────────────── Cache ────────────────────


def _restore_cache(job: GitLabJob, ctx: PipelineContext):
    """Restore cache for a job."""
    if not job.cache or not job.cache.paths:
        return
    if job.cache.policy == "push":
        return

    key = _expand_variables(job.cache.key, {**ctx.ci_vars, **ctx.variables, **job.variables})
    cache_dir = ctx.root / ".workspace" / "cache" / key

    if not cache_dir.is_dir():
        return

    for cached_path in job.cache.paths:
        src = cache_dir / cached_path
        dest = ctx.root / cached_path
        if src.exists():
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

    if ctx.verbose:
        print(f"    → cache restored: {key}")


def _save_cache(job: GitLabJob, ctx: PipelineContext):
    """Save cache after job execution."""
    if not job.cache or not job.cache.paths:
        return
    if job.cache.policy == "pull":
        return

    key = _expand_variables(job.cache.key, {**ctx.ci_vars, **ctx.variables, **job.variables})
    cache_dir = ctx.root / ".workspace" / "cache" / key
    cache_dir.mkdir(parents=True, exist_ok=True)

    for cache_path in job.cache.paths:
        src = ctx.root / cache_path
        dest = cache_dir / cache_path
        if src.exists():
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

    if ctx.verbose:
        print(f"    → cache saved: {key}")
