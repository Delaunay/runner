"""Action execution policy.

Controls whether `uses:` actions are allowed to run locally.
Actions can be dangerous (arbitrary code execution from third-party repos),
so users must explicitly allow them.

Policy options per action:
  - allow:  execute normally
  - forbid: stop execution with an error
  - skip:   skip the action and continue
  - ask:    prompt the user, remember the decision

Policies are stored in .workspace/.action-policy (one per line):
  actions/checkout@v4 = allow
  owner/dangerous-action@main = forbid
  * = skip

The special key `*` sets the default policy for unlisted actions.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path


class ActionPolicy(Enum):
    ALLOW = "allow"
    FORBID = "forbid"
    SKIP = "skip"
    ASK = "ask"


class PolicyStore:
    """Manages action execution policies."""

    def __init__(self, workspace_root: Path):
        self._path = workspace_root / ".workspace" / ".action-policy"
        self._policies: dict[str, ActionPolicy] | None = None

    def _load(self) -> dict[str, ActionPolicy]:
        if self._policies is not None:
            return self._policies

        self._policies = {}
        if not self._path.exists():
            return self._policies

        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().lower()
                try:
                    self._policies[key] = ActionPolicy(value)
                except ValueError:
                    pass

        return self._policies

    def _save(self):
        """Persist current policies to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Action execution policy (managed by runner)\n"]
        policies = self._load()
        for key, policy in sorted(policies.items()):
            lines.append(f"{key} = {policy.value}\n")
        self._path.write_text("".join(lines))

    def get(self, action: str) -> ActionPolicy:
        """Get the policy for an action. Returns ASK if unset."""
        policies = self._load()

        # Exact match
        if action in policies:
            return policies[action]

        # Match without version (e.g. "actions/checkout" matches "actions/checkout@v4")
        base = action.split("@")[0] if "@" in action else action
        if base in policies:
            return policies[base]

        # Wildcard default
        if "*" in policies:
            return policies["*"]

        return ActionPolicy.ASK

    def set(self, action: str, policy: ActionPolicy):
        """Set and persist a policy for an action."""
        self._load()
        self._policies[action] = policy
        self._save()

    def check(self, action: str, *, verbose: bool = False) -> ActionPolicy:
        """Check policy and potentially prompt the user.

        Returns the resolved policy (never ASK — that gets resolved to a concrete choice).
        """
        policy = self.get(action)

        if policy != ActionPolicy.ASK:
            return policy

        return self._prompt_user(action)

    def _prompt_user(self, action: str) -> ActionPolicy:
        """Interactively ask the user what to do with an unknown action."""
        if not sys.stdin.isatty():
            print(f"  ⚠ action '{action}' has no policy (non-interactive, skipping)")
            return ActionPolicy.SKIP

        print("\n  ┌─ Action policy required ─────────────────────────")
        print(f"  │ Action: {action}")
        print("  │")
        print("  │ This action wants to run code locally.")
        print("  │ What would you like to do?")
        print("  │")
        print("  │   [a] Allow  — run this action")
        print("  │   [f] Forbid — stop execution")
        print("  │   [s] Skip   — skip and continue")
        print("  │")

        while True:
            try:
                choice = input("  └─ Choice [a/f/s]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return ActionPolicy.SKIP

            if choice in ("a", "allow"):
                policy = ActionPolicy.ALLOW
                break
            elif choice in ("f", "forbid"):
                policy = ActionPolicy.FORBID
                break
            elif choice in ("s", "skip"):
                policy = ActionPolicy.SKIP
                break
            else:
                print("  │ Please enter a, f, or s")

        # Remember the decision
        base = action.split("@")[0] if "@" in action else action
        self.set(base, policy)
        print(f"  → Remembered: {base} = {policy.value}")

        return policy
