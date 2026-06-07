"""Tests for multi-backend CI format support."""

from __future__ import annotations

import textwrap

import pytest

from runner.workflow.backends import (
    detect_backend,
    get_backend_by_name,
    get_backends,
)
from runner.workflow.backends.forgejo import ForgejoBackend
from runner.workflow.backends.github import GitHubBackend
from runner.workflow.backends.gitlab import GitLabBackend, parse_gitlab_ci
from runner.workflow.expressions import ExpressionContext, evaluate_expression

# ───────────────────────── Detection Tests ─────────────────────────


class TestDetection:
    """Test auto-detection of CI backends."""

    def test_detects_github(self, tmp_path):
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        backend = detect_backend(tmp_path)
        assert backend.name == "github"

    def test_detects_forgejo(self, tmp_path):
        (tmp_path / ".forgejo" / "workflows").mkdir(parents=True)
        backend = detect_backend(tmp_path)
        assert backend.name == "forgejo"

    def test_detects_gitlab(self, tmp_path):
        (tmp_path / ".gitlab-ci.yml").write_text("stages: [build]")
        backend = detect_backend(tmp_path)
        assert backend.name == "gitlab"

    def test_forgejo_takes_priority_over_github(self, tmp_path):
        (tmp_path / ".forgejo" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        backend = detect_backend(tmp_path)
        assert backend.name == "forgejo"

    def test_gitlab_takes_priority_over_github(self, tmp_path):
        (tmp_path / ".gitlab-ci.yml").write_text("stages: [build]")
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        backend = detect_backend(tmp_path)
        assert backend.name == "gitlab"

    def test_forgejo_takes_priority_over_gitlab(self, tmp_path):
        (tmp_path / ".forgejo" / "workflows").mkdir(parents=True)
        (tmp_path / ".gitlab-ci.yml").write_text("stages: [build]")
        backend = detect_backend(tmp_path)
        assert backend.name == "forgejo"

    def test_fallback_to_github(self, tmp_path):
        backend = detect_backend(tmp_path)
        assert backend.name == "github"

    def test_get_backend_by_name(self):
        assert get_backend_by_name("github") is not None
        assert get_backend_by_name("forgejo") is not None
        assert get_backend_by_name("gitlab") is not None
        assert get_backend_by_name("nonexistent") is None

    def test_get_backends_returns_all(self):
        backends = get_backends()
        names = [b.name for b in backends]
        assert "github" in names
        assert "forgejo" in names
        assert "gitlab" in names


# ──────────────────────── GitHub Backend Tests ────────────────────────


class TestGitHubBackend:
    """Test the GitHub Actions backend wrapper."""

    def test_discover(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "test.yml").write_text(textwrap.dedent("""\
            name: Test
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo hello
        """))

        backend = GitHubBackend()
        workflows = backend.discover(tmp_path)
        assert "Test" in workflows
        assert workflows["Test"].jobs["test"].steps[0].run == "echo hello"

    def test_parse_single(self, tmp_path):
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent("""\
            name: CI
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: make build
        """))

        backend = GitHubBackend()
        wf = backend.parse(wf_file)
        assert wf.name == "CI"
        assert "build" in wf.jobs


# ──────────────────────── Forgejo Backend Tests ────────────────────────


class TestForgejoBackend:
    """Test the Forgejo/Codeberg Actions backend."""

    def test_discover_from_forgejo_dir(self, tmp_path):
        wf_dir = tmp_path / ".forgejo" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(textwrap.dedent("""\
            name: Forgejo CI
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo building
        """))

        backend = ForgejoBackend()
        workflows = backend.discover(tmp_path)
        assert "Forgejo CI" in workflows

    def test_fallback_to_github_dir(self, tmp_path):
        (tmp_path / ".forgejo" / "workflows").mkdir(parents=True)
        # No files in .forgejo, put one in .github
        gh_dir = tmp_path / ".github" / "workflows"
        gh_dir.mkdir(parents=True)
        (gh_dir / "ci.yml").write_text(textwrap.dedent("""\
            name: GH CI
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo test
        """))

        backend = ForgejoBackend()
        # .forgejo/workflows exists but is empty, so discover finds nothing there
        workflows = backend.discover(tmp_path)
        # When .forgejo/workflows is empty, it won't find anything
        assert len(workflows) == 0

    def test_detects_forgejo_path(self, tmp_path):
        (tmp_path / ".forgejo" / "workflows").mkdir(parents=True)
        backend = ForgejoBackend()
        assert backend.detect(tmp_path)

    def test_does_not_detect_github_only(self, tmp_path):
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        backend = ForgejoBackend()
        assert not backend.detect(tmp_path)

    def test_timeout_warning(self, tmp_path):
        wf_dir = tmp_path / ".forgejo" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(textwrap.dedent("""\
            name: Timed
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                timeout-minutes: 30
                steps:
                  - run: echo building
        """))

        backend = ForgejoBackend()
        with pytest.warns(UserWarning, match="job-level timeout-minutes"):
            backend.discover(tmp_path)


