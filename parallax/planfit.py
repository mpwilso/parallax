"""The plan against the intent, by code, before anything else happens.

Both mistakes that cost rejections on the first real tasks were checkable: a test file the intent
didn't allow, and a cap that didn't match the budget the intent named. So:
- every file and test the plan lists is inside the intent's scope;
- every numbered outcome in the intent is covered by the plan's `covers`, and nothing else is;
- if the intent names a budget, the plan's cap is that budget;
- the cap is above what drafting already spent, and at most the policy's cap for the task's size.
A mismatch goes back to the drafter automatically. Code decides; no model.
"""
from __future__ import annotations

from fnmatch import fnmatch

from . import lint


def in_scope(path: str, scope: list[str]) -> bool:
    path = path.split("::", 1)[0].strip("/")
    for pattern in scope:
        pattern = pattern.strip()
        if path == pattern.strip("/") or fnmatch(path, pattern):
            return True
        if pattern.endswith("/") and path.startswith(pattern):
            return True
    return False


def problems(intent: str, plan: dict, spent: float, budget_policy: dict) -> list[str]:
    out = []
    scope = lint.scope_of(intent)
    for path in [*plan["files"], *plan["tests"]]:
        if not in_scope(path, scope):
            out.append(f"the plan lists {path.split('::', 1)[0]}, which the intent's scope ({', '.join(scope)}) doesn't allow")
    outcomes = lint.outcomes_of(intent)
    covers = {str(k): v for k, v in plan.get("covers", {}).items()}
    for o in outcomes:
        if not covers.get(o):
            out.append(f"the plan doesn't cover outcome {o}; add it to covers with the tests or steps that prove it")
    for k in covers:
        if k not in outcomes:
            out.append(f"covers names outcome {k}, which the intent doesn't have")
    cap = float(plan["budget_cap_usd"])
    named = lint.budget_of(intent)
    if named is not None and abs(cap - named) > 0.005:
        out.append(f"the intent names a budget of ${named:.2f}, but the plan's cap is ${cap:.2f}; they must match")
    if cap <= spent:
        out.append(f"the cap (${cap:.2f}) isn't above what drafting already spent (${spent:.2f})")
    size = lint.intent_fields(intent).get("size", "small")
    limit = budget_policy["large_cap_usd" if size == "large" else "small_cap_usd"]
    if cap > limit:
        out.append(f"the cap (${cap:.2f}) is over the policy's ${limit:.2f} for a {size} task")
    return out
