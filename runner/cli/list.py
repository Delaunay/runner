"""CLI command to list available CI workflows (auto-detecting backend).

The top-level `runner list` detects the CI system and delegates to:
- runner.cli.github.list.List.execute()
- runner.cli.forgejo.list.List.execute()
- runner.cli.gitlab.list.List.execute()
"""

from __future__ import annotations

from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.args import ListArguments


class List(Command):
    """List available CI workflows (auto-detects backend)."""

    name: str = "list"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, List)
        add_arguments(parser, ListArguments)

    @staticmethod
    def execute(args):
        """Auto-detect the backend and delegate to the appropriate lister."""
        from runner.workflow.backends import detect_backend, get_backend_by_name

        root = Path(args.root) if args.root else Path.cwd()

        if args.backend and args.backend != "auto":
            backend = get_backend_by_name(args.backend)
            if backend is None:
                print(f"Unknown backend: {args.backend}")
                print("Available: github, forgejo, gitlab, auto")
                return 1
        else:
            backend = detect_backend(root)

        if backend.name == "gitlab":
            from runner.cli.gitlab.list import List as GitlabList
            return GitlabList.execute(args)
        elif backend.name == "forgejo":
            from runner.cli.forgejo.list import List as ForgejoList
            return ForgejoList.execute(args)
        else:
            from runner.cli.github.list import List as GithubList
            return GithubList.execute(args)


COMMANDS = [List]
