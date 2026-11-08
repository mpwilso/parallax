"""One trust question for a whole `parallax eval` run, asked before any case starts.

Each case runs in a scratch clone with a new ledger and a policy carrying the case's own [build]
setup, so trust.py stops every case at its trust question, and nothing in an eval answers one. So
the run asks you once, at the terminal: the question lists every case's commands exactly as its
scratch policy will hold them (read back from the policy text evals.eval_policy writes), with the
run's budget. Your answer is recorded in this repo's ledger, as you: eval.trusted, signed with the
approval key over the run, when it runs out, and each case's commands hash; or eval.refused, with
your reason, and then no case runs.

A yes trusts nothing ahead of time. As each case starts, code carries your answer into that case's
scratch ledger (trust.RUN_KIND, naming your decision and carrying its signature), for exactly that
case's commands; when the case's pipeline ends, code ends it there (trust.RUN_ENDED). trust.for_run
honours it only while it's signed, not ended and not run out, so nothing stays trusted after the run.
A command not on the list (another case's, or one that changed) still stops at trust.py's question.
A model never answers: the CLI asks only at an interactive terminal (cli._answer_trust).
"""
from __future__ import annotations

import tomllib
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import approvals, evals, trust
from .core import ParallaxError, Project
from .policy import Policy

KIND = "eval.trusted"
REFUSED = "eval.refused"
ENDED = "eval.trust.ended"

# (True, "") for yes, (False, your reason) for no, None when nobody could answer
Answer = Callable[[str], "tuple[bool, str] | None"]


def case_life() -> int:
    """Seconds a case's trust may last at most: its pipeline's time limit, then an hour to score it."""
    return evals.CASE_TIMEOUT + 3600


def case_commands(policy, case: evals.Case, ceiling: float, reticle: bool | None) -> dict[str, str]:
    """The commands that would run as you in this case, read back from its scratch policy as run_case writes it."""
    return trust.commands(Policy.from_dict(tomllib.loads(evals.eval_policy(policy, case, ceiling, reticle))))


def listing(project: Project, cases: list[evals.Case], ceiling: float, reticle: bool | None) -> dict[str, dict[str, str]]:
    return {c.id: case_commands(project.policy, c, ceiling, reticle) for c in cases}


def question(listed: dict[str, dict[str, str]], budget: float, ceiling: float) -> str:
    """Every case's commands, word for word; cases with the same commands are listed together."""
    groups: dict[str, list[str]] = {}
    for cid, cmds in listed.items():
        groups.setdefault(trust.sha(cmds), []).append(cid)
    lines = [f"this eval run has {len(listed)} cases, each in a scratch clone of its project, for up to "
             f"${budget:.2f} in all and ${ceiling:.2f} a case.",
             "these commands from each case's policy run as you, outside the sandbox, on a fresh checkout of its base commit:"]
    for ids in groups.values():
        cmds = listed[ids[0]]
        lines += ["", f"  {', '.join(ids)}:", *[f"    {trust.SETTINGS[k]}: {cmds[k] or 'none'}" for k in trust.SETTINGS]]
    lines += ["", "trust them for this run only? yes: each case runs hands-free, with exactly its own commands above "
                  "trusted from when it starts until it ends. nothing is trusted before or after, and any other command "
                  "still asks. no: no case runs."]
    return "\n".join(lines)


def ask(project: Project, cases: list[evals.Case], budget: float, per_case: float | None, reticle: bool | None,
        run_id: str, answer: Answer) -> dict | None:
    """The one question, before any case starts. Returns your eval.trusted entry, or None when no case
    has a command to trust or the budget can't reach the first case (nothing to ask). Raises, with no
    case run, on a no or when nobody could answer."""
    ceiling = evals.ceiling(project, per_case)
    listed = listing(project, cases, ceiling, reticle)
    if not any(v for cmds in listed.values() for v in cmds.values()):
        return None  # nothing would run as you
    if round(ceiling * (1 + evals.MARGIN), 2) > budget:
        return None  # the run stops before its first case, and says so
    key = approvals.load_key()  # no key: fails closed, before you're asked anything
    shas = {cid: trust.sha(cmds) for cid, cmds in listed.items()}
    got = answer(question(listed, budget, ceiling))
    if got is None:
        raise ParallaxError("the trust question needs you at an interactive terminal, so no case ran")
    yes, reason = got
    if not yes:
        project.ledger.append(REFUSED, "human", reason, run=run_id, cases=shas, commands=listed)
        raise ParallaxError("you didn't trust the cases' commands, so no case ran")
    until = (datetime.now(timezone.utc) + timedelta(seconds=len(cases) * case_life())).isoformat(timespec="seconds")
    return project.ledger.append(KIND, "human", reason or f"trusted at the terminal for eval run {run_id}",
                                 run=run_id, until=until, budget_usd=budget, per_case_usd=ceiling,
                                 cases=shas, commands=listed, sig=trust.run_sig(key, run_id, until, shas))


def carry(scratch: Project, grant: dict, case_id: str) -> dict | None:
    """Your answer, into this case's scratch ledger as it starts: this case's commands only, until it
    ends. A case that wasn't on the list gets nothing, so its commands still ask."""
    d = grant["data"]
    if case_id not in d["cases"]:
        return None
    soon = datetime.now(timezone.utc) + timedelta(seconds=case_life())
    until = min(datetime.fromisoformat(d["until"]), soon).isoformat(timespec="seconds")
    return scratch.ledger.append(
        trust.RUN_KIND, "parallax", f"your trust for eval run {d['run']} (decision {grant['id']}), "
                                    "for this case's commands while it runs",
        run=d["run"], case=case_id, by=grant["id"], commands_sha=d["cases"][case_id], cases=d["cases"],
        run_until=d["until"], until=until, sig=d["sig"], **d["commands"][case_id])


def end(scratch: Project, carried: dict) -> dict:
    """The case's pipeline ended: the run's trust no longer counts in its scratch repo."""
    return scratch.ledger.append(trust.RUN_ENDED, "parallax", f"the case ended: eval run {carried['data']['run']}'s "
                                                              "trust no longer counts here", grant=carried["id"],
                                 run=carried["data"]["run"])


def finish(project: Project, grant: dict, done: list[str]) -> dict:
    """The run ended: recorded beside your answer, with the cases it reached."""
    return project.ledger.append(ENDED, "parallax", f"eval run {grant['data']['run']} ended; its trust ended "
                                                    f"with each case", decision=grant["id"], run=grant["data"]["run"],
                                 done=done)
