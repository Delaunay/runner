"""Tests for the native GitLab CI/CD runner."""

from __future__ import annotations

import textwrap
from pathlib import Path

from runner.gitlab.executor import (
    PipelineContext,
    _eval_rule_condition,
    _evaluate_rules,
    _expand_variables,
    _should_run_job,
    execute_pipeline,
)
from runner.gitlab.parser import (
    GitLabJob,
    GitLabRule,
    GitLabService,
    parse_pipeline,
)

# ──────────────────── Parser Tests ────────────────────


class TestGitLabParser:
    """Test native GitLab CI parser."""

    def test_basic_pipeline(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            stages:
              - build
              - test

            build:
              stage: build
              image: python:3.12
              script:
                - pip install .
                - python -m build

            test:
              stage: test
              image: python:3.12
              script:
                - pytest
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.stages == ["build", "test"]
        assert "build" in pipeline.jobs
        assert "test" in pipeline.jobs
        assert pipeline.jobs["build"].image == "python:3.12"
        assert pipeline.jobs["build"].script == ["pip install .", "python -m build"]
        assert pipeline.jobs["test"].script == ["pytest"]

    def test_services(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            test:
              services:
                - name: postgres:15
                  alias: db
                  variables:
                    POSTGRES_DB: testdb
                - redis:7
              script:
                - pytest
        """))

        pipeline = parse_pipeline(ci)
        job = pipeline.jobs["test"]
        assert len(job.services) == 2
        assert job.services[0].hostname == "db"
        assert job.services[0].image == "postgres:15"
        assert job.services[0].variables["POSTGRES_DB"] == "testdb"
        assert job.services[1].hostname == "redis"

    def test_artifacts(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            build:
              script:
                - make build
              artifacts:
                paths:
                  - dist/
                  - build/output.log
                exclude:
                  - "*.tmp"
                expire_in: 1 week
                when: always
        """))

        pipeline = parse_pipeline(ci)
        art = pipeline.jobs["build"].artifacts
        assert art is not None
        assert art.paths == ["dist/", "build/output.log"]
        assert art.exclude == ["*.tmp"]
        assert art.expire_in == "1 week"
        assert art.when == "always"

    def test_cache(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            test:
              cache:
                key: pip-cache
                paths:
                  - .cache/pip
                policy: pull-push
              script:
                - pytest
        """))

        pipeline = parse_pipeline(ci)
        cache = pipeline.jobs["test"].cache
        assert cache is not None
        assert cache.key == "pip-cache"
        assert cache.paths == [".cache/pip"]
        assert cache.policy == "pull-push"

    def test_rules(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            deploy:
              rules:
                - if: $CI_COMMIT_BRANCH == "main"
                  when: on_success
                - if: $CI_COMMIT_BRANCH == "develop"
                  when: manual
                  allow_failure: true
                - when: never
              script:
                - deploy
        """))

        pipeline = parse_pipeline(ci)
        rules = pipeline.jobs["deploy"].rules
        assert len(rules) == 3
        assert rules[0].if_ == '$CI_COMMIT_BRANCH == "main"'
        assert rules[0].when == "on_success"
        assert rules[1].if_ == '$CI_COMMIT_BRANCH == "develop"'
        assert rules[1].when == "manual"
        assert rules[1].allow_failure is True
        assert rules[2].when == "never"

    def test_extends(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            .python:
              image: python:3.12
              before_script:
                - pip install -r requirements.txt

            test:
              extends: .python
              script:
                - pytest
        """))

        pipeline = parse_pipeline(ci)
        job = pipeline.jobs["test"]
        assert job.image == "python:3.12"
        assert job.before_script == ["pip install -r requirements.txt"]
        assert job.script == ["pytest"]

    def test_needs_explicit(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
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
                - job: build
              script:
                - make test
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.jobs["test"].needs == ["build"]
        assert pipeline.jobs["test"].needs_explicit is True

    def test_variables_inheritance(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            variables:
              GLOBAL: hello

            test:
              variables:
                LOCAL: world
              script:
                - echo $GLOBAL $LOCAL
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.jobs["test"].variables["GLOBAL"] == "hello"
        assert pipeline.jobs["test"].variables["LOCAL"] == "world"

    def test_allow_failure(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            lint:
              allow_failure: true
              script:
                - ruff check .
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.jobs["lint"].allow_failure is True

    def test_retry(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            flaky-test:
              retry: 2
              script:
                - pytest --flaky
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.jobs["flaky-test"].retry == 2

    def test_timeout(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            long-test:
              timeout: 2h 30m
              script:
                - make test-all
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.jobs["long-test"].timeout == "2h 30m"
        assert pipeline.jobs["long-test"].timeout_seconds == 9000.0

    def test_include_local(self, tmp_path):
        (tmp_path / "templates.yml").write_text(textwrap.dedent("""\
            .base:
              image: alpine:latest
        """))

        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            include:
              - local: /templates.yml

            test:
              extends: .base
              script:
                - echo hello
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.jobs["test"].image == "alpine:latest"

    def test_active_stages(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            stages:
              - build
              - test
              - deploy

            test-job:
              stage: test
              script:
                - pytest
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.active_stages() == ["test"]

    def test_jobs_for_stage(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            stages:
              - test

            unit:
              stage: test
              script:
                - pytest tests/unit

            integration:
              stage: test
              script:
                - pytest tests/integration
        """))

        pipeline = parse_pipeline(ci)
        jobs = pipeline.jobs_for_stage("test")
        names = [j.name for j in jobs]
        assert "unit" in names
        assert "integration" in names

    def test_default_image(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            default:
              image: python:3.12

            test:
              script:
                - pytest
        """))

        pipeline = parse_pipeline(ci)
        assert pipeline.jobs["test"].image == "python:3.12"

    def test_service_hostname_default(self):
        svc = GitLabService(name="registry.example.com/my/postgres:15")
        assert svc.hostname == "registry.example.com-my-postgres"


# ──────────────────── Variable Expansion Tests ────────────────────


class TestVariableExpansion:
    """Test GitLab-style variable expansion."""

    def test_simple_var(self):
        assert _expand_variables("echo $FOO", {"FOO": "bar"}) == "echo bar"

    def test_braced_var(self):
        assert _expand_variables("echo ${FOO}", {"FOO": "bar"}) == "echo bar"

    def test_unset_var_unchanged(self):
        assert _expand_variables("echo $MISSING", {}) == "echo $MISSING"

    def test_multiple_vars(self):
        env = {"A": "1", "B": "2"}
        assert _expand_variables("$A and $B", env) == "1 and 2"

    def test_mixed_braced_and_plain(self):
        env = {"X": "hello", "Y": "world"}
        assert _expand_variables("${X} $Y", env) == "hello world"


# ──────────────────── Rules Evaluation Tests ────────────────────


class TestRulesEvaluation:
    """Test GitLab CI rules evaluation."""

    def test_simple_equality(self):
        variables = {"CI_COMMIT_BRANCH": "main"}
        assert _eval_rule_condition('$CI_COMMIT_BRANCH == "main"', variables)
        assert not _eval_rule_condition('$CI_COMMIT_BRANCH == "develop"', variables)

    def test_inequality(self):
        variables = {"CI_COMMIT_BRANCH": "main"}
        assert _eval_rule_condition('$CI_COMMIT_BRANCH != "develop"', variables)
        assert not _eval_rule_condition('$CI_COMMIT_BRANCH != "main"', variables)

    def test_truthy_check(self):
        assert _eval_rule_condition("$MY_VAR", {"MY_VAR": "yes"})
        assert not _eval_rule_condition("$MY_VAR", {})

    def test_regex_match(self):
        variables = {"CI_COMMIT_BRANCH": "feature/login"}
        assert _eval_rule_condition("$CI_COMMIT_BRANCH =~ /^feature/", variables)
        assert not _eval_rule_condition("$CI_COMMIT_BRANCH =~ /^hotfix/", variables)

    def test_regex_not_match(self):
        variables = {"CI_COMMIT_BRANCH": "main"}
        assert _eval_rule_condition("$CI_COMMIT_BRANCH !~ /^feature/", variables)

    def test_and_operator(self):
        variables = {"A": "1", "B": "2"}
        assert _eval_rule_condition("$A && $B", variables)
        assert not _eval_rule_condition("$A && $MISSING", variables)

    def test_or_operator(self):
        variables = {"A": "1"}
        assert _eval_rule_condition("$A || $MISSING", variables)
        assert not _eval_rule_condition("$NOPE || $MISSING", variables)

    def test_evaluate_rules_first_match(self):
        rules = [
            GitLabRule(if_='$CI_COMMIT_BRANCH == "main"', when="on_success"),
            GitLabRule(when="never"),
        ]
        ctx = PipelineContext(
            root=Path("/tmp"),
            ci_vars={"CI_COMMIT_BRANCH": "main"},
        )
        assert _evaluate_rules(rules, ctx) is True

    def test_evaluate_rules_never(self):
        rules = [
            GitLabRule(if_='$CI_COMMIT_BRANCH == "main"', when="on_success"),
            GitLabRule(when="never"),
        ]
        ctx = PipelineContext(
            root=Path("/tmp"),
            ci_vars={"CI_COMMIT_BRANCH": "develop"},
        )
        # Second rule (when: never with no condition) matches unconditionally
        assert _evaluate_rules(rules, ctx) is False

    def test_evaluate_rules_no_rules(self):
        job = GitLabJob(name="test", script=["echo hi"])
        ctx = PipelineContext(root=Path("/tmp"))
        assert _should_run_job(job, ctx) is True


# ──────────────────── Executor Integration Tests ────────────────────


class TestPipelineExecution:
    """Integration tests for pipeline execution."""

    def test_simple_pipeline(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            stages:
              - test

            test:
              stage: test
              script:
                - echo "hello from test"
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, verbose=False, no_container=True,
        )
        assert result.success
        assert "test" in result.job_results
        assert result.job_results["test"].status == "success"

    def test_failing_script(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            test:
              script:
                - exit 1
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
        )
        assert not result.success
        assert result.job_results["test"].status == "failed"

    def test_allow_failure(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            lint:
              allow_failure: true
              script:
                - exit 1
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
        )
        # Pipeline should succeed because job allows failure
        assert result.success
        assert result.job_results["lint"].status == "failed"
        assert result.job_results["lint"].allow_failure is True

    def test_before_script_failure_skips_script(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            test:
              before_script:
                - exit 1
              script:
                - echo "should not run"
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
        )
        assert not result.success
        jr = result.job_results["test"]
        assert jr.before_script_result.returncode == 1
        assert jr.script_result is None  # script was never run

    def test_after_script_always_runs(self, tmp_path):
        marker = tmp_path / "after_ran"
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent(f"""\
            test:
              script:
                - exit 1
              after_script:
                - touch {marker}
        """))

        pipeline = parse_pipeline(ci)
        execute_pipeline(pipeline, root=tmp_path, no_container=True)
        assert marker.exists()

    def test_variable_expansion_in_script(self, tmp_path):
        output = tmp_path / "out.txt"
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent(f"""\
            variables:
              GREETING: hello

            test:
              variables:
                TARGET: world
              script:
                - echo "$GREETING $TARGET" > {output}
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
        )
        assert result.success
        assert output.read_text().strip() == "hello world"

    def test_stage_ordering_stops_on_failure(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            stages:
              - build
              - test
              - deploy

            build:
              stage: build
              script:
                - exit 1

            test:
              stage: test
              script:
                - echo test

            deploy:
              stage: deploy
              script:
                - echo deploy
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
        )
        assert not result.success
        assert "build" in result.job_results
        # test and deploy should not have run
        assert "test" not in result.job_results
        assert "deploy" not in result.job_results

    def test_skip_jobs_filter(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            lint:
              script:
                - exit 1

            test:
              script:
                - echo pass
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True, skip_jobs=["lint"],
        )
        assert result.success
        assert result.job_results["lint"].status == "skipped"
        assert result.job_results["test"].status == "success"

    def test_only_jobs_filter(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            lint:
              script:
                - echo lint

            test:
              script:
                - echo test
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True, only_jobs=["test"],
        )
        assert result.success
        assert result.job_results["lint"].status == "skipped"
        assert result.job_results["test"].status == "success"

    def test_only_stage_filter(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            stages:
              - build
              - test

            build:
              stage: build
              script:
                - echo build

            test:
              stage: test
              script:
                - echo test
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True, only_stage="test",
        )
        assert result.success
        assert "build" not in result.job_results
        assert "test" in result.job_results

    def test_dry_run(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            test:
              script:
                - rm -rf /important  # would be dangerous!
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, dry_run=True, no_container=True,
        )
        assert result.success

    def test_manual_job_skipped(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            deploy:
              when: manual
              script:
                - deploy.sh
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
        )
        assert result.success
        assert result.job_results["deploy"].status == "manual"

    def test_manual_job_runs_when_explicitly_selected(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            deploy:
              when: manual
              script:
                - echo deployed
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True, only_jobs=["deploy"],
        )
        assert result.success
        assert result.job_results["deploy"].status == "success"

    def test_artifacts_saved(self, tmp_path):
        (tmp_path / "output.txt").write_text("artifact content")
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            build:
              script:
                - echo done
              artifacts:
                paths:
                  - output.txt
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
        )
        assert result.success
        artifact_file = tmp_path / ".workspace" / "artifacts" / "build" / "output.txt"
        assert artifact_file.exists()
        assert artifact_file.read_text() == "artifact content"

    def test_rules_exclude_job(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent("""\
            deploy:
              rules:
                - if: $CI_COMMIT_BRANCH == "main"
              script:
                - deploy.sh
        """))

        pipeline = parse_pipeline(ci)
        # Running without CI_COMMIT_BRANCH=main should exclude the job
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
            variables={"CI_COMMIT_BRANCH": "develop"},
        )
        assert result.success
        assert result.job_results["deploy"].status == "skipped"

    def test_extra_variables_passed(self, tmp_path):
        output = tmp_path / "out.txt"
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(textwrap.dedent(f"""\
            test:
              script:
                - echo $MY_VAR > {output}
        """))

        pipeline = parse_pipeline(ci)
        result = execute_pipeline(
            pipeline, root=tmp_path, no_container=True,
            variables={"MY_VAR": "custom_value"},
        )
        assert result.success
        assert output.read_text().strip() == "custom_value"
