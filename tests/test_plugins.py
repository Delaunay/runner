"""Plugin discovery tests."""

from runner.cli import discover_commands


def test_discover_commands():
    commands = discover_commands()
    assert len(commands) >= 1
