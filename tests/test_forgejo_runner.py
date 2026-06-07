"""Tests for the native Forgejo/Codeberg Actions runner."""

from __future__ import annotations

import textwrap

import pytest

from runner.forgejo.executor import (
    _build_forgejo_env,
    execute_workflow,
    resolve_runner_label,
)
from runner.forgejo.parser import discover_workflows, parse_workflow

# ──────────────────── Parser Tests ────────────────────


class TestForgejoParser:
    """Test Forgejo-specific workflow parsing and discovery."""

    def test_parse_standard_workflow(self, tmp_path):
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent("""\
            name: Forgejo CI
            on: [push, pull_request]
            jobs:
              test:
                runs-on: docker
                steps:
                  - uses: actions/checkout@v4
                  - run: make test
        """))

        wf = parse_workflow(wf_file)
        assert wf.name == "Forgejo CI"
        assert "test" in wf.jobs
        assert wf.jobs["test"].runs_on == "docker"
        assert len(wf.jobs["test"].steps) == 2

    def test_discover_from_forgejo_dir(self, tmp_path):
        wf_dir = tmp_path / ".forgejo" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(textwrap.dedent("""\
            name: Build
            on: [push]
            jobs:
              build:
                runs-on: docker
                steps:
                  - run: echo building
        """))

        workflows = discover_workflows(tmp_path)
        assert "Build" in workflows

    def test_discover_falls_back_to_github_dir(self, tmp_path):
        gh_dir = tmp_path / ".github" / "workflows"
        gh_dir.mkdir(parents=True)
        (gh_dir / "test.yml").write_text(textwrap.dedent("""\
            name: Test
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo test
        """))

        workflows = discover_workflows(tmp_path)
        assert "Test" in workflows

    def test_forgejo_dir_takes_priority(self, tmp_path):
        # Both dirs exist but only .forgejo is used
        forgejo_dir = tmp_path / ".forgejo" / "workflows"
        forgejo_dir.mkdir(parents=True)
        (forgejo_dir / "forgejo.yml").write_text(textwrap.dedent("""\
            name: Forgejo
            on: [push]
            jobs:
              build:
                runs-on: docker
                steps:
                  - run: echo forgejo
        """))

        gh_dir = tmp_path / ".github" / "workflows"
        gh_dir.mkdir(parents=True)
        (gh_dir / "github.yml").write_text(textwrap.dedent("""\
            name: GitHub
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo github
        """))

        workflows = discover_workflows(tmp_path)
        assert "Forgejo" in workflows
        assert "GitHub" not in workflows

    def test_empty_dir_returns_empty(self, tmp_path):
        workflows = discover_workflows(tmp_path)
        assert workflows == {}

    def test_timeout_warning(self, tmp_path):
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent("""\
            name: Timed
            on: [push]
            jobs:
              build:
                runs-on: docker
                timeout-minutes: 30
                steps:
                  - run: echo building
        """))

        with pytest.warns(UserWarning, match="job-level timeout-minutes"):
            parse_workflow(wf_file)

    def test_concurrency_warning(self, tmp_path):
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent("""\
            name: Concurrent
            on: [push]
            jobs:
              deploy:
                runs-on: docker
                concurrency: deploy-group
                steps:
                  - run: deploy.sh
        """))

        with pytest.warns(UserWarning, match="concurrency groups"):
            parse_workflow(wf_file)


# ──────────────────── Executor Tests ────────────────────


class TestForgejoExecutor:
    """Test Forgejo-specific execution behavior."""

    def test_simple_workflow(self, tmp_path):
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent("""\
            name: Test
            on: [push]
            jobs:
              test:
                runs-on: docker
                steps:
                  - run: echo "hello from forgejo"
        """))

        wf = parse_workflow(wf_file)
        result = execute_workflow(
            wf, root=tmp_path, verbose=False, ignore_runs_on=True,
        )
        assert result is True

    def test_forgejo_env_injected(self, tmp_path):
        output = tmp_path / "env_out.txt"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: Env Test
            on: [push]
            jobs:
              test:
                runs-on: docker
                steps:
                  - run: echo "$FORGEJO_ACTIONS" > {output}
        """))

        wf = parse_workflow(wf_file)
        execute_workflow(wf, root=tmp_path, ignore_runs_on=True)
        assert output.read_text().strip() == "true"

    def test_gitea_env_injected(self, tmp_path):
        output = tmp_path / "gitea_out.txt"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: Gitea Env
            on: [push]
            jobs:
              test:
                runs-on: docker
                steps:
                  - run: echo "$GITEA_ACTIONS" > {output}
        """))

        wf = parse_workflow(wf_file)
        execute_workflow(wf, root=tmp_path, ignore_runs_on=True)
        assert output.read_text().strip() == "true"

    def test_matrix_expansion(self, tmp_path):
        output = tmp_path / "matrix_out.txt"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: Matrix
            on: [push]
            jobs:
              test:
                runs-on: docker
                strategy:
                  matrix:
                    version: ["1", "2"]
                steps:
                  - run: echo "${{{{ matrix.version }}}}" >> {output}
        """))

        wf = parse_workflow(wf_file)
        execute_workflow(wf, root=tmp_path, ignore_runs_on=True)
        content = output.read_text().strip().split("\n")
        assert "1" in content
        assert "2" in content

    def test_skip_steps(self, tmp_path):
        marker = tmp_path / "should_not_exist"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: Skip
            on: [push]
            jobs:
              test:
                runs-on: docker
                steps:
                  - name: Setup
                    run: touch {marker}
                  - name: Test
                    run: echo ok
        """))

        wf = parse_workflow(wf_file)
        execute_workflow(
            wf, root=tmp_path, ignore_runs_on=True,
            skip_steps=["Setup"],
        )
        assert not marker.exists()

    def test_dry_run(self, tmp_path):
        marker = tmp_path / "should_not_exist"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: DryRun
            on: [push]
            jobs:
              test:
                runs-on: docker
                steps:
                  - run: touch {marker}
        """))

        wf = parse_workflow(wf_file)
        result = execute_workflow(wf, root=tmp_path, dry_run=True, ignore_runs_on=True)
        assert result is True
        assert not marker.exists()


# ──────────────────── Helper Tests ────────────────────


class TestRunnerLabels:
    """Test Forgejo runner label mapping."""

    def test_docker_label(self):
        assert resolve_runner_label("docker") == "ubuntu:latest"

    def test_ubuntu_latest(self):
        assert resolve_runner_label("ubuntu-latest") == "ubuntu:latest"

    def test_python_label(self):
        assert resolve_runner_label("python") == "python:3"

    def test_unknown_label(self):
        assert resolve_runner_label("custom-runner") is None

    def test_case_insensitive(self):
        assert resolve_runner_label("Docker") == "ubuntu:latest"
        assert resolve_runner_label("PYTHON") == "python:3"


class TestForgejoEnv:
    """Test Forgejo environment variable building."""

    def test_base_vars(self, tmp_path):
        env = _build_forgejo_env(tmp_path)
        assert env["GITEA_ACTIONS"] == "true"
        assert env["FORGEJO_ACTIONS"] == "true"
        assert "RUNNER_OS" in env
        assert "RUNNER_ARCH" in env

    def test_runner_os_set(self, tmp_path):
        import sys
        env = _build_forgejo_env(tmp_path)
        if sys.platform == "linux":
            assert env["RUNNER_OS"] == "Linux"
        elif sys.platform == "darwin":
            assert env["RUNNER_OS"] == "macOS"
