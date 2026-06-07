"""runner github list — list GitHub Actions workflows."""

from __future__ import annotations

from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.args import ListArguments


class List(Command):
    """List available GitHub Actions workflows."""

    name: str = "list"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, List)
        add_arguments(parser, ListArguments)

    @staticmethod
    def execute(args):
        from runner.cli.display import print_workflows
        from runner.github import discover_workflows

        root = Path(args.root) if args.root else Path.cwd()
        workflows = discover_workflows(root)

        if not workflows:
            print("No GitHub Actions workflows found.")
            print(f"Looked in: {root / '.github' / 'workflows'}")
            return 1

        print("Available workflows (github):\n")
        print_workflows(workflows)
        return 0


COMMANDS = [List]
