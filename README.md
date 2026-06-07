# pyrunner

[![PyPI](https://img.shields.io/pypi/v/pyrunner-ci.svg)](https://pypi.python.org/pypi/pyrunner-ci)
[![Python Versions](https://img.shields.io/pypi/pyversions/pyrunner-ci.svg)](https://pypi.python.org/pypi/pyrunner-ci)
[![Tests](https://github.com/Delaunay/pyrunner/actions/workflows/test.yml/badge.svg?branch=master)](https://github.com/Delaunay/pyrunner/actions/workflows/test.yml)

Execute GitHub Actions workflows locally — like `make` for your CI.

```bash
pip install pyrunner-ci
```

## Why?

- **Test CI locally** before pushing — same commands, same order, same environment.
- **No more "it works on my machine"** — use containers to match the CI runner OS.
- **Single source of truth** — your workflow file *is* your local task runner.

## Quick Start

```bash
pip install pyrunner-ci

# List available workflows
runner list

# Run the test workflow
runner run --workflow test

# Run a specific matrix combination
runner run --workflow test --matrix "python-version=3.12"

# Dry-run (see what would execute without running it)
runner run --workflow test --dry_run

# Verbose output
runner run --workflow test --verbose

# Run without containers (always local)
runner run --workflow test --no_container
```

## Features

### Workflow Parsing

Reads `.github/workflows/*.yml` and supports:
- Jobs with `needs:` dependency ordering (topological sort)
- `strategy.matrix` expansion and selection
- `env:` at workflow, job, and step levels
- `working-directory` at step, job, or workflow defaults level
- `shell:` override (bash, sh, python)
- `defaults.run.shell` and `defaults.run.working-directory` (workflow and job level)
- `services:` sidecar containers per job
- `container:` explicit container image for a job
- `timeout-minutes:` at step and job level
- `runs-on:` as string or list (uses first entry)
- `outputs:` at job level for inter-job communication

### Execution

- `run:` steps executed via bash with `set -eo pipefail`
- `if:` conditions at **step and job level** (`success()`, `failure()`, `always()`, `cancelled()`)
- `continue-on-error:` — step failure doesn't stop the job
- `timeout-minutes:` — kills hung steps
- Full expression evaluation: `${{ matrix.* }}`, `${{ env.* }}`, `${{ github.* }}`, `${{ secrets.* }}`, `${{ steps.*.outputs.* }}`, `${{ needs.*.outputs.* }}`
- Boolean operators: `&&`, `||`, `!`, `==`, `!=`
- Expression functions: `contains()`, `startsWith()`, `endsWith()`, `format()`, `join()`, `hashFiles()`, `toJSON()`, `fromJSON()`
- Job dependency outputs via `needs.<job>.outputs.<key>` and `needs.<job>.result`
- Jobs skipped automatically when a dependency fails

### Container Support (runs-on)

By default, `runs-on:` is mapped to a container for faithful CI reproduction:

| `runs-on` | Container |
|-----------|-----------|
| `ubuntu-latest` | `ubuntu:24.04` |
| `ubuntu-22.04` | `ubuntu:22.04` |
| `ubuntu-20.04` | `ubuntu:20.04` |

- **Prefers podman**, falls back to docker
- If local OS already matches (e.g. Linux + `ubuntu-latest`), runs natively
- `--no_container` to skip container execution entirely
- Job-level `container:` key for explicit image override

### Services

Jobs can declare sidecar containers that run for the duration of the job:

```yaml
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
    steps:
      - run: pg_isready -h localhost
```

Services are spun up before steps execute and torn down afterward.

### Actions (`uses:`)

Supports executing `uses:` actions locally:

| Type | How it runs |
|------|-------------|
| **Composite** | Parses `action.yml`, inlines the `steps:` |
| **JavaScript** | Runs `node <main>` with `INPUT_*` env vars |
| **Docker** | Builds Dockerfile or pulls image, mounts workspace |
| **Local** | `uses: ./path/to/action` resolved from repo |
| **Remote** | `uses: owner/repo@ref` cloned and cached |

Common actions (`actions/checkout`, `actions/setup-python`, `actions/setup-node`, `astral-sh/setup-uv`)
have built-in handlers that map to local equivalents without network access.

#### Built-in Action Handlers

| Action | Local behavior |
|--------|---------------|
| `actions/checkout` | Noop (already in repo) |
| `actions/setup-python` | Validates current Python version |
| `actions/setup-node` | Validates node is available |
| `astral-sh/setup-uv` | Validates uv is available |
| `actions/upload-artifact` | Copies files to `.workspace/artifacts/<name>/` |
| `actions/download-artifact` | Restores files from `.workspace/artifacts/<name>/` |
| `actions/cache` | Persists/restores paths using `.workspace/cache/<key>/` |

#### Action Security Policy

Third-party actions execute arbitrary code. Runner requires explicit approval:

```
# .workspace/.action-policy
actions/checkout = allow
actions/setup-python = allow
actions/setup-node = allow
astral-sh/setup-uv = allow
some/trusted-action = allow
dangerous/unknown = forbid
* = skip
```

| Policy | Behavior |
|--------|----------|
| `allow` | Execute the action normally |
| `forbid` | Stop execution with an error |
| `skip` | Skip the action and continue |
| *(unset)* | Prompt the user interactively, remember the choice |

### Reusable Workflows

Jobs that call reusable workflows are supported:

```yaml
jobs:
  deploy:
    uses: ./.github/workflows/deploy.yml
    with:
      environment: production
```

Inputs are passed as `INPUT_*` environment variables to the called workflow.

### Environment Files

Steps can communicate via GitHub-compatible environment files:

- `$GITHUB_ENV` — set env vars for subsequent steps
- `$GITHUB_OUTPUT` — set step outputs (readable via `${{ steps.id.outputs.key }}`)
- `$GITHUB_PATH` — prepend to PATH for subsequent steps
- `$GITHUB_STEP_SUMMARY` — step summary (stored but not displayed)

### Secrets

Secrets are loaded from `.workspace/.secrets`:

```
# .workspace/.secrets
API_KEY=my-secret-key
DB_PASSWORD="p@ssw0rd"
```

Then referenced in workflows as `${{ secrets.API_KEY }}`.

**Secret providers** (checked in order):

1. **Environment variables** — `RUNNER_SECRET_<KEY>` (highest priority)
2. **File** — `.workspace/.secrets`
3. **External command** — set `RUNNER_SECRET_CMD` env var to a command template
   (e.g. `RUNNER_SECRET_CMD="pass show ci/{key}"` or `RUNNER_SECRET_CMD="op read op://ci/{key}"`)

### Workspace Management

Runner creates a `.workspace/` directory for state:

```
.workspace/
├── .secrets            ← Secret values (gitignore this!)
├── .action-policy      ← Action allow/forbid/skip decisions
├── actions/            ← Cached action clones
├── jobs/               ← Per-job working directories + env files
├── artifacts/          ← Artifact storage between jobs
└── cache/              ← Cache storage
```

Manage the workspace with the `workspace` subcommand:

```bash
# Show space breakdown (sizes, file counts, per-section details)
runner workspace status

# Clean everything
runner workspace clean

# Clean only cache
runner workspace clean --target cache

# Clean only artifacts
runner workspace clean --target artifacts

# Clean only job working dirs
runner workspace clean --target jobs

# Clean a specific subpath
runner workspace clean --target cache/deps-abc123
```

Add `.workspace/` to your `.gitignore`.

## GitHub Context

The `github.*` context is populated from the local git repo:

| Expression | Source |
|------------|--------|
| `github.sha` | `git rev-parse HEAD` |
| `github.ref` | `git symbolic-ref HEAD` |
| `github.ref_name` | branch name |
| `github.workspace` | project root path |
| `github.repository` | from git remote origin |
| `github.actor` | from git config user.name |

## Development

```bash
git clone https://github.com/Delaunay/pyrunner.git
cd pyrunner
uv venv .venv && source .venv/bin/activate
make install
make test
```

### Publishing to PyPI

The project uses [trusted publishing](https://docs.pypi.org/trusted-publishers/) via GitHub Actions.
To publish a new release:

```bash
# Tag a new version
git tag v0.1.0
git push origin v0.1.0
```

The `Build & Publish` workflow will automatically build the wheel and publish to PyPI
when a `v*` tag is pushed.

For manual publishing:

```bash
pip install build twine
python -m build
twine upload dist/*
```

## License

BSD-3-Clause
