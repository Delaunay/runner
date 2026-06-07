"""Tests for the GitHub Actions workflow parser and executor."""

import textwrap
from pathlib import Path

import pytest

from runner.workflow.executor import (
    ExecutionContext,
    execute_step,
    execute_workflow,
)
from runner.workflow.parser import (
    Step,
    discover_workflows,
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


class TestParser:
    def test_parse_simple_workflow(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Test
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - name: Run tests
                    run: pytest
        """)

        wf = parse_workflow(path)
        assert wf.name == "Test"
        assert "test" in wf.jobs
        assert len(wf.jobs["test"].steps) == 2
        assert wf.jobs["test"].steps[0].uses == "actions/checkout@v4"
        assert wf.jobs["test"].steps[1].run == "pytest"

    def test_parse_matrix_strategy(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Matrix
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                strategy:
                  matrix:
                    python-version: ["3.11", "3.12", "3.13"]
                steps:
                  - name: Install
                    run: echo ${{ matrix.python-version }}
        """)

        wf = parse_workflow(path)
        job = wf.jobs["test"]
        assert job.strategy is not None
        combos = job.strategy.expand()
        assert len(combos) == 3

    def test_parse_job_dependencies(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Deploy
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo build
              test:
                needs: build
                runs-on: ubuntu-latest
                steps:
                  - run: echo test
              deploy:
                needs: [build, test]
                runs-on: ubuntu-latest
                steps:
                  - run: echo deploy
        """)

        wf = parse_workflow(path)
        assert wf.jobs["test"].needs == ["build"]
        assert wf.jobs["deploy"].needs == ["build", "test"]

        order = wf.job_order()
        assert order.index("build") < order.index("test")
        assert order.index("build") < order.index("deploy")
        assert order.index("test") < order.index("deploy")

    def test_parse_working_directory(self, tmp_workflow):
        path = tmp_workflow("""\
            name: UI
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - name: Install
                    working-directory: frontend
                    run: npm install
        """)

        wf = parse_workflow(path)
        step = wf.jobs["build"].steps[0]
        assert step.working_directory == "frontend"

    def test_parse_env(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Env
            on: push
            env:
              GLOBAL: "yes"
            jobs:
              test:
                env:
                  JOB_LEVEL: "true"
                runs-on: ubuntu-latest
                steps:
                  - name: Check
                    env:
                      STEP_LEVEL: "1"
                    run: echo done
        """)

        wf = parse_workflow(path)
        assert wf.env == {"GLOBAL": "yes"}
        assert wf.jobs["test"].env == {"JOB_LEVEL": "true"}
        assert wf.jobs["test"].steps[0].env == {"STEP_LEVEL": "1"}

    def test_discover_workflows(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)

        (wf_dir / "test.yml").write_text(textwrap.dedent("""\
            name: Test
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo test
        """))

        (wf_dir / "build.yml").write_text(textwrap.dedent("""\
            name: Build
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo build
        """))

        workflows = discover_workflows(tmp_path)
        assert "Test" in workflows
        assert "Build" in workflows


class TestExecutor:
    def test_execute_simple_run_step(self, tmp_path):
        step = Step(name="Echo", run="echo hello")
        ctx = ExecutionContext(root=tmp_path, verbose=False)
        result = execute_step(step, ctx)
        assert result.success
        assert "hello" in result.stdout

    def test_execute_step_with_env(self, tmp_path):
        step = Step(name="Env", run="echo $MY_VAR", env={"MY_VAR": "world"})
        ctx = ExecutionContext(root=tmp_path)
        result = execute_step(step, ctx)
        assert result.success
        assert "world" in result.stdout

    def test_execute_step_failure(self, tmp_path):
        step = Step(name="Fail", run="exit 1")
        ctx = ExecutionContext(root=tmp_path)
        result = execute_step(step, ctx)
        assert not result.success
        assert result.returncode == 1

    def test_execute_step_working_directory(self, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        step = Step(name="Pwd", run="pwd", working_directory="subdir")
        ctx = ExecutionContext(root=tmp_path)
        result = execute_step(step, ctx)
        assert result.success
        assert "subdir" in result.stdout

    def test_dry_run(self, tmp_path):
        step = Step(name="Dangerous", run="rm -rf /")
        ctx = ExecutionContext(root=tmp_path, dry_run=True)
        result = execute_step(step, ctx)
        assert result.skipped

    def test_matrix_expression_resolution(self, tmp_path):
        step = Step(name="Matrix", run="echo ${{ matrix.version }}")
        ctx = ExecutionContext(root=tmp_path, matrix={"version": "3.12"})
        result = execute_step(step, ctx)
        assert result.success
        assert "3.12" in result.stdout

    def test_checkout_action_is_noop(self, tmp_path):
        step = Step(uses="actions/checkout@v4")
        ctx = ExecutionContext(root=tmp_path, verbose=True)
        result = execute_step(step, ctx)
        assert result.success
        assert result.skipped

    def test_execute_full_workflow(self, tmp_workflow, tmp_path):
        path = tmp_workflow("""\
            name: Simple
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - name: Hello
                    run: echo "it works"
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root)
        assert success

    def test_execute_workflow_with_matrix(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Matrix
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                strategy:
                  matrix:
                    val: ["a", "b"]
                steps:
                  - name: Print
                    run: echo ${{ matrix.val }}
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root, verbose=True)
        assert success

    def test_continue_on_error(self, tmp_workflow):
        path = tmp_workflow("""\
            name: ContinueOnError
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Failing step
                    run: exit 1
                    continue-on-error: true
                  - name: Should still run
                    run: echo "I ran"
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root)
        assert success

    def test_timeout(self, tmp_path):
        step = Step(name="Slow", run="sleep 10", timeout_minutes=0.01)
        ctx = ExecutionContext(root=tmp_path)
        result = execute_step(step, ctx)
        assert not result.success
        assert result.timed_out

    def test_if_condition_skips_step(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Conditional
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Always runs
                    run: echo "yes"
                  - name: Skipped
                    if: failure()
                    run: echo "should not run"
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root, verbose=True)
        assert success

    def test_step_outputs(self, tmp_workflow):
        path = tmp_workflow("""\
            name: Outputs
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Set output
                    id: setter
                    run: echo "value=hello" >> $GITHUB_OUTPUT
                  - name: Use output
                    run: echo "${{ steps.setter.outputs.value }}"
        """)

        wf = parse_workflow(path)
        root = path.parent.parent.parent
        success = execute_workflow(wf, root=root, verbose=True)
        assert success
