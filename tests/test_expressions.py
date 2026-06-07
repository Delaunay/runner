"""Tests for the expression evaluator."""

import textwrap

import pytest

from runner.workflow.expressions import (
    ExpressionContext,
    StepContext,
    evaluate_condition,
    evaluate_expression,
)
from runner.workflow.secrets import (
    CommandSecretProvider,
    EnvSecretProvider,
    FileSecretProvider,
    create_default_store,
)


@pytest.fixture
def ctx():
    return ExpressionContext(
        matrix={"python-version": "3.12", "os": "ubuntu-latest"},
        env={"CI": "true", "MY_VAR": "hello"},
        steps={
            "build": StepContext(outputs={"artifact": "dist/foo.whl"}, outcome="success"),
            "test": StepContext(outputs={"coverage": "85"}, outcome="failure"),
        },
        github={"sha": "abc123", "ref": "refs/heads/main", "workspace": "/tmp"},
        secrets={"API_KEY": "secret123"},
        job_status="success",
    )


class TestExpressionResolution:
    def test_matrix_access(self, ctx):
        assert evaluate_expression("${{ matrix.python-version }}", ctx) == "3.12"

    def test_env_access(self, ctx):
        assert evaluate_expression("${{ env.CI }}", ctx) == "true"

    def test_github_context(self, ctx):
        assert evaluate_expression("${{ github.sha }}", ctx) == "abc123"

    def test_secrets_access(self, ctx):
        assert evaluate_expression("${{ secrets.API_KEY }}", ctx) == "secret123"

    def test_step_outputs(self, ctx):
        assert evaluate_expression("${{ steps.build.outputs.artifact }}", ctx) == "dist/foo.whl"

    def test_step_outcome(self, ctx):
        assert evaluate_expression("${{ steps.test.outcome }}", ctx) == "failure"

    def test_missing_context_returns_empty(self, ctx):
        assert evaluate_expression("${{ steps.nonexistent.outputs.x }}", ctx) == ""

    def test_multiple_expressions(self, ctx):
        result = evaluate_expression("v${{ matrix.python-version }}-${{ github.sha }}", ctx)
        assert result == "v3.12-abc123"


class TestFunctions:
    def test_contains(self, ctx):
        assert evaluate_expression("${{ contains('hello world', 'world') }}", ctx) == "True"
        assert evaluate_expression("${{ contains('hello', 'xyz') }}", ctx) == "False"

    def test_starts_with(self, ctx):
        assert evaluate_expression("${{ startsWith('hello world', 'hello') }}", ctx) == "True"
        assert evaluate_expression("${{ startsWith('hello', 'world') }}", ctx) == "False"

    def test_ends_with(self, ctx):
        assert evaluate_expression("${{ endsWith('hello.tar.gz', '.tar.gz') }}", ctx) == "True"

    def test_format(self, ctx):
        result = evaluate_expression("${{ format('Hello {0}, you are {1}', 'world', 'great') }}", ctx)
        assert result == "Hello world, you are great"

    def test_success(self, ctx):
        assert evaluate_expression("${{ success() }}", ctx) == "True"

    def test_failure(self, ctx):
        assert evaluate_expression("${{ failure() }}", ctx) == "False"
        ctx.job_status = "failure"
        assert evaluate_expression("${{ failure() }}", ctx) == "True"

    def test_always(self, ctx):
        assert evaluate_expression("${{ always() }}", ctx) == "True"
        ctx.job_status = "failure"
        assert evaluate_expression("${{ always() }}", ctx) == "True"


class TestConditions:
    def test_none_condition_defaults_to_success(self, ctx):
        assert evaluate_condition(None, ctx) is True
        ctx.job_status = "failure"
        assert evaluate_condition(None, ctx) is False

    def test_always_condition(self, ctx):
        ctx.job_status = "failure"
        assert evaluate_condition("always()", ctx) is True

    def test_failure_condition(self, ctx):
        assert evaluate_condition("failure()", ctx) is False
        ctx.job_status = "failure"
        assert evaluate_condition("failure()", ctx) is True

    def test_expression_condition(self, ctx):
        assert evaluate_condition("${{ success() }}", ctx) is True

    def test_contains_condition(self, ctx):
        assert evaluate_condition("contains(matrix.os, 'ubuntu')", ctx) is True
        assert evaluate_condition("contains(matrix.os, 'windows')", ctx) is False


class TestSecrets:
    def test_file_provider(self, tmp_path):
        secrets_file = tmp_path / ".secrets"
        secrets_file.write_text(textwrap.dedent("""\
            # Comment
            API_KEY=my-secret-key
            DB_URL="postgres://localhost/db"
            EMPTY=
            QUOTED='single quotes'
        """))

        provider = FileSecretProvider(secrets_file)
        assert provider.available()
        assert provider.get("API_KEY") == "my-secret-key"
        assert provider.get("DB_URL") == "postgres://localhost/db"
        assert provider.get("EMPTY") == ""
        assert provider.get("QUOTED") == "single quotes"
        assert provider.get("MISSING") is None

    def test_env_provider(self, monkeypatch):
        monkeypatch.setenv("RUNNER_SECRET_MY_TOKEN", "abc123")
        provider = EnvSecretProvider()
        assert provider.get("MY_TOKEN") == "abc123"
        assert provider.get("NOPE") is None

    def test_store_chain_priority(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNER_SECRET_KEY", "from-env")

        secrets_file = tmp_path / ".workspace" / ".secrets"
        secrets_file.parent.mkdir(parents=True)
        secrets_file.write_text("KEY=from-file\nOTHER=file-only\n")

        store = create_default_store(tmp_path)
        # Env takes priority
        assert store.get("KEY") == "from-env"
        # File provides keys not in env
        assert store.get("OTHER") == "file-only"
        # Missing returns empty string
        assert store.get("NOPE") == ""

    def test_store_no_secrets(self, tmp_path):
        store = create_default_store(tmp_path)
        assert store.get("ANYTHING") == ""

    def test_command_provider(self, tmp_path):
        provider = CommandSecretProvider("echo secret-value")
        assert provider.available()
        assert provider.get("ignored") == "secret-value"

    def test_command_provider_not_found(self):
        provider = CommandSecretProvider("nonexistent_binary_xyz get {key}")
        assert not provider.available()
