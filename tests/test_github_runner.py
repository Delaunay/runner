"""Tests for the native GitHub Actions runner subcommand."""

from __future__ import annotations

import textwrap

from runner.github import discover_workflows, execute_workflow, parse_workflow


class TestGitHubRunner:
    """Test the runner/github/ package."""

    def test_parse_workflow(self, tmp_path):
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent("""\
            name: CI
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo hello
        """))

        wf = parse_workflow(wf_file)
        assert wf.name == "CI"
        assert "test" in wf.jobs

    def test_discover_workflows(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "test.yml").write_text(textwrap.dedent("""\
            name: Test
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo test
        """))
        (wf_dir / "build.yml").write_text(textwrap.dedent("""\
            name: Build
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo build
        """))

        workflows = discover_workflows(tmp_path)
        assert "Test" in workflows
        assert "Build" in workflows

    def test_execute_simple_workflow(self, tmp_path):
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent("""\
            name: Simple
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo "hello github"
        """))

        wf = parse_workflow(wf_file)
        result = execute_workflow(wf, root=tmp_path, ignore_runs_on=True)
        assert result is True

    def test_execute_with_skip_steps(self, tmp_path):
        marker = tmp_path / "should_not_exist"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: Skip
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Dangerous
                    run: touch {marker}
                  - name: Safe
                    run: echo ok
        """))

        wf = parse_workflow(wf_file)
        result = execute_workflow(
            wf, root=tmp_path, ignore_runs_on=True, skip_steps=["Dangerous"],
        )
        assert result is True
        assert not marker.exists()

    def test_execute_with_matrix(self, tmp_path):
        output = tmp_path / "out.txt"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: Matrix
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                strategy:
                  matrix:
                    version: ["a", "b"]
                steps:
                  - run: echo "${{{{ matrix.version }}}}" >> {output}
        """))

        wf = parse_workflow(wf_file)
        result = execute_workflow(wf, root=tmp_path, ignore_runs_on=True)
        assert result is True
        content = output.read_text().strip().split("\n")
        assert "a" in content
        assert "b" in content

    def test_execute_dry_run(self, tmp_path):
        marker = tmp_path / "should_not_exist"
        wf_file = tmp_path / "ci.yml"
        wf_file.write_text(textwrap.dedent(f"""\
            name: DryRun
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: touch {marker}
        """))

        wf = parse_workflow(wf_file)
        result = execute_workflow(wf, root=tmp_path, dry_run=True, ignore_runs_on=True)
        assert result is True
        assert not marker.exists()


class TestTopLevelAutoDetect:
    """Test that top-level run/list auto-detection works via the backends."""

    def test_detects_github(self, tmp_path):
        from runner.workflow.backends import detect_backend

        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        backend = detect_backend(tmp_path)
        assert backend.name == "github"

    def test_detects_forgejo(self, tmp_path):
        from runner.workflow.backends import detect_backend

        (tmp_path / ".forgejo" / "workflows").mkdir(parents=True)
        backend = detect_backend(tmp_path)
        assert backend.name == "forgejo"

    def test_detects_gitlab(self, tmp_path):
        from runner.workflow.backends import detect_backend

        (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]")
        backend = detect_backend(tmp_path)
        assert backend.name == "gitlab"
