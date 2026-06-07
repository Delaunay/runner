"""runner gitlab — parent command for native GitLab CI execution."""

from argklass.command import ParentCommand


class Gitlab(ParentCommand):
    """Native GitLab CI/CD pipeline runner."""

    name: str = "gitlab"

    @classmethod
    def help(cls):
        return "Run GitLab CI/CD pipelines natively"

    @staticmethod
    def module():
        import runner.cli.gitlab
        return runner.cli.gitlab


COMMANDS = Gitlab
