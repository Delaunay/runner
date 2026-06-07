"""CLI command to execute CI workflows locally (auto-detecting backend).

The top-level `runner run` detects the CI system and delegates to:
- runner.cli.github.run.Run.execute()
- runner.cli.forgejo.run.Run.execute()
- runner.cli.gitlab.run.Run.execute()
"""

from __future__ import annotations

from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.args import RunArguments


class Run(Command):
    """Execute CI workflows locally (auto-detects backend)."""

    name: str = "run"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, Run)
        add_arguments(parser, RunArguments)

    @staticmethod
    def execute(args):
        """Auto-detect the backend and delegate to the appropriate runner."""
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
            from runner.cli.gitlab.run import Run as GitlabRun
            return GitlabRun.execute(args)
        elif backend.name == "forgejo":
            from runner.cli.forgejo.run import Run as ForgejoRun
            return ForgejoRun.execute(args)
        else:
            from runner.cli.github.run import Run as GithubRun
            return GithubRun.execute(args)


COMMANDS = [Run]
