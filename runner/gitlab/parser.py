"""GitLab CI/CD pipeline parser — native data model.

Parses .gitlab-ci.yml into GitLab-native dataclasses that preserve the
original semantics rather than translating to GitHub Actions concepts.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class GitLabService:
    """A service container definition."""

    name: str
    alias: str = ""
    entrypoint: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)

    @property
    def image(self) -> str:
        return self.name

    @property
    def hostname(self) -> str:
        """The hostname the service is reachable at."""
        if self.alias:
            return self.alias
        # Default: image name with / and : replaced by -
        return re.sub(r"[/:]", "-", self.name.split(":")[0])


@dataclass
class GitLabArtifacts:
    """Artifacts configuration for a job."""

    paths: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    expire_in: str = ""
    name: str = ""
    when: str = "on_success"  # on_success | on_failure | always
    reports: dict[str, Any] = field(default_factory=dict)


@dataclass
class GitLabCache:
    """Cache configuration for a job."""

    key: str = ""
    paths: list[str] = field(default_factory=list)
    policy: str = "pull-push"  # pull | push | pull-push
    when: str = "on_success"


@dataclass
class GitLabRule:
    """A single rule entry."""

    if_: str = ""
    when: str = "on_success"  # on_success | manual | always | never | delayed
    allow_failure: bool = False
    changes: list[str] = field(default_factory=list)
    exists: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class GitLabJob:
    """A GitLab CI job with full native semantics."""

    name: str
    stage: str = "test"
    image: str = ""
    before_script: list[str] = field(default_factory=list)
    script: list[str] = field(default_factory=list)
    after_script: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    services: list[GitLabService] = field(default_factory=list)
    artifacts: GitLabArtifacts | None = None
    cache: GitLabCache | None = None
    needs: list[str] = field(default_factory=list)
    needs_explicit: bool = False
    rules: list[GitLabRule] = field(default_factory=list)
    only: list[str] | dict | None = None
    except_: list[str] | dict | None = None
    allow_failure: bool = False
    timeout: str = ""
    retry: int = 0
    parallel: int | dict | None = None
    environment: str | dict | None = None
    when: str = "on_success"  # on_success | manual | always | delayed
    dependencies: list[str] | None = None
    resource_group: str = ""
    interruptible: bool = False
    tags: list[str] = field(default_factory=list)
    coverage: str = ""

    @property
    def timeout_seconds(self) -> float | None:
        """Parse timeout string to seconds."""
        if not self.timeout:
            return None
        return _parse_duration(self.timeout)


@dataclass
class GitLabPipeline:
    """A complete GitLab CI/CD pipeline."""

    path: Path
    stages: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    jobs: dict[str, GitLabJob] = field(default_factory=dict)
    default_image: str = ""
    default_before_script: list[str] = field(default_factory=list)
    default_after_script: list[str] = field(default_factory=list)
    default_services: list[GitLabService] = field(default_factory=list)
    default_cache: GitLabCache | None = None
    default_tags: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    def jobs_for_stage(self, stage: str) -> list[GitLabJob]:
        """Get all jobs in a given stage, preserving definition order."""
        return [j for j in self.jobs.values() if j.stage == stage]

    def active_stages(self) -> list[str]:
        """Return only stages that have at least one job."""
        used = {j.stage for j in self.jobs.values()}
        return [s for s in self.stages if s in used]


# ──────────────────── Reserved top-level keys ────────────────────

RESERVED_KEYS = frozenset({
    "stages", "variables", "default", "include", "workflow",
    "image", "before_script", "after_script", "services",
    "cache", "pages",
})


def parse_pipeline(path: Path) -> GitLabPipeline:
    """Parse a .gitlab-ci.yml file into a GitLabPipeline."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty CI file: {path}")

    raw = _resolve_includes(raw, path.parent)

    # Global settings
    stages = raw.get("stages", ["build", "test", "deploy"])
    global_vars = _to_str_dict(raw.get("variables", {}))
    defaults = raw.get("default", {})

    default_image = raw.get("image", "") or defaults.get("image", "")
    default_before = raw.get("before_script") or defaults.get("before_script", [])
    default_after = raw.get("after_script") or defaults.get("after_script", [])
    default_services_raw = raw.get("services") or defaults.get("services", [])
    default_cache_raw = defaults.get("cache") or raw.get("cache")
    default_tags = defaults.get("tags", [])

    default_services = _parse_services(default_services_raw)
    default_cache = _parse_cache(default_cache_raw) if default_cache_raw else None

    # Collect templates (keys starting with .)
    templates: dict[str, dict] = {}
    for key, val in raw.items():
        if key.startswith(".") and isinstance(val, dict):
            templates[key] = val

    # Parse jobs
    jobs: dict[str, GitLabJob] = {}
    for key, val in raw.items():
        if key in RESERVED_KEYS or key.startswith(".") or not isinstance(val, dict):
            continue

        job = _parse_job(
            key, val,
            templates=templates,
            global_vars=global_vars,
            default_image=default_image,
            default_before=default_before,
            default_after=default_after,
            default_services=default_services,
            default_cache=default_cache,
            default_tags=default_tags,
        )
        jobs[key] = job

    return GitLabPipeline(
        path=path,
        stages=stages,
        variables=global_vars,
        jobs=jobs,
        default_image=default_image,
        default_before_script=default_before,
        default_after_script=default_after,
        default_services=default_services,
        default_cache=default_cache,
        default_tags=default_tags,
    )


