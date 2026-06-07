"""runner workspace status — display space breakdown."""

from dataclasses import dataclass
from pathlib import Path

from argklass.arguments import add_arguments
from argklass.command import Command, newparser


def _dir_size(path: Path) -> int:
    """Compute total size in bytes of a directory tree."""
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def _format_size(size_bytes: int) -> str:
    """Format bytes into a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for f in path.rglob("*") if f.is_file())


def _bar(part: int, whole: int, width: int = 20) -> str:
    """Render a simple ASCII proportion bar."""
    if whole == 0:
        return ""
    ratio = part / whole
    filled = int(ratio * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {ratio * 100:.0f}%"


@dataclass
class StatusArgs:
    root: str = ""
    """Project root (defaults to cwd)."""


class Status(Command):
    """Show workspace size breakdown."""

    name: str = "status"

    @staticmethod
    def arguments(subparsers):
        parser = newparser(subparsers, Status)
        add_arguments(parser, StatusArgs)

    @staticmethod
    def execute(args):
        root = Path(args.root) if args.root else Path.cwd()
        ws_dir = root / ".workspace"

        if not ws_dir.exists():
            print("No .workspace directory found.")
            return 0

        total = _dir_size(ws_dir)

        sections = {
            "jobs": ws_dir / "jobs",
            "artifacts": ws_dir / "artifacts",
            "cache": ws_dir / "cache",
        }

        print("Workspace: .workspace/")
        print(f"  Total size: {_format_size(total)}")
        print()

        accounted = 0
        for name, path in sections.items():
            size = _dir_size(path)
            accounted += size
            files = _count_files(path)
            bar = _bar(size, total) if total > 0 else ""
            print(f"  {name + '/':<14} {_format_size(size):>10}  {files:>5} files  {bar}")

            if path.exists():
                subdirs = sorted(
                    (d for d in path.iterdir() if d.is_dir()),
                    key=lambda d: _dir_size(d),
                    reverse=True,
                )
                for sub in subdirs[:10]:
                    sub_size = _dir_size(sub)
                    sub_files = _count_files(sub)
                    print(f"    {sub.name:<20} {_format_size(sub_size):>10}  {sub_files:>5} files")
                if len(subdirs) > 10:
                    print(f"    ... and {len(subdirs) - 10} more")

        other = total - accounted
        if other > 0:
            print(f"  {'other':<14} {_format_size(other):>10}")

        print()
        return 0


COMMANDS = Status
