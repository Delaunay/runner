"""Tests for the actions module."""

import textwrap

from runner.workflow.actions import (
    _parse_manifest,
    build_input_env,
    execute_action,
    resolve_action,
)


class TestParseManifest:
    def test_composite_action(self, tmp_path):
        action_yml = tmp_path / "action.yml"
        action_yml.write_text(textwrap.dedent("""\
            name: My Composite
            description: Does stuff
            inputs:
              name:
                description: Your name
                required: true
                default: world
            runs:
              using: composite
              steps:
                - name: Greet
                  run: echo "Hello ${{ inputs.name }}"
                  shell: bash
        """))

        manifest = _parse_manifest(action_yml)
        assert manifest.name == "My Composite"
        assert "name" in manifest.inputs
        assert manifest.inputs["name"].default == "world"
        assert manifest.runs.using == "composite"
        assert len(manifest.runs.steps) == 1

    def test_node_action(self, tmp_path):
        action_yml = tmp_path / "action.yml"
        action_yml.write_text(textwrap.dedent("""\
            name: Node Action
            runs:
              using: node20
              main: dist/index.js
        """))

        manifest = _parse_manifest(action_yml)
        assert manifest.runs.using == "node20"
        assert manifest.runs.main == "dist/index.js"

    def test_docker_action(self, tmp_path):
        action_yml = tmp_path / "action.yml"
        action_yml.write_text(textwrap.dedent("""\
            name: Docker Action
            runs:
              using: docker
              image: Dockerfile
              args:
                - ${{ inputs.who }}
        """))

        manifest = _parse_manifest(action_yml)
        assert manifest.runs.using == "docker"
        assert manifest.runs.image == "Dockerfile"
        assert len(manifest.runs.args) == 1


class TestResolveAction:
    def test_local_action(self, tmp_path):
        action_dir = tmp_path / ".github" / "actions" / "greet"
        action_dir.mkdir(parents=True)
        (action_dir / "action.yml").write_text(textwrap.dedent("""\
            name: Greet
            runs:
              using: composite
              steps:
                - run: echo hi
                  shell: bash
        """))

        resolved = resolve_action(
            "./.github/actions/greet",
            root=tmp_path,
            cache_dir=tmp_path / ".cache",
        )
        assert resolved is not None
        assert resolved.manifest.name == "Greet"
        assert resolved.path == action_dir

    def test_local_action_not_found(self, tmp_path):
        resolved = resolve_action(
            "./nonexistent",
            root=tmp_path,
            cache_dir=tmp_path / ".cache",
        )
        assert resolved is None


class TestBuildInputEnv:
    def test_builds_input_env(self, tmp_path):
        action_yml = tmp_path / "action.yml"
        action_yml.write_text(textwrap.dedent("""\
            name: Test
            inputs:
              user-name:
                default: anon
              count:
                default: "5"
            runs:
              using: composite
              steps: []
        """))

        manifest = _parse_manifest(action_yml)
        env = build_input_env(manifest, {"user-name": "alice"})
        assert env["INPUT_USER-NAME"] == "alice"
        assert env["INPUT_COUNT"] == "5"


class TestBuildInputEnvHyphens:
    """Verify INPUT_* preserves hyphens (GitHub Actions convention)."""

    def test_hyphen_preserved(self, tmp_path):
        action_yml = tmp_path / "action.yml"
        action_yml.write_text(textwrap.dedent("""\
            name: Test
            inputs:
              java-version:
                default: "17"
              node-version:
                default: ""
            runs:
              using: composite
              steps: []
        """))

        manifest = _parse_manifest(action_yml)
        env = build_input_env(manifest, {"java-version": "21", "node-version": "20"})
        assert env["INPUT_JAVA-VERSION"] == "21"
        assert env["INPUT_NODE-VERSION"] == "20"

    def test_space_replaced_with_underscore(self, tmp_path):
        action_yml = tmp_path / "action.yml"
        action_yml.write_text(textwrap.dedent("""\
            name: Test
            inputs:
              my input:
                default: val
            runs:
              using: composite
              steps: []
        """))

        manifest = _parse_manifest(action_yml)
        env = build_input_env(manifest, {"my input": "hello"})
        assert env["INPUT_MY_INPUT"] == "hello"

    def test_undeclared_inputs_passed_through(self, tmp_path):
        action_yml = tmp_path / "action.yml"
        action_yml.write_text(textwrap.dedent("""\
            name: Test
            inputs:
              declared:
                default: ""
            runs:
              using: composite
              steps: []
        """))

        manifest = _parse_manifest(action_yml)
        env = build_input_env(manifest, {"declared": "a", "extra-param": "b"})
        assert env["INPUT_DECLARED"] == "a"
        assert env["INPUT_EXTRA-PARAM"] == "b"


