"""Tests for workspace management."""



from runner.workflow.workspace import Workspace, _parse_env_file


class TestWorkspace:
    def test_setup_creates_directories(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()

        assert (tmp_path / ".workspace").is_dir()
        assert (tmp_path / ".workspace" / "jobs").is_dir()
        assert (tmp_path / ".workspace" / "artifacts").is_dir()
        assert (tmp_path / ".workspace" / "cache").is_dir()

    def test_create_job_dir(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()

        job_ws = ws.create_job_dir("test", "3.12")
        assert job_ws.path == tmp_path / ".workspace" / "jobs" / "test-3.12"

    def test_artifact_dir(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()

        d = ws.artifact_dir("dist")
        assert d.is_dir()
        assert d == tmp_path / ".workspace" / "artifacts" / "dist"

    def test_clean(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        assert (tmp_path / ".workspace").exists()

        ws.clean()
        assert not (tmp_path / ".workspace").exists()


class TestJobWorkspace:
    def test_setup_creates_env_files(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        job_ws = ws.create_job_dir("test")
        job_ws.setup(clone=False)

        assert job_ws.env_file.exists()
        assert job_ws.output_file.exists()
        assert job_ws.path_file.exists()

    def test_github_env_vars(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        job_ws = ws.create_job_dir("test")
        job_ws.setup(clone=False)

        env_vars = job_ws.get_github_env_vars()
        assert "GITHUB_ENV" in env_vars
        assert "GITHUB_OUTPUT" in env_vars
        assert "GITHUB_PATH" in env_vars
        assert "GITHUB_WORKSPACE" in env_vars

    def test_read_env_file_simple(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        job_ws = ws.create_job_dir("test")
        job_ws.setup(clone=False)

        job_ws.env_file.write_text("FOO=bar\nBAZ=qux\n")
        result = job_ws.read_env_file()
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_read_env_file_multiline(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        job_ws = ws.create_job_dir("test")
        job_ws.setup(clone=False)

        job_ws.env_file.write_text("CERT<<EOF\nline1\nline2\nEOF\n")
        result = job_ws.read_env_file()
        assert result == {"CERT": "line1\nline2"}

    def test_read_outputs(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        job_ws = ws.create_job_dir("test")
        job_ws.setup(clone=False)

        job_ws.output_file.write_text("result=pass\ncoverage=95\n")
        outputs = job_ws.read_outputs()
        assert outputs == {"result": "pass", "coverage": "95"}

    def test_path_additions(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        job_ws = ws.create_job_dir("test")
        job_ws.setup(clone=False)

        job_ws.path_file.write_text("/usr/local/bin\n/opt/tools/bin\n")
        paths = job_ws.read_path_additions()
        assert paths == ["/usr/local/bin", "/opt/tools/bin"]

    def test_reset_step_files(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.setup()
        job_ws = ws.create_job_dir("test")
        job_ws.setup(clone=False)

        job_ws.output_file.write_text("key=value\n")
        assert job_ws.read_outputs() == {"key": "value"}

        job_ws.reset_step_files()
        assert job_ws.read_outputs() == {}


class TestParseEnvFile:
    def test_simple_kv(self, tmp_path):
        f = tmp_path / "env"
        f.write_text("A=1\nB=hello world\n")
        assert _parse_env_file(f) == {"A": "1", "B": "hello world"}

    def test_multiline_heredoc(self, tmp_path):
        f = tmp_path / "env"
        f.write_text("JSON<<DELIM\n{\"key\": \"value\"}\nDELIM\n")
        assert _parse_env_file(f) == {"JSON": '{"key": "value"}'}

    def test_empty_file(self, tmp_path):
        f = tmp_path / "env"
        f.write_text("")
        assert _parse_env_file(f) == {}