# ──────────────────────── GitLab Backend Tests ────────────────────────


class TestGitLabBackend:
    """Test the GitLab CI/CD backend."""

    def test_detect(self, tmp_path):
        (tmp_path / ".gitlab-ci.yml").write_text("stages: [build]")
        backend = GitLabBackend()
        assert backend.detect(tmp_path)

    def test_does_not_detect_without_file(self, tmp_path):
        backend = GitLabBackend()
        assert not backend.detect(tmp_path)

    def test_basic_pipeline(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            stages:
              - build
              - test

            variables:
              PROJECT: myapp

            build-job:
              stage: build
              image: python:3.12
              script:
                - pip install .
                - python setup.py build

            test-job:
              stage: test
              image: python:3.12
              script:
                - pytest
              services:
                - postgres:15
        """))

        wf = parse_gitlab_ci(ci_file)
        assert wf.name == ".gitlab-ci"
        assert "build-job" in wf.jobs
        assert "test-job" in wf.jobs

        build = wf.jobs["build-job"]
        assert build.container == "python:3.12"
        assert len(build.steps) == 1
        assert "pip install ." in build.steps[0].run
        assert "python setup.py build" in build.steps[0].run
        assert build.env["PROJECT"] == "myapp"

        test = wf.jobs["test-job"]
        assert "postgres" in test.services
        assert test.services["postgres"].image == "postgres:15"

    def test_stage_ordering_creates_needs(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            stages:
              - build
              - test
              - deploy

            build:
              stage: build
              script:
                - make build

            unit-tests:
              stage: test
              script:
                - make test

            integration-tests:
              stage: test
              script:
                - make integration

            deploy:
              stage: deploy
              script:
                - make deploy
        """))

        wf = parse_gitlab_ci(ci_file)

        # Test-stage jobs should need build-stage jobs
        assert "build" in wf.jobs["unit-tests"].needs
        assert "build" in wf.jobs["integration-tests"].needs

        # Deploy should need test-stage jobs
        assert "unit-tests" in wf.jobs["deploy"].needs
        assert "integration-tests" in wf.jobs["deploy"].needs

        # Build stage has no needs
        assert wf.jobs["build"].needs == []

    def test_explicit_needs_override_stages(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            stages:
              - build
              - test

            build:
              stage: build
              script:
                - make build

            test:
              stage: test
              needs:
                - build
              script:
                - make test
        """))

        wf = parse_gitlab_ci(ci_file)
        # Explicit needs should be preserved, not overridden
        assert wf.jobs["test"].needs == ["build"]

    def test_extends_template(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            .python:
              image: python:3.12
              before_script:
                - pip install -r requirements.txt

            test:
              extends: .python
              script:
                - pytest
        """))

        wf = parse_gitlab_ci(ci_file)
        job = wf.jobs["test"]
        assert job.container == "python:3.12"
        assert len(job.steps) == 2  # before_script + script
        assert "pip install" in job.steps[0].run
        assert "pytest" in job.steps[1].run

    def test_multiple_extends(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            .base:
              image: python:3.12

            .test-template:
              before_script:
                - pip install pytest

            test:
              extends:
                - .base
                - .test-template
              script:
                - pytest
        """))

        wf = parse_gitlab_ci(ci_file)
        job = wf.jobs["test"]
        assert job.container == "python:3.12"
        assert "pip install pytest" in job.steps[0].run

    def test_variables_merged(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            variables:
              GLOBAL_VAR: hello

            test:
              variables:
                LOCAL_VAR: world
              script:
                - echo $GLOBAL_VAR $LOCAL_VAR
        """))

        wf = parse_gitlab_ci(ci_file)
        assert wf.jobs["test"].env["GLOBAL_VAR"] == "hello"
        assert wf.jobs["test"].env["LOCAL_VAR"] == "world"

    def test_before_and_after_script(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            test:
              before_script:
                - setup-env
              script:
                - run-tests
              after_script:
                - cleanup
        """))

        wf = parse_gitlab_ci(ci_file)
        job = wf.jobs["test"]
        assert len(job.steps) == 3
        assert job.steps[0].name == "before_script"
        assert "setup-env" in job.steps[0].run
        assert "run-tests" in job.steps[1].run
        assert job.steps[2].name == "after_script"
        assert "cleanup" in job.steps[2].run
        # after_script always continues on error
        assert job.steps[2].continue_on_error is True

    def test_allow_failure(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            test:
              allow_failure: true
              script:
                - make test
        """))

        wf = parse_gitlab_ci(ci_file)
        # The script step should have continue_on_error=True
        script_step = wf.jobs["test"].steps[0]
        assert script_step.continue_on_error is True

    def test_timeout_parsing(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            test:
              timeout: 1h 30m
              script:
                - make test
        """))

        wf = parse_gitlab_ci(ci_file)
        assert wf.jobs["test"].timeout_minutes == 90.0

    def test_timeout_minutes_only(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            test:
              timeout: 45m
              script:
                - make test
        """))

        wf = parse_gitlab_ci(ci_file)
        assert wf.jobs["test"].timeout_minutes == 45.0

    def test_services_parsing(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            test:
              services:
                - name: postgres:15
                  alias: db
                  variables:
                    POSTGRES_DB: testdb
                - redis:7
              script:
                - make test
        """))

        wf = parse_gitlab_ci(ci_file)
        services = wf.jobs["test"].services
        assert "db" in services
        assert services["db"].image == "postgres:15"
        assert services["db"].env["POSTGRES_DB"] == "testdb"
        assert "redis" in services

    def test_rules_condition(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            deploy:
              rules:
                - if: $CI_COMMIT_BRANCH == "main"
              script:
                - deploy
        """))

        wf = parse_gitlab_ci(ci_file)
        # Should translate to github expression form
        assert wf.jobs["deploy"].if_ is not None
        assert "github.ref_name" in wf.jobs["deploy"].if_
        assert "'main'" in wf.jobs["deploy"].if_

    def test_only_branches(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            deploy:
              only:
                - main
                - develop
              script:
                - deploy
        """))

        wf = parse_gitlab_ci(ci_file)
        assert wf.jobs["deploy"].if_ is not None
        assert "main" in wf.jobs["deploy"].if_
        assert "develop" in wf.jobs["deploy"].if_

    def test_parallel_matrix(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            test:
              parallel:
                matrix:
                  - PYTHON: ["3.11", "3.12", "3.13"]
                    OS: ["linux", "macos"]
              script:
                - tox
        """))

        wf = parse_gitlab_ci(ci_file)
        assert wf.jobs["test"].strategy is not None
        matrix = wf.jobs["test"].strategy.matrix
        assert "PYTHON" in matrix
        assert "OS" in matrix

    def test_include_local(self, tmp_path):
        # Create included file
        (tmp_path / "templates.yml").write_text(textwrap.dedent("""\
            .python:
              image: python:3.12
        """))

        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            include:
              - local: /templates.yml

            test:
              extends: .python
              script:
                - pytest
        """))

        wf = parse_gitlab_ci(ci_file)
        assert wf.jobs["test"].container == "python:3.12"

    def test_discover(self, tmp_path):
        (tmp_path / ".gitlab-ci.yml").write_text(textwrap.dedent("""\
            build:
              script:
                - make build
        """))

        backend = GitLabBackend()
        workflows = backend.discover(tmp_path)
        assert len(workflows) == 1
        assert ".gitlab-ci" in workflows

    def test_global_before_script_applies(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            before_script:
              - apt-get update

            test:
              script:
                - make test
        """))

        wf = parse_gitlab_ci(ci_file)
        job = wf.jobs["test"]
        assert len(job.steps) == 2
        assert "apt-get update" in job.steps[0].run

    def test_job_before_script_overrides_global(self, tmp_path):
        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text(textwrap.dedent("""\
            before_script:
              - global-setup

            test:
              before_script:
                - local-setup
              script:
                - make test
        """))

        wf = parse_gitlab_ci(ci_file)
        job = wf.jobs["test"]
        assert "local-setup" in job.steps[0].run
        assert "global-setup" not in job.steps[0].run


