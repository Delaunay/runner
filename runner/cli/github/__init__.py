"""runner github — parent command for GitHub Actions execution."""

from argklass.command import ParentCommand


class Github(ParentCommand):
    """GitHub Actions workflow runner."""

    name: str = "github"

    @classmethod
    def help(cls):
        return "Run GitHub Actions workflows"

    @staticmethod
    def module():
        import runner.cli.github
        return runner.cli.github


COMMANDS = Github
