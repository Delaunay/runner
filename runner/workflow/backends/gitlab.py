"""GitLab CI/CD backend — parses .gitlab-ci.yml into the shared IR.

Translates GitLab CI concepts to the Workflow/Job/Step intermediate representation:
- stages -> job ordering via needs
- script/before_script/after_script -> Step objects
- image -> Job.container
- services -> Job.services
- variables -> env
- rules/only/except -> Job.if_ (simplified)
- extends/.template -> resolved at parse time
- needs -> Job.needs
- artifacts -> handled via workspace
- cache -> mapped to cache handler
- timeout -> Job.timeout_minutes
- allow_failure -> Step.continue_on_error
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from runner.workflow.parser import (
    Defaults,
    Job,
    ServiceDef,
    Step,
    Strategy,
    Workflow,
)

GITLAB_RESERVED_KEYS = {
    "stages",
    "variables",
    "default",
    "include",
    "workflow",
    "image",
    "before_script",
    "after_script",
    "services",
    "cache",
    "pages",
}


class GitLabBackend:
    """Backend for GitLab CI/CD (.gitlab-ci.yml)."""

    name: str = "gitlab"

    def detect(self, root: Path) -> bool:
        return (root / ".gitlab-ci.yml").is_file()

    def discover(self, root: Path) -> dict[str, Workflow]:
        ci_file = root / ".gitlab-ci.yml"
        if not ci_file.is_file():
            return {}
        try:
            wf = self.parse(ci_file)
            return {wf.name: wf}
        except Exception as e:
            print(f"Warning: failed to parse .gitlab-ci.yml: {e}")
            return {}

    def parse(self, path: Path) -> Workflow:
        return parse_gitlab_ci(path)


def parse_gitlab_ci(path: Path) -> Workflow:
    """Parse a .gitlab-ci.yml file into the shared Workflow IR."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty CI file: {path}")

    # Resolve includes (local only for now)
    raw = _resolve_includes(raw, path.parent)

    # Extract global config
    stages = raw.get("stages", ["build", "test", "deploy"])
    global_vars = {k: str(v) for k, v in raw.get("variables", {}).items()}
    defaults = raw.get("default", {})
    global_image = raw.get("image") or defaults.get("image")
    global_before = raw.get("before_script") or defaults.get("before_script", [])
    global_after = raw.get("after_script") or defaults.get("after_script", [])
    global_services = raw.get("services") or defaults.get("services", [])

    default_shell = None
    if defaults.get("shell"):
        default_shell = defaults["shell"]

    # Collect templates (keys starting with .)
    templates: dict[str, dict] = {}
    for key, val in raw.items():
        if key.startswith(".") and isinstance(val, dict):
            templates[key] = val

    # Parse jobs (non-reserved, non-template keys)
    jobs: dict[str, Job] = {}
    for key, val in raw.items():
        if key in GITLAB_RESERVED_KEYS:
            continue
        if key.startswith("."):
            continue
        if not isinstance(val, dict):
            continue

        job = _parse_gitlab_job(
            key,
            val,
            templates=templates,
            stages=stages,
            global_image=global_image,
            global_before=global_before,
            global_after=global_after,
            global_services=global_services,
            global_vars=global_vars,
        )
        jobs[key] = job

    # Build needs from stage ordering if not explicitly set
    _infer_needs_from_stages(jobs, stages)

    wf_defaults = None
    if default_shell:
        wf_defaults = Defaults(run_shell=default_shell)

    return Workflow(
        name=path.stem,
        path=path,
        on={"push": None},
        jobs=jobs,
        env=global_vars,
        defaults=wf_defaults,
    )