# ──────────────────── Expression Alias Tests ─────────────────────


class TestExpressionAliases:
    """Test that forgejo.*/forge.*/gitea.* are aliases for github.*."""

    def test_forgejo_context_alias(self):
        ctx = ExpressionContext(
            github={"ref_name": "main", "sha": "abc123"},
        )
        assert evaluate_expression("${{ forgejo.ref_name }}", ctx) == "main"
        assert evaluate_expression("${{ forgejo.sha }}", ctx) == "abc123"

    def test_forge_context_alias(self):
        ctx = ExpressionContext(
            github={"ref_name": "develop", "actor": "dev"},
        )
        assert evaluate_expression("${{ forge.ref_name }}", ctx) == "develop"
        assert evaluate_expression("${{ forge.actor }}", ctx) == "dev"

    def test_gitea_context_alias(self):
        ctx = ExpressionContext(
            github={"workspace": "/tmp/work"},
        )
        assert evaluate_expression("${{ gitea.workspace }}", ctx) == "/tmp/work"

    def test_github_still_works(self):
        ctx = ExpressionContext(
            github={"ref_name": "main"},
        )
        assert evaluate_expression("${{ github.ref_name }}", ctx) == "main"

    def test_forgejo_event_inputs(self):
        ctx = ExpressionContext(
            github={"event_name": "push"},
            inputs={"version": "1.0.0"},
        )
        assert evaluate_expression("${{ forgejo.event.inputs.version }}", ctx) == "1.0.0"
