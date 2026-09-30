"""What steers the agents, as hashes, so `parallax stats` can say when the evals are older than it.

The playbook's rule: rerun the evals whenever the model, a prompt, CLAUDE.md or REVIEW.md
changes. Each eval run records this fingerprint; stats compares it with today's.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

FILES = ("CLAUDE.md", "REVIEW.md", "parallax.policy.toml")


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def prompts() -> dict[str, str]:
    """Every text an agent is given besides the task's own files: label -> the text."""
    from . import build, check, lifecycle, review
    from .agents import claude
    out = {f"parallax/agents/claude.py ({name})": getattr(claude, name)
           for name in ("DRAFT_PROMPT", "MAKER_PROMPT", "BLIND_PROMPT")}
    out["parallax/agents/claude.py (BLIND_SCHEMA)"] = json.dumps(claude.BLIND_SCHEMA, sort_keys=True)
    out["parallax/lifecycle.py (SHAPES)"] = json.dumps(lifecycle.SHAPES, sort_keys=True)
    out["parallax/lifecycle.py (the drafting request)"] = inspect.getsource(lifecycle._material)
    out["parallax/build.py (MAKER_GOAL)"] = build.MAKER_GOAL
    out["parallax/check.py (REWORK)"] = check.REWORK
    out["parallax/review.py (the checker's brief)"] = inspect.getsource(review.brief)
    return out


def models(policy) -> dict[str, str]:
    """The model of each agent an eval runs. Field is off in evals, so it isn't here."""
    from .agents.claude import DEFAULT_MODEL
    return {"Focus's model": policy.draft["model"], "Maker's model": DEFAULT_MODEL,
            "Second Eye's model": policy.check["model"]}


def current(root: Path, policy) -> dict[str, str]:
    """label -> sha256 (files, prompts) or the model name. A missing file is "missing"."""
    out = {name: (_sha((Path(root) / name).read_bytes()) if (Path(root) / name).is_file() else "missing")
           for name in FILES}
    out.update({label: _sha(text) for label, text in prompts().items()})
    out.update(models(policy))
    return out


def changed(recorded: dict[str, str], now: dict[str, str]) -> list[str]:
    """What differs since the fingerprint was recorded, in the fingerprint's order."""
    return [label for label, value in now.items() if recorded.get(label) != value]