class TestNodeActionEnvExport:
    """Test that JS actions can export env vars and PATH additions."""

    def test_node_action_exports_env(self, tmp_path):
        """A JS action that writes to GITHUB_ENV should export vars."""
        action_dir = tmp_path / "setup-tool"
        action_dir.mkdir()
        (action_dir / "action.yml").write_text(textwrap.dedent("""\
            name: Setup Tool
            inputs:
              version:
                default: "1.0"
            runs:
              using: node20
              main: index.js
        """))
        # The action writes TOOL_HOME to GITHUB_ENV and tool/bin to GITHUB_PATH
        (action_dir / "index.js").write_text(textwrap.dedent("""\
            const fs = require('fs');
            const path = require('path');

            const toolHome = '/opt/tool-1.0';
            const envFile = process.env.GITHUB_ENV;
            const pathFile = process.env.GITHUB_PATH;

            fs.appendFileSync(envFile, `TOOL_HOME=${toolHome}\\n`);
            fs.appendFileSync(pathFile, `${toolHome}/bin\\n`);

            // Also write an output
            const outputFile = process.env.GITHUB_OUTPUT;
            fs.appendFileSync(outputFile, `installed-version=1.0\\n`);
        """))

        resolved = resolve_action(
            "./setup-tool", root=tmp_path, cache_dir=tmp_path / ".cache",
        )
        assert resolved is not None

        result = execute_action(
            resolved, with_={"version": "1.0"}, env={}, root=tmp_path,
        )
        assert result.success
        assert result.exported_env["TOOL_HOME"] == "/opt/tool-1.0"
        assert "/opt/tool-1.0/bin" in result.exported_path
        assert result.outputs["installed-version"] == "1.0"

    def test_node_action_multiline_env(self, tmp_path):
        """Test multiline GITHUB_ENV format (key<<DELIMITER)."""
        action_dir = tmp_path / "multiline-action"
        action_dir.mkdir()
        (action_dir / "action.yml").write_text(textwrap.dedent("""\
            name: Multiline
            runs:
              using: node20
              main: index.js
        """))
        # Use actual newlines via template literal
        (action_dir / "index.js").write_text(
            "const fs = require('fs');\n"
            "const envFile = process.env.GITHUB_ENV;\n"
            "fs.appendFileSync(envFile, 'CERT<<EOF\\nline1\\nline2\\nEOF\\n');\n"
        )

        resolved = resolve_action(
            "./multiline-action", root=tmp_path, cache_dir=tmp_path / ".cache",
        )

        result = execute_action(
            resolved, with_={}, env={}, root=tmp_path,
        )
        assert result.success
        assert result.exported_env["CERT"] == "line1\nline2"


class TestActionEnvPropagation:
    """Integration test: exported env/path from an action is visible in later steps."""

    def test_exported_env_visible_in_next_step(self, tmp_path):
        from runner.workflow.executor import execute_workflow
        from runner.workflow.parser import parse_workflow

        # Create a fake action that exports MY_TOOL_HOME
        action_dir = tmp_path / ".github" / "actions" / "setup-tool"
        action_dir.mkdir(parents=True)
        (action_dir / "action.yml").write_text(textwrap.dedent("""\
            name: Setup
            runs:
              using: node20
              main: index.js
        """))
        (action_dir / "index.js").write_text(textwrap.dedent("""\
            const fs = require('fs');
            fs.appendFileSync(process.env.GITHUB_ENV, 'MY_TOOL_HOME=/opt/mytool\\n');
        """))

        # Pre-allow all actions via policy (non-interactive test)
        policy_dir = tmp_path / ".workspace"
        policy_dir.mkdir(parents=True)
        (policy_dir / ".action-policy").write_text("* = allow\n")

        # Create a workflow that uses the action, then reads the env var
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        output_file = tmp_path / "result.txt"
        (wf_dir / "test.yml").write_text(textwrap.dedent(f"""\
            name: Test
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: ./.github/actions/setup-tool
                  - run: echo "$MY_TOOL_HOME" > {output_file}
        """))

        wf = parse_workflow(wf_dir / "test.yml")
        success = execute_workflow(wf, root=tmp_path, ignore_runs_on=True)
        assert success
        assert output_file.read_text().strip() == "/opt/mytool"


class TestExecuteComposite:
    def test_composite_returns_steps(self, tmp_path):
        action_dir = tmp_path / "my-action"
        action_dir.mkdir()
        (action_dir / "action.yml").write_text(textwrap.dedent("""\
            name: Multi Step
            runs:
              using: composite
              steps:
                - name: Step 1
                  run: echo one
                  shell: bash
                - name: Step 2
                  run: echo two
                  shell: bash
        """))

        resolved = resolve_action(
            "./my-action",
            root=tmp_path,
            cache_dir=tmp_path / ".cache",
        )
        assert resolved is not None

        result = execute_action(
            resolved,
            with_={},
            env={},
            root=tmp_path,
        )
        assert result.success
        assert result.composite_steps is not None
        assert len(result.composite_steps) == 2
