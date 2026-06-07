"""runner workspace clean — remove workspace data."""

import shutil
from dataclasses import dataclass
from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser

from runner.cli.workspace.status import _dir_size, _format_size


@dataclass
class CleanArgs:
    root: str = ""
    """Project root (defaults to cwd)."""

    target: str = "all"
    """What to clean: all, cache, artifacts, jobs."""


class Clean(Command):
    """Remove workspace data (cache, artifacts, jobs, or everything)."""

    name: str = "clean"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, Clean)
        add_arguments(parser, CleanArgs)

    @staticmethod
    def execute(args):
        root = Path(args.root) if args.root else Path.cwd()
        ws_dir = root / ".workspace"

        if not ws_dir.exists():
            print("Nothing to clean.")
            return 0

        target = args.target

        if target == "all":
            size = _dir_size(ws_dir)
            shutil.rmtree(ws_dir)
            print(f"Removed .workspace/ ({_format_size(size)} freed)")
        elif target in ("cache", "artifacts", "jobs"):
            section = ws_dir / target
            if not section.exists():
                print(f"No {target}/ directory to clean.")
                return 0
            size = _dir_size(section)
            shutil.rmtree(section)
            section.mkdir(parents=True, exist_ok=True)
            print(f"Cleaned {target}/ ({_format_size(size)} freed)")
        else:
            # Try as a specific subdirectory under a section
            # e.g., "cache/deps-abc123"
            target_path = ws_dir / target
            if target_path.exists() and target_path.is_dir():
                size = _dir_size(target_path)
                shutil.rmtree(target_path)
                print(f"Removed {target} ({_format_size(size)} freed)")
            else:
                print(f"Unknown target: {target}")
                print("Available: all, cache, artifacts, jobs, or a specific subpath")
                return 1

        return 0


COMMANDS = Clean
