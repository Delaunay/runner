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
        assert env["INPUT_USER_NAME"] == "alice"
        assert env["INPUT_COUNT"] == "5"


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
