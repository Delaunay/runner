"""Tests for new features: services, artifacts, cache, job outputs,
job-level if, boolean expressions, defaults, container key, needs context."""

import textwrap
from pathlib import Path

import pytest

from runner.workflow.executor import (
    ExecutionContext,
    execute_step,
    execute_workflow,
)
from runner.workflow.expressions import (
    ExpressionContext,
    _eval_inner,
    evaluate_condition,
    evaluate_expression,
)
from runner.workflow.parser import (
    ServiceDef,
    Step,
    parse_workflow,
)


@pytest.fixture
def tmp_workflow(tmp_path):
    """Create a temporary workflow file."""
    def _make(content: str, name: str = "test.yml") -> Path:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        wf_file = wf_dir / name
        wf_file.write_text(textwrap.dedent(content))
        return wf_file
    return _make


class TestParserServices:
    def test_parse_services(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Services
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                services:
                  postgres:
                    image: postgres:15
                    env:
                      POSTGRES_PASSWORD: test
                    ports:
                      - "5432:5432"
                    volumes:
                      - /tmp/data:/var/lib/postgresql/data
                  redis:
                    image: redis:7
                    ports:
                      - "6379:6379"
                steps:
                  - run: echo test
        """)

        wf = parse_workflow(path)
        job = wf.jobs["test"]
        assert "postgres" in job.services
        assert "redis" in job.services

        pg = job.services["postgres"]
        assert pg.image == "postgres:15"
        assert pg.env == {"POSTGRES_PASSWORD": "test"}
        assert pg.ports == ["5432:5432"]
        assert pg.volumes == ["/tmp/data:/var/lib/postgresql/data"]

        redis = job.services["redis"]
        assert redis.image == "redis:7"
        assert redis.ports == ["6379:6379"]

    def test_service_def_defaults(self):
        svc = ServiceDef(image="nginx:latest")
        assert svc.env == {}
        assert svc.ports == []
        assert svc.volumes == []
        assert svc.options == ""


class TestParserDefaults:
    def test_parse_workflow_defaults(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Defaults
            on: push
            defaults:
              run:
                shell: bash
                working-directory: src
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo test
        """)

        wf = parse_workflow(path)
        assert wf.defaults is not None
        assert wf.defaults.run_shell == "bash"
        assert wf.defaults.run_working_directory == "src"

    def test_parse_job_defaults(self, tmp_workflow):
        path = tmp_workflow("""\
            name: JobDefaults
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                defaults:
                  run:
                    shell: python
                    working-directory: scripts
                steps:
                  - run: print('hello')
        """)

        wf = parse_workflow(path)
        job = wf.jobs["test"]
        assert job.defaults is not None
        assert job.defaults.run_shell == "python"
        assert job.defaults.run_working_directory == "scripts"


class TestParserJobOutputs:
    def test_parse_job_outputs(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Outputs
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                outputs:
                  version: ${{ steps.ver.outputs.version }}
                  sha: ${{ github.sha }}
                steps:
                  - id: ver
                    run: echo "version=1.0.0" >> $GITHUB_OUTPUT
        """)

        wf = parse_workflow(path)
        job = wf.jobs["build"]
        assert job.outputs == {
            "version": "${{ steps.ver.outputs.version }}",
            "sha": "${{ github.sha }}",
        }


class TestParserContainer:
    def test_parse_container_string(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Container
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                container: node:18
                steps:
                  - run: node --version
        """)

        wf = parse_workflow(path)
        assert wf.jobs["test"].container == "node:18"

    def test_parse_container_dict(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Container
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                container:
                  image: python:3.12
                steps:
                  - run: python --version
        """)

        wf = parse_workflow(path)
        assert wf.jobs["test"].container == "python:3.12"


class TestParserJobLevelIf:
    def test_parse_job_if(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Conditional
            on: push
            jobs:
              deploy:
                if: github.ref == 'refs/heads/main'
                runs-on: ubuntu-latest
                steps:
                  - run: echo deploy
        """)

        wf = parse_workflow(path)
        assert wf.jobs["deploy"].if_ == "github.ref == 'refs/heads/main'"


