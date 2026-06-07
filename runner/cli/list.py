"""CLI command to list available workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.utils import discover


@dataclass
class ListArguments:
    root: str = ""
    """Project root (defaults to cwd)."""


class List(Command):
    """List available workflows and their jobs/matrix."""

    name: str = "list"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, List)
        add_arguments(parser, ListArguments)

    @staticmethod
    def execute(args):
        root = Path(args.root) if args.root else Path.cwd()
        workflows = discover(root)
        if not workflows:
            return 1

        print("Available workflows:\n")
        for name, wf in workflows.items():
            print(f"  {name}")
            print(f"    file: {wf.path.name}")
            for job_id, job in wf.jobs.items():
                step_count = len(job.steps)
                needs = f" (needs: {', '.join(job.needs)})" if job.needs else ""
                matrix_info = ""
                if job.strategy and job.strategy.matrix:
                    keys = list(job.strategy.matrix.keys())
                    combos = job.strategy.expand()
                    matrix_info = f" [{len(combos)} combinations: {', '.join(keys)}]"
                    for combo in combos:
                        label = ", ".join(f"{k}={v}" for k, v in combo.items())
                        print(f"      - {label}")
                print(f"    • {job.display_name} ({step_count} steps){needs}{matrix_info}")
            print()

        return 0


COMMANDS = [List]
