"""runner gitlab list — list jobs in a GitLab CI/CD pipeline."""

from __future__ import annotations

from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.args import ListArguments


class List(Command):
    """List jobs and stages in a GitLab CI/CD pipeline."""

    name: str = "list"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, List)
        add_arguments(parser, ListArguments)

    @staticmethod
    def execute(args):
        from runner.cli.display import print_gitlab_pipeline
        from runner.gitlab import parse_pipeline

        root = Path(args.root) if args.root else Path.cwd()
        ci_file = root / ".gitlab-ci.yml"

        if not ci_file.is_file():
            print("No .gitlab-ci.yml found.")
            return 1

        pipeline = parse_pipeline(ci_file)

        print("Available pipeline (gitlab):\n")
        print_gitlab_pipeline(pipeline)
        return 0


COMMANDS = [List]