def _parse_job(
    name: str,
    raw: dict[str, Any],
    *,
    templates: dict[str, dict],
    global_vars: dict[str, str],
    default_image: str,
    default_before: list[str],
    default_after: list[str],
    default_services: list[GitLabService],
    default_cache: GitLabCache | None,
    default_tags: list[str],
) -> GitLabJob:
    """Parse a single job definition."""
    raw = _resolve_extends(raw, templates)

    stage = raw.get("stage", "test")
    image = raw.get("image", default_image)
    variables = {**global_vars, **_to_str_dict(raw.get("variables", {}))}

    before_script = raw.get("before_script", default_before)
    script = raw.get("script", [])
    after_script = raw.get("after_script", default_after)

    services = _parse_services(raw.get("services", [])) or default_services
    artifacts = _parse_artifacts(raw.get("artifacts"))
    cache = _parse_cache(raw.get("cache")) or default_cache
    tags = raw.get("tags", default_tags)

    # Needs
    needs_raw = raw.get("needs", [])
    needs = []
    needs_explicit = "needs" in raw
    if isinstance(needs_raw, list):
        for n in needs_raw:
            if isinstance(n, str):
                needs.append(n)
            elif isinstance(n, dict) and "job" in n:
                needs.append(n["job"])

    # Rules
    rules = _parse_rules(raw.get("rules", []))

    # Other fields
    allow_failure = raw.get("allow_failure", False)
    if isinstance(allow_failure, dict):
        allow_failure = True  # allow_failure: { exit_codes: [...] }
    timeout = str(raw.get("timeout", ""))
    retry = int(raw.get("retry", 0)) if isinstance(raw.get("retry"), int) else 0
    parallel = raw.get("parallel")
    environment = raw.get("environment")
    when = raw.get("when", "on_success")
    dependencies = raw.get("dependencies")
    resource_group = raw.get("resource_group", "")
    interruptible = raw.get("interruptible", False)
    coverage = raw.get("coverage", "")

    return GitLabJob(
        name=name,
        stage=stage,
        image=image,
        before_script=[str(s) for s in before_script],
        script=[str(s) for s in script],
        after_script=[str(s) for s in after_script],
        variables=variables,
        services=services,
        artifacts=artifacts,
        cache=cache,
        needs=needs,
        needs_explicit=needs_explicit,
        rules=rules,
        only=raw.get("only"),
        except_=raw.get("except"),
        allow_failure=bool(allow_failure),
        timeout=timeout,
        retry=retry,
        parallel=parallel,
        environment=environment,
        when=when,
        dependencies=dependencies,
        resource_group=resource_group,
        interruptible=interruptible,
        tags=tags,
        coverage=coverage,
    )


