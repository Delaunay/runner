"""runner forgejo — parent command for native Forgejo/Codeberg Actions execution."""

from argklass.command import ParentCommand


class Forgejo(ParentCommand):
    """Native Forgejo/Codeberg Actions runner."""

    name: str = "forgejo"

    @classmethod
    def help(cls):
        return "Run Forgejo/Codeberg Actions workflows natively"

    @staticmethod
    def module():
        import runner.cli.forgejo
        return runner.cli.forgejo


COMMANDS = Forgejo