def _parse_gitlab_job(
    job_id: str,
    raw: dict[str, Any],
    *,
    templates: dict[str, dict],
    stages: list[str],
    global_image: str | None,
    global_before: list[str],
    global_after: list[str],
    global_services: list,
    global_vars: dict[str, str],
) -> Job:
    """Parse a single GitLab CI job into the shared IR."""
    # Resolve extends (template inheritance)
    raw = _resolve_extends(raw, templates)

    stage = raw.get("stage", "test")
    image = raw.get("image") or global_image
    variables = {**global_vars, **{k: str(v) for k, v in raw.get("variables", {}).items()}}

    # Build steps from script blocks
    before_script = raw.get("before_script", global_before)
    script = raw.get("script", [])
    after_script = raw.get("after_script", global_after)

    steps: list[Step] = []

    if before_script:
        steps.append(Step(
            name="before_script",
            run="\n".join(str(s) for s in before_script),
        ))

    if script:
        steps.append(Step(
            name=raw.get("name", job_id),
            run="\n".join(str(s) for s in script),
            continue_on_error=bool(raw.get("allow_failure", False)),
        ))

    if after_script:
        steps.append(Step(
            name="after_script",
            run="\n".join(str(s) for s in after_script),
            continue_on_error=True,
        ))

    # Parse services
    services_raw = raw.get("services", global_services)
    services = _parse_services(services_raw)

    # Parse needs
    needs_raw = raw.get("needs", [])
    needs = []
    if isinstance(needs_raw, list):
        for n in needs_raw:
            if isinstance(n, str):
                needs.append(n)
            elif isinstance(n, dict) and "job" in n:
                needs.append(n["job"])

    # Parse rules -> if_ condition (simplified)
    if_ = _rules_to_condition(raw.get("rules"), raw.get("only"), raw.get("except"))

    # Timeout
    timeout = raw.get("timeout")
    timeout_minutes = _parse_timeout(timeout)

    # Strategy/parallel
    strategy = None
    parallel = raw.get("parallel")
    if isinstance(parallel, dict) and "matrix" in parallel:
        matrix = parallel["matrix"]
        if isinstance(matrix, list) and matrix:
            merged: dict[str, list] = {}
            for entry in matrix:
                for k, v in entry.items():
                    if k not in merged:
                        merged[k] = []
                    if isinstance(v, list):
                        merged[k].extend(v)
                    else:
                        merged[k].append(v)
            strategy = Strategy(matrix=merged)

    return Job(
        id=job_id,
        name=raw.get("name"),
        runs_on=None,
        container=image,
        steps=steps,
        needs=needs,
        env=variables,
        strategy=strategy,
        if_=if_,
        services=services,
        timeout_minutes=timeout_minutes,
        concurrency=stage,  # Temporarily stores the stage for needs inference
    )


def _resolve_extends(raw: dict[str, Any], templates: dict[str, dict]) -> dict[str, Any]:
    """Resolve `extends:` by merging template(s) into the job definition."""
    extends = raw.get("extends")
    if not extends:
        return raw

    if isinstance(extends, str):
        extends = [extends]

    merged: dict[str, Any] = {}
    for tmpl_name in extends:
        tmpl = templates.get(tmpl_name, {})
        # Recursively resolve nested extends
        tmpl = _resolve_extends(tmpl, templates)
        merged = _deep_merge(merged, tmpl)

    # Job's own keys override templates
    merged = _deep_merge(merged, {k: v for k, v in raw.items() if k != "extends"})
    return merged


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. Override wins for scalar values; lists are replaced."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _resolve_includes(raw: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Resolve `include:` directives (local files only)."""
    includes = raw.pop("include", None)
    if not includes:
        return raw

    if isinstance(includes, str):
        includes = [{"local": includes}]
    elif isinstance(includes, list):
        includes = [
            ({"local": i} if isinstance(i, str) else i) for i in includes
        ]

    for inc in includes:
        if isinstance(inc, dict) and "local" in inc:
            local_path = base_dir / inc["local"].lstrip("/")
            if local_path.is_file():
                with open(local_path) as f:
                    included = yaml.safe_load(f) or {}
                # Merge included content (included keys don't override main)
                for k, v in included.items():
                    if k not in raw:
                        raw[k] = v
                    elif isinstance(raw[k], dict) and isinstance(v, dict):
                        raw[k] = _deep_merge(v, raw[k])

    return raw


