"""runner forgejo run — execute a Forgejo/Codeberg Actions workflow."""

from __future__ import annotations

from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.args import RunArguments, warn_unsupported


class Run(Command):
    """Execute a Forgejo/Codeberg Actions workflow locally."""

    name: str = "run"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, Run)
        add_arguments(parser, RunArguments)

    @staticmethod
    def execute(args):
        from runner.cli.utils import find_workflow, parse_kv
        from runner.forgejo import discover_workflows, execute_workflow, parse_workflow

        warn_unsupported(args, "forgejo")

        root = Path(args.root) if args.root else Path.cwd()

        if args.workflow and (root / args.workflow).exists():
            wf = parse_workflow(root / args.workflow)
            workflows = {wf.name: wf}
        else:
            workflows = discover_workflows(root)

        if not workflows:
            print("No Forgejo workflows found.")
            print("Looked in: .forgejo/workflows/ and .github/workflows/")
            return 1

        matrix = parse_kv(args.matrix)
        env = parse_kv(args.env)
        inputs = parse_kv(args.inputs)
        skip_steps = _parse_pipe(args.skip)
        skip_jobs = _parse_pipe(args.skip_jobs)

        # --event mode
        if args.event:
            event = args.event.lower()
            matched = [
                wf for wf in workflows.values()
                if event in (wf.on or {})
            ]
            if not matched:
                print(f"No workflows triggered by event: {event}")
                return 1

            all_ok = True
            for wf in matched:
                print(f"\n{'═' * 60}")
                print(f"Workflow: {wf.name} (triggered by: {event})")
                print(f"{'═' * 60}")
                ok = execute_workflow(
                    wf, root=root, dry_run=args.dry_run, verbose=args.verbose,
                    ignore_runs_on=args.no_container, env=env, matrix=matrix,
                    skip_steps=skip_steps, skip_jobs=skip_jobs, inputs=inputs,
                )
                if not ok:
                    all_ok = False
            return 0 if all_ok else 1

        # Single workflow mode
        if not args.workflow:
            if len(workflows) == 1:
                wf = next(iter(workflows.values()))
            else:
                print("Multiple workflows found. Specify one with --workflow.")
                print("Run `runner forgejo list` to see available workflows.")
                return 1
        else:
            wf = find_workflow(args.workflow, workflows)
            if wf is None:
                available = ", ".join(workflows.keys())
                print(f"Workflow '{args.workflow}' not found. Available: {available}")
                return 1

        ok = execute_workflow(
            wf, job_filter=args.job or None, root=root,
            dry_run=args.dry_run, verbose=args.verbose,
            ignore_runs_on=args.no_container, env=env, matrix=matrix,
            skip_steps=skip_steps, skip_jobs=skip_jobs, inputs=inputs,
        )
        return 0 if ok else 1


def _parse_pipe(text: str) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in text.split("|") if s.strip()]


COMMANDS = [Run]
