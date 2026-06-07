"""Parse GitHub Actions workflow YAML files into structured objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Step:
    """A single step in a job."""

    name: str | None = None
    run: str | None = None
    uses: str | None = None
    with_: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    working_directory: str | None = None
    id: str | None = None
    if_: str | None = None
    shell: str | None = None
    continue_on_error: bool = False
    timeout_minutes: float | None = None

    @property
    def is_run(self) -> bool:
        return self.run is not None

    @property
    def is_action(self) -> bool:
        return self.uses is not None

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.uses:
            return f"uses: {self.uses}"
        if self.run:
            first_line = self.run.strip().split("\n")[0]
            return first_line[:60]
        return "(unnamed step)"


@dataclass
class Strategy:
    """Job strategy (matrix, fail-fast, etc.)."""

    matrix: dict[str, list[Any]] = field(default_factory=dict)
    fail_fast: bool = True
    max_parallel: int | None = None

    def expand(self) -> list[dict[str, Any]]:
        """Expand matrix into all combinations."""
        if not self.matrix:
            return [{}]

        include = self.matrix.pop("include", [])
        exclude = self.matrix.pop("exclude", [])

        keys = list(self.matrix.keys())
        values = list(self.matrix.values())

        combinations = [{}]
        for key, vals in zip(keys, values):
            combinations = [{**c, key: v} for c in combinations for v in vals]

        if exclude:
            combinations = [c for c in combinations if c not in exclude]

        combinations.extend(include)
        return combinations


@dataclass
class ServiceDef:
    """A service container (sidecar) for a job."""

    image: str
    env: dict[str, str] = field(default_factory=dict)
    ports: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    options: str = ""
    credentials: dict[str, str] = field(default_factory=dict)


@dataclass
class Defaults:
    """Workflow or job-level defaults."""

    run_shell: str | None = None
    run_working_directory: str | None = None


@dataclass
class Job:
    """A job in a workflow."""

    id: str
    name: str | None = None
    runs_on: str | None = None
    steps: list[Step] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    strategy: Strategy | None = None
    if_: str | None = None
    services: dict[str, ServiceDef] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    container: str | None = None
    defaults: Defaults | None = None
    concurrency: str | None = None
    timeout_minutes: float | None = None
    uses: str | None = None
    with_: dict[str, Any] = field(default_factory=dict)
    secrets_: str | dict[str, str] | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.id


@dataclass
class Workflow:
    """A parsed GitHub Actions workflow."""

    name: str
    path: Path
    on: dict[str, Any] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    defaults: Defaults | None = None
    concurrency: str | None = None

    def job_order(self) -> list[str]:
        """Topological sort of jobs based on `needs`."""
        visited = set()
        order = []

        def visit(job_id: str):
            if job_id in visited:
                return
            visited.add(job_id)
            job = self.jobs.get(job_id)
            if job:
                for dep in job.needs:
                    visit(dep)
            order.append(job_id)

        for job_id in self.jobs:
            visit(job_id)

        return order


def _parse_step(raw: dict[str, Any]) -> Step:
    timeout = raw.get("timeout-minutes")
    return Step(
        name=raw.get("name"),
        run=raw.get("run"),
        uses=raw.get("uses"),
        with_=raw.get("with", {}),
        env={k: str(v) for k, v in raw.get("env", {}).items()},
        working_directory=raw.get("working-directory"),
        id=raw.get("id"),
        if_=raw.get("if"),
        shell=raw.get("shell"),
        continue_on_error=bool(raw.get("continue-on-error", False)),
        timeout_minutes=float(timeout) if timeout is not None else None,
    )


def _parse_strategy(raw: dict[str, Any] | None) -> Strategy | None:
    if raw is None:
        return None
    matrix = raw.get("matrix", {})
    if isinstance(matrix, dict):
        matrix = {k: (v if isinstance(v, list) else [v]) for k, v in matrix.items()}
    return Strategy(
        matrix=matrix,
        fail_fast=raw.get("fail-fast", True),
        max_parallel=raw.get("max-parallel"),
    )


def _parse_services(raw: dict[str, Any] | None) -> dict[str, ServiceDef]:
    if not raw:
        return {}
    services = {}
    for name, svc in raw.items():
        if isinstance(svc, str):
            services[name] = ServiceDef(image=svc)
        else:
            services[name] = ServiceDef(
                image=svc.get("image", ""),
                env={k: str(v) for k, v in svc.get("env", {}).items()},
                ports=[str(p) for p in svc.get("ports", [])],
                volumes=svc.get("volumes", []),
                options=svc.get("options", ""),
                credentials=svc.get("credentials", {}),
            )
    return services


def _parse_defaults(raw: dict[str, Any] | None) -> Defaults | None:
    if not raw:
        return None
    run = raw.get("run", {})
    return Defaults(
        run_shell=run.get("shell"),
        run_working_directory=run.get("working-directory"),
    )


def _parse_job(job_id: str, raw: dict[str, Any]) -> Job:
    needs = raw.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]

    container = raw.get("container")
    if isinstance(container, dict):
        container = container.get("image")

    concurrency = raw.get("concurrency")
    if isinstance(concurrency, dict):
        concurrency = concurrency.get("group")

    timeout = raw.get("timeout-minutes")

    secrets_raw = raw.get("secrets")
    if isinstance(secrets_raw, dict):
        secrets_val = {k: str(v) for k, v in secrets_raw.items()}
    elif isinstance(secrets_raw, str):
        secrets_val = secrets_raw
    else:
        secrets_val = None

    return Job(
        id=job_id,
        name=raw.get("name"),
        runs_on=raw.get("runs-on") if not isinstance(raw.get("runs-on"), list) else raw["runs-on"][0],
        steps=[_parse_step(s) for s in raw.get("steps", [])],
        needs=needs,
        env={k: str(v) for k, v in raw.get("env", {}).items()},
        strategy=_parse_strategy(raw.get("strategy")),
        if_=raw.get("if"),
        services=_parse_services(raw.get("services")),
        outputs={k: str(v) for k, v in raw.get("outputs", {}).items()},
        container=container,
        defaults=_parse_defaults(raw.get("defaults")),
        concurrency=concurrency,
        timeout_minutes=float(timeout) if timeout is not None else None,
        uses=raw.get("uses"),
        with_=raw.get("with", {}),
        secrets_=secrets_val,
    )


def parse_workflow(path: Path | str) -> Workflow:
    """Parse a workflow YAML file into a Workflow object."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty workflow file: {path}")

    concurrency = raw.get("concurrency")
    if isinstance(concurrency, dict):
        concurrency = concurrency.get("group")

    jobs = {}
    for job_id, job_raw in raw.get("jobs", {}).items():
        jobs[job_id] = _parse_job(job_id, job_raw)

    return Workflow(
        name=raw.get("name", path.stem),
        path=path,
        on=raw.get("on", raw.get(True, {})),
        jobs=jobs,
        env={k: str(v) for k, v in raw.get("env", {}).items()},
        defaults=_parse_defaults(raw.get("defaults")),
        concurrency=concurrency,
    )


def discover_workflows(root: Path | str | None = None) -> dict[str, Workflow]:
    """Find and parse all workflow files under .github/workflows/."""
    if root is None:
        root = Path.cwd()
    root = Path(root)

    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return {}

    workflows = {}
    for f in sorted(workflows_dir.iterdir()):
        if f.suffix in (".yml", ".yaml"):
            try:
                wf = parse_workflow(f)
                workflows[wf.name] = wf
            except Exception as e:
                print(f"Warning: skipping {f.name}: {e}")

    return workflows