def _parse_services(raw: list | None) -> list[GitLabService]:
    """Parse a services list."""
    if not raw:
        return []
    services = []
    for item in raw:
        if isinstance(item, str):
            services.append(GitLabService(name=item))
        elif isinstance(item, dict):
            services.append(GitLabService(
                name=item.get("name", ""),
                alias=item.get("alias", ""),
                entrypoint=item.get("entrypoint", []),
                command=item.get("command", []),
                variables=_to_str_dict(item.get("variables", {})),
            ))
    return services


def _parse_artifacts(raw: dict | None) -> GitLabArtifacts | None:
    """Parse an artifacts definition."""
    if not raw:
        return None
    return GitLabArtifacts(
        paths=raw.get("paths", []),
        exclude=raw.get("exclude", []),
        expire_in=raw.get("expire_in", ""),
        name=raw.get("name", ""),
        when=raw.get("when", "on_success"),
        reports=raw.get("reports", {}),
    )


def _parse_cache(raw: dict | str | None) -> GitLabCache | None:
    """Parse a cache definition."""
    if not raw:
        return None
    if isinstance(raw, str):
        return GitLabCache(key=raw)
    return GitLabCache(
        key=raw.get("key", "") if isinstance(raw.get("key"), str) else str(raw.get("key", "")),
        paths=raw.get("paths", []),
        policy=raw.get("policy", "pull-push"),
        when=raw.get("when", "on_success"),
    )


def _parse_rules(raw: list | None) -> list[GitLabRule]:
    """Parse rules list."""
    if not raw:
        return []
    rules = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rules.append(GitLabRule(
            if_=item.get("if", ""),
            when=item.get("when", "on_success"),
            allow_failure=item.get("allow_failure", False),
            changes=item.get("changes", []),
            exists=item.get("exists", []),
            variables=_to_str_dict(item.get("variables", {})),
        ))
    return rules


# ──────────────────── Template / Include Resolution ────────────────────


def _resolve_extends(raw: dict[str, Any], templates: dict[str, dict]) -> dict[str, Any]:
    """Resolve `extends:` by merging template(s) into the job."""
    extends = raw.get("extends")
    if not extends:
        return raw

    if isinstance(extends, str):
        extends = [extends]

    merged: dict[str, Any] = {}
    for tmpl_name in extends:
        tmpl = templates.get(tmpl_name, {})
        tmpl = _resolve_extends(tmpl, templates)
        merged = _deep_merge(merged, tmpl)

    merged = _deep_merge(merged, {k: v for k, v in raw.items() if k != "extends"})
    return merged


def _resolve_includes(raw: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Resolve `include:` directives (local files only for now)."""
    includes = raw.pop("include", None)
    if not includes:
        return raw

    if isinstance(includes, str):
        includes = [{"local": includes}]
    elif isinstance(includes, list):
        includes = [{"local": i} if isinstance(i, str) else i for i in includes]

    for inc in includes:
        if isinstance(inc, dict) and "local" in inc:
            local_path = base_dir / inc["local"].lstrip("/")
            if local_path.is_file():
                with open(local_path) as f:
                    included = yaml.safe_load(f) or {}
                for k, v in included.items():
                    if k not in raw:
                        raw[k] = v
                    elif isinstance(raw[k], dict) and isinstance(v, dict):
                        raw[k] = _deep_merge(v, raw[k])

    return raw


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. Override wins for scalars; dicts recurse."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _to_str_dict(d: dict | None) -> dict[str, str]:
    """Convert a dict to str keys and str values."""
    if not d:
        return {}
    return {str(k): str(v) for k, v in d.items()}


def _parse_duration(text: str) -> float:
    """Parse a GitLab duration string (e.g. '1h 30m', '45 minutes') to seconds."""
    text = str(text).strip().lower()
    seconds = 0.0

    hours = re.search(r"(\d+)\s*h", text)
    mins = re.search(r"(\d+)\s*m", text)
    secs = re.search(r"(\d+)\s*s", text)

    if hours:
        seconds += int(hours.group(1)) * 3600
    if mins:
        seconds += int(mins.group(1)) * 60
    if secs:
        seconds += int(secs.group(1))

    if not hours and not mins and not secs:
        plain = re.match(r"(\d+)", text)
        if plain:
            seconds = float(plain.group(1)) * 60  # Assume minutes

    return seconds
