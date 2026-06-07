"""GitHub Actions expression evaluator.

Handles ${{ ... }} expressions including:
- Context access: matrix.*, env.*, steps.*.outputs.*, github.*, secrets.*
- Functions: success(), failure(), always(), cancelled(),
             contains(), startsWith(), endsWith(), format(), join(), hashFiles()
- Condition evaluation for `if:` fields
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def evaluate_expression(expr: str, context: ExpressionContext) -> str:
    """Evaluate a ${{ ... }} expression and return its string value."""

    def replacer(m):
        inner = m.group(1).strip()
        result = _eval_inner(inner, context)
        return str(result)

    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", replacer, str(expr))


def evaluate_condition(condition: str | bool | None, context: ExpressionContext) -> bool:
    """Evaluate an `if:` condition. Returns True if the step should run."""
    if condition is None:
        return context.job_status == "success"

    if isinstance(condition, bool):
        return condition

    condition = condition.strip()

    # GitHub implicitly wraps conditions in ${{ }} if not already wrapped
    if not condition.startswith("${{"):
        condition = f"${{{{ {condition} }}}}"

    result = evaluate_expression(condition, context)
    return _truthy(result)


class ExpressionContext:
    """All available context for expression evaluation."""

    def __init__(
        self,
        *,
        matrix: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        steps: dict[str, StepContext] | None = None,
        github: dict[str, str] | None = None,
        secrets: dict[str, str] | None = None,
        job_status: str = "success",
        runner: dict[str, str] | None = None,
        needs: dict[str, Any] | None = None,
        inputs: dict[str, str] | None = None,
    ):
        self.matrix = matrix or {}
        self.env = env or {}
        self.steps = steps or {}
        self.github = github or {}
        self.secrets = secrets or {}
        self.job_status = job_status
        self.runner = runner or {}
        self.needs = needs or {}
        self.inputs = inputs or {}


class StepContext:
    """Context for a completed step."""

    def __init__(self, outputs: dict[str, str] | None = None, outcome: str = "success"):
        self.outputs = outputs or {}
        self.outcome = outcome


def _truthy(value: str) -> bool:
    """GitHub Actions truthiness rules."""
    if isinstance(value, bool):
        return value
    v = str(value).lower().strip()
    return v not in ("", "0", "false", "null", "none")


def _eval_inner(expr: str, ctx: ExpressionContext) -> Any:
    """Evaluate a single inner expression (without ${{ }})."""
    expr = expr.strip()

    # Boolean operators (lowest precedence, left to right)
    # Split on || first (lower precedence than &&)
    or_parts = _split_operator(expr, "||")
    if or_parts:
        return any(_truthy(_eval_inner(p, ctx)) for p in or_parts)

    and_parts = _split_operator(expr, "&&")
    if and_parts:
        return all(_truthy(_eval_inner(p, ctx)) for p in and_parts)

    # Negation
    if expr.startswith("!"):
        return not _truthy(_eval_inner(expr[1:], ctx))

    # Comparison operators
    for op in ("!=", "=="):
        comp_parts = _split_comparison(expr, op)
        if comp_parts:
            left = _eval_inner(comp_parts[0], ctx)
            right = _eval_inner(comp_parts[1], ctx)
            if op == "==":
                return str(left).lower() == str(right).lower()
            else:
                return str(left).lower() != str(right).lower()

    # Parenthesized expression
    if expr.startswith("(") and expr.endswith(")"):
        return _eval_inner(expr[1:-1], ctx)

    # String literal
    if (expr.startswith("'") and expr.endswith("'")) or (expr.startswith('"') and expr.endswith('"')):
        return expr[1:-1]

    # Boolean literals
    if expr.lower() == "true":
        return True
    if expr.lower() == "false":
        return False

    # Numeric literal
    try:
        if "." in expr:
            return float(expr)
        return int(expr)
    except ValueError:
        pass

    # Function calls
    func_match = re.match(r"(\w+)\((.*)\)$", expr, re.DOTALL)
    if func_match:
        fname = func_match.group(1)
        args_str = func_match.group(2).strip()
        return _call_function(fname, args_str, ctx)

    # Dot access / context reference
    return _resolve_context(expr, ctx)


def _split_operator(expr: str, op: str) -> list[str] | None:
    """Split expression by a binary operator, respecting parens and quotes."""
    depth = 0
    in_quote = None
    i = 0
    parts = []
    last = 0

    while i < len(expr):
        ch = expr[i]
        if ch in ("'", '"') and in_quote is None:
            in_quote = ch
        elif ch == in_quote:
            in_quote = None
        elif in_quote is None:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and expr[i:i + len(op)] == op:
                parts.append(expr[last:i].strip())
                i += len(op)
                last = i
                continue
        i += 1

    if parts:
        parts.append(expr[last:].strip())
        return parts
    return None


def _split_comparison(expr: str, op: str) -> list[str] | None:
    """Split on a comparison operator (first occurrence only)."""
    depth = 0
    in_quote = None
    i = 0

    while i < len(expr):
        ch = expr[i]
        if ch in ("'", '"') and in_quote is None:
            in_quote = ch
        elif ch == in_quote:
            in_quote = None
        elif in_quote is None:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and expr[i:i + len(op)] == op:
                # Make sure != doesn't match the ! in a != that's part of !==
                left = expr[:i].strip()
                right = expr[i + len(op):].strip()
                if left and right:
                    return [left, right]
        i += 1
    return None


def _resolve_context(expr: str, ctx: ExpressionContext) -> Any:
    """Resolve a dotted context reference like matrix.python-version."""
    parts = expr.split(".")

    if not parts:
        return expr

    root = parts[0]
    rest = ".".join(parts[1:])

    if root == "matrix" and len(parts) >= 2:
        return ctx.matrix.get(rest, "")

    if root == "env" and len(parts) >= 2:
        return ctx.env.get(rest, os.environ.get(rest, ""))

    if root == "secrets" and len(parts) >= 2:
        return ctx.secrets.get(rest, "")

    if root in ("github", "forgejo", "forge", "gitea") and len(parts) >= 2:
        # forgejo.* / forge.* / gitea.* are aliases for github.*
        if rest.startswith("event.inputs.") and len(parts) >= 4:
            input_key = ".".join(parts[3:])
            return ctx.inputs.get(input_key, "")
        return ctx.github.get(rest, "")

    if root == "inputs" and len(parts) >= 2:
        return ctx.inputs.get(rest, "")

    if root == "runner" and len(parts) >= 2:
        return ctx.runner.get(rest, "")

    if root == "steps" and len(parts) >= 3:
        step_id = parts[1]
        step_ctx = ctx.steps.get(step_id)
        if step_ctx is None:
            return ""
        if parts[2] == "outputs" and len(parts) >= 4:
            output_key = ".".join(parts[3:])
            return step_ctx.outputs.get(output_key, "")
        if parts[2] == "outcome":
            return step_ctx.outcome
        return ""

    if root == "needs" and len(parts) >= 3:
        job_id = parts[1]
        job_ctx = ctx.needs.get(job_id)
        if job_ctx is None:
            return ""
        if isinstance(job_ctx, dict):
            if parts[2] == "outputs" and len(parts) >= 4:
                output_key = ".".join(parts[3:])
                return job_ctx.get("outputs", {}).get(output_key, "")
            if parts[2] == "result":
                return job_ctx.get("result", "")
        return ""

    return expr


def _call_function(name: str, args_str: str, ctx: ExpressionContext) -> Any:
    """Evaluate a function call."""
    args = _parse_args(args_str) if args_str else []

    # Status functions
    if name == "success":
        return ctx.job_status == "success"
    if name == "failure":
        return ctx.job_status == "failure"
    if name == "always":
        return True
    if name == "cancelled":
        return ctx.job_status == "cancelled"

    # String functions
    if name == "contains" and len(args) >= 2:
        haystack = str(_eval_arg(args[0], ctx)).lower()
        needle = str(_eval_arg(args[1], ctx)).lower()
        return needle in haystack

    if name == "startsWith" and len(args) >= 2:
        string = str(_eval_arg(args[0], ctx))
        prefix = str(_eval_arg(args[1], ctx))
        return string.lower().startswith(prefix.lower())

    if name == "endsWith" and len(args) >= 2:
        string = str(_eval_arg(args[0], ctx))
        suffix = str(_eval_arg(args[1], ctx))
        return string.lower().endswith(suffix.lower())

    if name == "format" and len(args) >= 1:
        template = str(_eval_arg(args[0], ctx))
        for i, arg in enumerate(args[1:]):
            template = template.replace(f"{{{i}}}", str(_eval_arg(arg, ctx)))
        return template

    if name == "join" and len(args) >= 1:
        arr = _eval_arg(args[0], ctx)
        sep = str(_eval_arg(args[1], ctx)) if len(args) >= 2 else ","
        if isinstance(arr, list):
            return sep.join(str(x) for x in arr)
        return str(arr)

    if name == "hashFiles" and len(args) >= 1:
        return _hash_files(args, ctx)

    if name == "toJSON" and len(args) >= 1:
        import json
        val = _eval_arg(args[0], ctx)
        return json.dumps(val)

    if name == "fromJSON" and len(args) >= 1:
        import json
        try:
            return json.loads(str(_eval_arg(args[0], ctx)))
        except (json.JSONDecodeError, TypeError):
            return ""

    return ""


def _parse_args(args_str: str) -> list[str]:
    """Parse comma-separated function arguments respecting quotes."""
    args = []
    current = []
    depth = 0
    in_quote = None

    for ch in args_str:
        if ch in ("'", '"') and in_quote is None:
            in_quote = ch
            current.append(ch)
        elif ch == in_quote:
            in_quote = None
            current.append(ch)
        elif ch == "(" and in_quote is None:
            depth += 1
            current.append(ch)
        elif ch == ")" and in_quote is None:
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0 and in_quote is None:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

    if current:
        args.append("".join(current).strip())

    return args


def _eval_arg(arg: str, ctx: ExpressionContext) -> Any:
    """Evaluate a single function argument."""
    arg = arg.strip()

    # String literal
    if (arg.startswith("'") and arg.endswith("'")) or (arg.startswith('"') and arg.endswith('"')):
        return arg[1:-1]

    # Boolean
    if arg.lower() == "true":
        return True
    if arg.lower() == "false":
        return False

    # Number
    try:
        if "." in arg:
            return float(arg)
        return int(arg)
    except ValueError:
        pass

    # Context reference
    return _resolve_context(arg, ctx)


def _hash_files(args: list[str], ctx: ExpressionContext) -> str:
    """Hash files matching glob patterns."""
    import glob as glob_mod

    workspace = ctx.github.get("workspace", os.getcwd())
    h = hashlib.sha256()

    for arg in args:
        pattern = str(_eval_arg(arg, ctx))
        matches = sorted(glob_mod.glob(os.path.join(workspace, pattern), recursive=True))
        for match in matches:
            if os.path.isfile(match):
                with open(match, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        h.update(chunk)

    return h.hexdigest()


def build_github_context(root: Path) -> dict[str, str]:
    """Build the github.* context from the local git repo."""
    ctx = {
        "workspace": str(root),
        "event_name": "local",
        "actor": _git_config(root, "user.name") or os.environ.get("USER", "local"),
        "repository": _git_remote_repo(root),
    }

    sha = _git_output(root, ["git", "rev-parse", "HEAD"])
    if sha:
        ctx["sha"] = sha

    ref = _git_output(root, ["git", "symbolic-ref", "HEAD"])
    if ref:
        ctx["ref"] = ref
        ctx["ref_name"] = ref.replace("refs/heads/", "")
    else:
        ctx["ref"] = "refs/heads/unknown"
        ctx["ref_name"] = "unknown"

    return ctx


def _git_output(root: Path, cmd: list[str]) -> str:
    """Run a git command and return stripped output."""
    try:
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return ""


def _git_config(root: Path, key: str) -> str:
    return _git_output(root, ["git", "config", key])


def _git_remote_repo(root: Path) -> str:
    """Extract owner/repo from git remote."""
    url = _git_output(root, ["git", "remote", "get-url", "origin"])
    if not url:
        return ""
    # Handle SSH and HTTPS URLs
    url = url.rstrip(".git")
    if ":" in url and "@" in url:
        # git@github.com:owner/repo
        return url.split(":")[-1]
    # https://github.com/owner/repo
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return ""


def load_secrets(root: Path) -> dict[str, str]:
    """Load secrets from .workspace/.secrets in the project root."""
    secrets_file = root / ".workspace" / ".secrets"
    if not secrets_file.exists():
        return {}

    secrets = {}
    for line in secrets_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            secrets[key] = value

    return secrets