def _parse_services(services_raw: list | None) -> dict[str, ServiceDef]:
    """Parse GitLab services list into ServiceDef dict."""
    if not services_raw:
        return {}
    services = {}
    for i, svc in enumerate(services_raw):
        if isinstance(svc, str):
            name = svc.split(":")[0].split("/")[-1]
            services[name] = ServiceDef(image=svc)
        elif isinstance(svc, dict):
            name = svc.get("name", svc.get("alias", f"svc-{i}"))
            # GitLab uses 'name' for image too
            image = svc.get("name", svc.get("image", ""))
            alias = svc.get("alias", name.split(":")[0].split("/")[-1])
            services[alias] = ServiceDef(
                image=image,
                env={k: str(v) for k, v in svc.get("variables", {}).items()},
                ports=[],
            )
    return services


def _rules_to_condition(
    rules: list[dict] | None,
    only: list | dict | None,
    except_: list | dict | None,
) -> str | None:
    """Convert GitLab rules/only/except to a simplified if: condition string."""
    if rules:
        # Extract simple branch/ref conditions from the first rule
        for rule in rules:
            if isinstance(rule, dict):
                if_val = rule.get("if")
                if if_val:
                    # GitLab uses $CI_COMMIT_BRANCH == 'main' style
                    # Translate common patterns to GitHub expression form
                    return _translate_gitlab_condition(if_val)
        return None

    if only:
        branches = only if isinstance(only, list) else only.get("refs", [])
        if branches:
            conditions = [f"github.ref_name == '{b}'" for b in branches]
            return " || ".join(conditions)

    return None


def _translate_gitlab_condition(condition: str) -> str:
    """Best-effort translation of GitLab CI rule conditions to our expression format."""
    # $CI_COMMIT_BRANCH == "main" -> github.ref_name == 'main'
    result = condition
    result = result.replace("$CI_COMMIT_BRANCH", "github.ref_name")
    result = result.replace("$CI_COMMIT_REF_NAME", "github.ref_name")
    result = result.replace("$CI_COMMIT_TAG", "github.ref_name")
    result = result.replace("$CI_PIPELINE_SOURCE", "github.event_name")
    result = result.replace('"', "'")
    return result


def _parse_timeout(timeout: str | int | None) -> float | None:
    """Parse GitLab timeout string (e.g. '30 minutes', '1h 30m') to minutes."""
    if timeout is None:
        return None
    if isinstance(timeout, (int, float)):
        return float(timeout)

    timeout = str(timeout).strip().lower()
    minutes = 0.0

    # Handle "Xh Ym" or "X hours Y minutes" patterns
    import re
    hours = re.search(r"(\d+)\s*h", timeout)
    mins = re.search(r"(\d+)\s*m", timeout)
    secs = re.search(r"(\d+)\s*s", timeout)

    if hours:
        minutes += int(hours.group(1)) * 60
    if mins:
        minutes += int(mins.group(1))
    if secs:
        minutes += int(secs.group(1)) / 60

    # Plain number with "minutes" suffix
    if not hours and not mins and not secs:
        plain = re.match(r"(\d+)", timeout)
        if plain:
            minutes = float(plain.group(1))

    return minutes if minutes > 0 else None


def _infer_needs_from_stages(jobs: dict[str, Job], stages: list[str]):
    """For jobs without explicit needs, infer dependencies from stage ordering.

    Jobs in stage N depend on all jobs in stage N-1 (unless they have explicit needs).
    """
    # We need to recover stage info. We stored it in the job's concurrency field
    # as a temporary transport mechanism during parsing.
    stage_jobs: dict[str, list[str]] = {s: [] for s in stages}

    for job_id, job in jobs.items():
        stage = job.concurrency or "test"
        if stage in stage_jobs:
            stage_jobs[stage].append(job_id)
        else:
            stage_jobs.setdefault(stage, []).append(job_id)

    # For each job without explicit needs, add all jobs from the previous stage
    for i, stage in enumerate(stages):
        if i == 0:
            continue
        prev_stage = stages[i - 1]
        prev_jobs = stage_jobs.get(prev_stage, [])
        if not prev_jobs:
            continue
        for job_id in stage_jobs.get(stage, []):
            job = jobs[job_id]
            if not job.needs:
                job.needs = list(prev_jobs)
