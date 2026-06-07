"""runner github run — execute a GitHub Actions workflow."""

from __future__ import annotations

from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.args import RunArguments, warn_unsupported

EVENT_ALIASES = {
    "pr": "pull_request",
    "pull": "pull_request",
    "pull_request": "pull_request",
    "push": "push",
    "dispatch": "workflow_dispatch",
    "schedule": "schedule",
    "release": "release",
}


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
    """Execute a GitHub Actions workflow locally."""

    name: str = "run"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, Run)
        add_arguments(parser, RunArguments)

    @staticmethod
    def execute(args):
        from runner.cli.utils import find_workflow, parse_kv
        from runner.github import discover_workflows, execute_workflow, parse_workflow

        warn_unsupported(args, "github")

        root = Path(args.root) if args.root else Path.cwd()

        if args.workflow and (root / args.workflow).exists():
            wf = parse_workflow(root / args.workflow)
            workflows = {wf.name: wf}
        else:
            workflows = discover_workflows(root)

        if not workflows:
            print("No GitHub Actions workflows found.")
            print(f"Looked in: {root / '.github' / 'workflows'}")
            return 1

        matrix = parse_kv(args.matrix)
        env = parse_kv(args.env)
        inputs = parse_kv(args.inputs)
        skip_steps = _parse_pipe(args.skip)
        skip_jobs = _parse_pipe(args.skip_jobs)

        # --event mode
        if args.event:
            targets = _workflows_for_event(args.event, workflows)
            if not targets:
                resolved = EVENT_ALIASES.get(args.event.lower(), args.event.lower())
                print(f"No workflows trigger on '{resolved}'.")
                return 1

            resolved = EVENT_ALIASES.get(args.event.lower(), args.event.lower())
            print(f"Event: {resolved} → {len(targets)} workflow(s)\n")

            all_success = True
            for wf in targets:
                print(f"{'━' * 60}")
                print(f"Workflow: {wf.name}")
                print(f"{'━' * 60}")
                success = execute_workflow(
                    wf, job_filter=args.job or None, step_filter=args.step or None,
                    root=root, dry_run=args.dry_run, verbose=args.verbose,
                    ignore_runs_on=args.no_container, env=env, matrix=matrix,
                    skip_steps=skip_steps, skip_jobs=skip_jobs, inputs=inputs,
                )
                if not success:
                    all_success = False
            return 0 if all_success else 1

        # Single workflow mode
        if not args.workflow:
            if len(workflows) == 1:
                wf = next(iter(workflows.values()))
            else:
                print("Multiple workflows found. Specify one with --workflow.")
                print("Run `runner github list` to see available workflows.")
                return 1
        else:
            wf = find_workflow(args.workflow, workflows)
            if wf is None:
                available = ", ".join(workflows.keys())
                print(f"Workflow '{args.workflow}' not found. Available: {available}")
                return 1

        success = execute_workflow(
            wf, job_filter=args.job or None, step_filter=args.step or None,
            root=root, dry_run=args.dry_run, verbose=args.verbose,
            ignore_runs_on=args.no_container, env=env, matrix=matrix,
            skip_steps=skip_steps, skip_jobs=skip_jobs, inputs=inputs,
        )
        return 0 if success else 1


def _parse_pipe(text: str) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in text.split("|") if s.strip()]


COMMANDS = [Run]