class TestParserTimeoutMinutes:
    def test_parse_job_timeout(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Timeout
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                timeout-minutes: 30
                steps:
                  - run: echo test
        """)

        wf = parse_workflow(path)
        assert wf.jobs["test"].timeout_minutes == 30.0


class TestBooleanExpressions:
    def test_equality(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/main"})
        result = _eval_inner("github.ref == 'refs/heads/main'", ctx)
        assert result is True

    def test_inequality(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/dev"})
        result = _eval_inner("github.ref != 'refs/heads/main'", ctx)
        assert result is True

    def test_and_operator(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/main", "event_name": "push"})
        result = _eval_inner("github.ref == 'refs/heads/main' && github.event_name == 'push'", ctx)
        assert result is True

    def test_and_operator_false(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/dev", "event_name": "push"})
        result = _eval_inner("github.ref == 'refs/heads/main' && github.event_name == 'push'", ctx)
        assert result is False

    def test_or_operator(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/dev"})
        result = _eval_inner("github.ref == 'refs/heads/main' || github.ref == 'refs/heads/dev'", ctx)
        assert result is True

    def test_or_operator_false(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/feature"})
        result = _eval_inner("github.ref == 'refs/heads/main' || github.ref == 'refs/heads/dev'", ctx)
        assert result is False

    def test_negation(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/dev"})
        result = _eval_inner("!contains(github.ref, 'main')", ctx)
        assert result is True

    def test_complex_expression(self):
        ctx = ExpressionContext(
            github={"ref": "refs/heads/main", "event_name": "push"},
            env={"DEPLOY": "true"},
        )
        result = _eval_inner(
            "github.ref == 'refs/heads/main' && (github.event_name == 'push' || env.DEPLOY == 'true')",
            ctx,
        )
        assert result is True

    def test_string_literal(self):
        ctx = ExpressionContext()
        result = _eval_inner("'hello'", ctx)
        assert result == "hello"

    def test_boolean_literal_true(self):
        ctx = ExpressionContext()
        result = _eval_inner("true", ctx)
        assert result is True

    def test_boolean_literal_false(self):
        ctx = ExpressionContext()
        result = _eval_inner("false", ctx)
        assert result is False

    def test_equality_in_condition(self):
        ctx = ExpressionContext(github={"ref": "refs/heads/main"})
        assert evaluate_condition("github.ref == 'refs/heads/main'", ctx) is True
        assert evaluate_condition("github.ref != 'refs/heads/main'", ctx) is False

    def test_evaluate_expression_with_boolean(self, tmp_path):
        ctx = ExpressionContext(github={"ref": "refs/heads/main"})
        result = evaluate_expression(
            "${{ github.ref == 'refs/heads/main' }}", ctx
        )
        assert result == "True"


class TestNeedsContext:
    def test_needs_outputs(self):
        ctx = ExpressionContext(
            needs={
                "build": {
                    "outputs": {"version": "1.2.3", "sha": "abc123"},
                    "result": "success",
                }
            }
        )
        result = _eval_inner("needs.build.outputs.version", ctx)
        assert result == "1.2.3"

    def test_needs_result(self):
        ctx = ExpressionContext(
            needs={
                "build": {"outputs": {}, "result": "success"}
            }
        )
        result = _eval_inner("needs.build.result", ctx)
        assert result == "success"

    def test_needs_missing_job(self):
        ctx = ExpressionContext(needs={})
        result = _eval_inner("needs.build.outputs.version", ctx)
        assert result == ""

    def test_evaluate_expression_needs(self):
        ctx = ExpressionContext(
            needs={"build": {"outputs": {"ver": "2.0"}, "result": "success"}}
        )
        result = evaluate_expression("${{ needs.build.outputs.ver }}", ctx)
        assert result == "2.0"


class TestJobLevelIfExecution:
    def test_job_skipped_by_if(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Conditional
            on: push
            jobs:
              skip_me:
                if: false
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
              always_run:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root)
        assert success

    def test_job_runs_with_true_if(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Conditional
            on: push
            jobs:
              run_me:
                if: true
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root)
        assert success


class TestDefaults:
    def test_workflow_defaults_apply_to_steps(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Defaults
            on: push
            defaults:
              run:
                shell: python
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: PyScript
                    run: print('hello from python')
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root, verbose=True)
        assert success

    def test_step_shell_overrides_default(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Override
            on: push
            defaults:
              run:
                shell: python
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: BashStep
                    shell: bash
                    run: echo hello
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root, verbose=True)
        assert success

    def test_job_defaults_override_workflow(self, tmp_workflow):
        path = tmp_workflow("""\
            name: JobOverride
            on: push
            defaults:
              run:
                shell: bash
            jobs:
              test:
                runs-on: ubuntu-latest
                defaults:
                  run:
                    shell: python
                steps:
                  - name: PyScript
                    run: print('job default')
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root, verbose=True)
        assert success


