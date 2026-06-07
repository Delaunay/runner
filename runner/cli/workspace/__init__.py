"""runner workspace — parent command for workspace management."""

from argklass.command import ParentCommand


class Workspace(ParentCommand):
    """Manage the .workspace directory (cache, artifacts, jobs)."""

    name: str = "workspace"

    @classmethod
    def help(cls):
        return "Workspace management subcommands"

    @staticmethod
    def module():
        import runner.cli.workspace
        return runner.cli.workspace


COMMANDS = Workspace
