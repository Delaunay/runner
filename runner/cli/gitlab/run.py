"""runner gitlab run — execute a GitLab CI/CD pipeline."""

from __future__ import annotations

from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.args import RunArguments, warn_unsupported


class Run(Command):
    """Execute a GitLab CI/CD pipeline locally."""

    name: str = "run"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, Run)
        add_arguments(parser, RunArguments)

    @staticmethod
    def execute(args):
        from runner.cli.utils import parse_kv
        from runner.gitlab import execute_pipeline, parse_pipeline

        warn_unsupported(args, "gitlab")

        root = Path(args.root) if args.root else Path.cwd()

        # For gitlab, --workflow can point to the ci file
        ci_file = root / ".gitlab-ci.yml"
        if args.workflow:
            candidate = root / args.workflow
            if candidate.exists():
                ci_file = candidate

        if not ci_file.is_file():
            print(f"Not found: {ci_file}")
            return 1

        pipeline = parse_pipeline(ci_file)

        variables = parse_kv(args.env)
        skip_jobs = _parse_pipe(args.skip_jobs) or _parse_pipe(args.skip)
        only_jobs = [j.strip() for j in args.job.split(",") if j.strip()] if args.job else []

        result = execute_pipeline(
            pipeline,
            root=root,
            dry_run=args.dry_run,
            verbose=args.verbose,
            no_container=args.no_container,
            variables=variables,
            skip_jobs=skip_jobs,
            only_jobs=only_jobs,
            only_stage=args.step or "",
        )

        return 0 if result.success else 1


def _parse_pipe(text: str) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in text.split("|") if s.strip()]


COMMANDS = [Run]
