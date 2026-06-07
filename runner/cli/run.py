"""CLI command to execute GitHub Actions workflows locally."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.utils import discover, find_workflow, parse_kv

EVENT_ALIASES = {
    "pr": "pull_request",
    "pull": "pull_request",
    "pull_request": "pull_request",
    "push": "push",
    "dispatch": "workflow_dispatch",
    "schedule": "schedule",
    "release": "release",
}


@dataclass
class RunArguments:
    """Arguments for the `run` command."""

    workflow: str = ""
    """Workflow name or filename (e.g. 'test' or 'test.yml')."""

    event: str = ""
    """Run all workflows triggered by this event (e.g. 'push', 'pull_request', 'pr')."""

    job: str = ""
    """Run only this job (by id or name)."""

    step: str = ""
    """Filter steps by name substring."""

    root: str = ""
    """Project root (defaults to cwd)."""

    dry_run: bool = False
    """Print commands without executing."""

    verbose: bool = False
    """Show command output in real-time."""

    no_container: bool = False
    """Skip container matching and always run locally."""

    matrix: str = ""
    """Select a matrix combination as key=value pairs (comma-separated). Only matching combinations run."""

    env: str = ""
    """Extra environment variables as key=value pairs (comma-separated)."""

    skip: str = ""
    """Steps to skip (pipe-separated patterns matching name, id, or uses)."""

    skip_jobs: str = ""
    """Jobs to skip (pipe-separated patterns matching job id or name). Example: --skip_jobs "lint|deploy"."""


def _workflows_for_event(event: str, workflows: dict) -> list:
    """Filter workflows that trigger on the given event."""
    resolved = EVENT_ALIASES.get(event.lower(), event.lower())
    matched = []
    for wf in workflows.values():
        triggers = wf.on
        if isinstance(triggers, dict):
            if resolved in triggers:
                matched.append(wf)
        elif isinstance(triggers, list):
            if resolved in triggers:
                matched.append(wf)
        elif isinstance(triggers, str):
            if resolved == triggers:
                matched.append(wf)
    return matched


class Run(Command):
    """Execute GitHub Actions workflows locally (like make for CI)."""

    name: str = "run"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, Run)
        add_arguments(parser, RunArguments)

    @staticmethod
    def execute(args):
        from runner.workflow.executor import execute_workflow
        from runner.workflow.parser import parse_workflow

        root = Path(args.root) if args.root else Path.cwd()

        if args.workflow and (root / args.workflow).exists():
            wf = parse_workflow(root / args.workflow)
            workflows = {wf.name: wf}
        else:
            workflows = discover(root)

        if not workflows:
            return 1

        matrix = parse_kv(args.matrix)
        env = parse_kv(args.env)
        skip_steps = [s.strip() for s in args.skip.split("|") if s.strip()] if args.skip else []
        skip_jobs = [s.strip() for s in args.skip_jobs.split("|") if s.strip()] if args.skip_jobs else []

        # --event mode: run all workflows triggered by a given event
        if args.event:
            targets = _workflows_for_event(args.event, workflows)
            if not targets:
                resolved = EVENT_ALIASES.get(args.event.lower(), args.event.lower())
                print(f"No workflows trigger on '{resolved}'.")
                print("Available events:")
                events = set()
                for wf in workflows.values():
                    if isinstance(wf.on, dict):
                        events.update(wf.on.keys())
                    elif isinstance(wf.on, list):
                        events.update(wf.on)
                    elif isinstance(wf.on, str):
                        events.add(wf.on)
                for e in sorted(events):
                    print(f"  • {e}")
                return 1

            resolved = EVENT_ALIASES.get(args.event.lower(), args.event.lower())
            print(f"Event: {resolved} → {len(targets)} workflow(s)\n")

            all_success = True
            for wf in targets:
                print(f"{'━' * 60}")
                print(f"Workflow: {wf.name}")
                print(f"{'━' * 60}")

                success = execute_workflow(
                    wf,
                    job_filter=args.job or None,
                    step_filter=args.step or None,
                    root=root,
                    dry_run=args.dry_run,
                    verbose=args.verbose,
                    ignore_runs_on=args.no_container,
                    env=env,
                    matrix=matrix,
                    skip_steps=skip_steps,
                    skip_jobs=skip_jobs,
                )
                if not success:
                    all_success = False

            return 0 if all_success else 1

        # --workflow mode
        if not args.workflow:
            print("Usage: runner run --workflow <name>")
            print("       runner run --event <event>")
            print("Run `runner list` to see available workflows.")
            return 1

        wf = find_workflow(args.workflow, workflows)
        if wf is None:
            available = ", ".join(workflows.keys())
            print(f"Workflow '{args.workflow}' not found. Available: {available}")
            return 1

        success = execute_workflow(
            wf,
            job_filter=args.job or None,
            step_filter=args.step or None,
            root=root,
            dry_run=args.dry_run,
            verbose=args.verbose,
            ignore_runs_on=args.no_container,
            env=env,
            matrix=matrix,
            skip_steps=skip_steps,
            skip_jobs=skip_jobs,
        )

        return 0 if success else 1


COMMANDS = [Run]