class TestArtifacts:
    def test_upload_artifact(self, tmp_path):
        # Create a file to upload
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "output.txt").write_text("hello")

        step = Step(
            uses="actions/upload-artifact@v4",
            with_={"name": "my-artifact", "path": "dist/output.txt"},
        )
        ctx = ExecutionContext(root=tmp_path, verbose=True)
        result = execute_step(step, ctx)
        assert result.success

        # Check artifact was stored
        artifact_path = tmp_path / ".workspace" / "artifacts" / "my-artifact" / "dist" / "output.txt"
        assert artifact_path.exists()
        assert artifact_path.read_text() == "hello"

    def test_download_artifact(self, tmp_path):
        # Set up an artifact
        artifact_dir = tmp_path / ".workspace" / "artifacts" / "my-artifact"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "result.txt").write_text("world")

        step = Step(
            uses="actions/download-artifact@v4",
            with_={"name": "my-artifact", "path": "downloaded"},
        )
        ctx = ExecutionContext(root=tmp_path, verbose=True)
        result = execute_step(step, ctx)
        assert result.success

        # Check file was restored
        assert (tmp_path / "downloaded" / "result.txt").exists()
        assert (tmp_path / "downloaded" / "result.txt").read_text() == "world"

    def test_download_missing_artifact(self, tmp_path):
        step = Step(
            uses="actions/download-artifact@v4",
            with_={"name": "nonexistent"},
        )
        ctx = ExecutionContext(root=tmp_path)
        result = execute_step(step, ctx)
        assert result.success  # doesn't fail, just warns


class TestCache:
    def test_cache_miss_then_hit(self, tmp_path):
        cache_dir = tmp_path / ".workspace" / "cache" / "deps-abc123"
        cache_dir.mkdir(parents=True)
        (cache_dir / "cached_file.txt").write_text("cached")

        step = Step(
            uses="actions/cache@v4",
            with_={"key": "deps-abc123", "path": "node_modules"},
        )
        ctx = ExecutionContext(root=tmp_path, verbose=True)
        result = execute_step(step, ctx)
        assert result.success

        # Verify restore from cache
        assert (tmp_path / "node_modules" / "cached_file.txt").exists()
        assert (tmp_path / "node_modules" / "cached_file.txt").read_text() == "cached"

    def test_cache_miss_no_error(self, tmp_path):
        step = Step(
            uses="actions/cache@v4",
            with_={"key": "brand-new-key", "path": "some_dir"},
        )
        ctx = ExecutionContext(root=tmp_path, verbose=True)
        result = execute_step(step, ctx)
        assert result.success


class TestJobOutputs:
    def test_job_outputs_in_workflow(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Outputs
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                outputs:
                  greeting: hello-world
                steps:
                  - run: echo done
              deploy:
                needs: build
                runs-on: ubuntu-latest
                steps:
                  - run: echo "${{ needs.build.outputs.greeting }}"
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root, verbose=True)
        assert success


class TestDependencyFailure:
    def test_job_skipped_on_dependency_failure(self, tmp_workflow):
        path = tmp_workflow("""\
            name: DepFail
            on: push
            jobs:
              first:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
              second:
                needs: first
                runs-on: ubuntu-latest
                steps:
                  - run: echo "should not run"
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root)
        assert not success


class TestRunsOnList:
    def test_runs_on_list_uses_first(self, tmp_workflow):
        path = tmp_workflow("""\
            name: RunsOnList
            on: push
            jobs:
              test:
                runs-on: [ubuntu-latest, ubuntu-22.04]
                steps:
                  - run: echo test
        """)

        wf = parse_workflow(path)
        assert wf.jobs["test"].runs_on == "ubuntu-latest"


class TestReusableWorkflows:
    def test_parse_reusable_workflow_job(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Caller
            on: push
            jobs:
              call_reusable:
                uses: ./.github/workflows/reusable.yml
                with:
                  config: production
                secrets: inherit
        """)

        wf = parse_workflow(path)
        job = wf.jobs["call_reusable"]
        assert job.uses == "./.github/workflows/reusable.yml"
        assert job.with_ == {"config": "production"}
        assert job.secrets_ == "inherit"

    def test_execute_reusable_workflow(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)

        # Create the reusable workflow
        (wf_dir / "reusable.yml").write_text(textwrap.dedent("""\
            name: Reusable
            on: workflow_call
            jobs:
              inner:
                runs-on: ubuntu-latest
                steps:
                  - run: echo "reusable ran"
        """))

        # Create the caller workflow
        (wf_dir / "caller.yml").write_text(textwrap.dedent("""\
            name: Caller
            on: push
            jobs:
              call_it:
                uses: ./.github/workflows/reusable.yml
        """))

        wf = parse_workflow(wf_dir / "caller.yml")
        success = execute_workflow(wf, root=tmp_path)
        assert success

    def test_reusable_workflow_with_inputs(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)

        (wf_dir / "reusable.yml").write_text(textwrap.dedent("""\
            name: Reusable
            on: workflow_call
            jobs:
              inner:
                runs-on: ubuntu-latest
                steps:
                  - run: echo "$INPUT_MESSAGE"
        """))

        (wf_dir / "caller.yml").write_text(textwrap.dedent("""\
            name: Caller
            on: push
            jobs:
              call_it:
                uses: ./.github/workflows/reusable.yml
                with:
                  message: hello-reusable
        """))

        wf = parse_workflow(wf_dir / "caller.yml")
        success = execute_workflow(wf, root=tmp_path, verbose=True)
        assert success
