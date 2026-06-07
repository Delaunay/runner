# runner

> Execute GitHub Actions workflows locally — like `make` for your CI.

## Project Structure

```
runner/              ← Project root (git repo)
├── pyproject.toml                           ← Package config (setuptools)
├── Makefile                                 ← Dev commands
├── tests/                                   ← pytest test suite
└── runner/           ← Python package
    ├── __init__.py                          ← Namespace package (extend_path)
    ├── cli/                                 ← CLI entry point (argklass)
    │   ├── __init__.py                      ← Command discovery + main()
    │   └── run.py                           ← `runner run` — workflow executor CLI
    ├── workflow/                             ← Core: GitHub Actions workflow engine
    │   ├── __init__.py                      ← Public API
    │   ├── parser.py                        ← YAML → Workflow/Job/Step dataclasses
    │   └── executor.py                      ← Local execution of workflow steps
    ├── plugins/                             ← Namespace package for extensions
    │   ├── __init__.py                      ← extend_path hook
    │   └── example/                         ← Example plugin
    └── data/                                ← Bundled data files (JSON, CSV)
```

## Setup

Prerequisites: Python >= 3.11, uv

```bash
uv venv --python=3.11
source .venv/bin/activate
make install
```

## Usage

```bash
# List available workflows
runner run

# Execute a workflow
runner run --workflow test

# Dry-run (show commands without executing)
runner run --workflow test --dry_run

# Verbose (stream stdout/stderr)
runner run --workflow test --verbose

# Run specific job
runner run --workflow test --job lint
```

## Architecture

### Key Patterns

- **Namespace package**: `runner` uses `pkgutil.extend_path` — third-party packages can add `runner.plugins.*` modules
- **CLI plugins**: Commands discovered from `runner.cli` + `runner.plugins` via argklass
- **Workflow engine**: Parses `.github/workflows/*.yml`, resolves expressions, executes `run:` steps via bash
- **Action handlers**: Known `uses:` actions mapped to local equivalents (checkout → noop, setup-python → version check)

## Testing

```bash
make test    # pytest
make lint    # ruff check
make format  # ruff fix + format
```
