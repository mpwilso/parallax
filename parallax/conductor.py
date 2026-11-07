"""M15, the light conductor: several tasks from one request.

Focus may split a request: docs/tasks/<id>/split.md lists its parts, each a goal in plain words and a
scope. It is drafted, normalized and linted like the intent and plan, and code checks it (split.py);
a split that fails goes back to Focus with the exact problems, up to the same redraft limit, then
comes to you as one decision. One part is always allowed, and expected for small requests.

Each part is its own task, made by project.new_task and run by the same pilot as a one-part request:
its own worktree, sandbox, budget, Reticle, Maker, Second Eye and inbox item. The request's id is the
id of the task it started as, which is part 1. The ledger links them: one request.split entry names
the parts and the request, and each later part's task.created carries the request and its number.
- Up to [conductor] max_parallel parts work at once; the rest wait for one to finish.
- Parts whose scopes overlap run in turn, in the split's order: the later one is made on the earlier
  one's accepted commit, never on a tree you haven't accepted. If the earlier one ends without an
  accept, the later one comes to you as one decision: build it on the base branch as it is, or drop it.
- The launch: a part whose plan fits waits (launch.held) until no part of its request is still
  drafting. If their caps, with the caps already approved for the request, are within your launch
  limit (auto_launch_usd), code launches each one under the launch rule, signed and naming it. If
  they're over, one question goes to you, on the first of them; your launch starts them all.
Without a [conductor] table in the policy, every request is one task, as before M15.

advance() moves every request on. It runs when a pilot or a build ends, after an accept, a drop or an
answer, and with the housekeeping on every command and board request. A lock keeps two processes from
taking the same step at once.
"""
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass

from . import budgets, lifecycle, lint, planfit, split, status
from .core import Project, inside_task
from .ledger import file_lock

LOCK = "conductor.lock"
WORKING = ("drafting", "building", "checking")
ENDED = ("rejected", "closed")  # dropped, or ended at a gate: never accepted
ACCEPTED = ("accepted", "merging", "merged")


@dataclass
class Part:
    n: int
    goal: str
    scope: list[str]
    after: int | None  # the part it runs after, or None
    task: str | None   # its task, once made


@dataclass
class Request:
    id: str
    words: str  # the request as you typed it
    parts: list[Part]

    def part(self, n: int) -> Part:
        return next(p for p in self.parts if p.n == n)


def split_entry(project: Project, request_id: str) -> dict | None:
    return next((e for e in project.ledger.entries()
                 if e["kind"] == "request.split" and e["data"].get("request") == request_id), None)


def requests(project: Project) -> list[Request]:
    """Every request split into more than one part, oldest first."""
    splits, made, words = {}, {}, {}
    for e in project.ledger.entries():
        d = e["data"]
        if e["kind"] == "request.split" and d.get("request") not in splits:
            splits[d["request"]] = e
        elif e["kind"] == "task.created" and d.get("request"):
            if d["request"] == d.get("task"):
                words[d["task"]] = e["reason"]
            elif d.get("part"):
                made[(d["request"], d["part"])] = d["task"]
    out = []
    for rid, e in splits.items():
        listed = e["data"].get("parts") or []
        if len(listed) < 2:
            continue
        parts = [Part(p["n"], p["goal"], list(p.get("scope") or []), p.get("after"),
                      rid if p["n"] == 1 else made.get((rid, p["n"]))) for p in listed]
        out.append(Request(rid, words.get(rid, ""), parts))
    return out


def request_of(project: Project, request_id: str) -> Request | None:
    return next((r for r in requests(project) if r.id == request_id), None)


def part_of(project: Project, task_id: str) -> tuple[Request, Part] | None:
    """The request and part this task is, when its request was split into more than one."""
    rid = (project.tasks().get(task_id) or {}).get("request")
    req = request_of(project, rid) if rid else None
    part = next((p for p in req.parts if p.task == task_id), None) if req else None
    return (req, part) if part else None


def typed(project: Project, task_id: str) -> str:
    """The request as you typed it: for a later part, the words of the task the request started as."""
    t = project.task(task_id)
    rid = t.get("request")
    if rid and rid != task_id and rid in project.tasks():
        return project.task(rid)["goal"]
    return t["goal"]


# drafting the split ---------------------------------------------------------------------------------

def wants_split(project: Project, task_id: str) -> bool:
    """The task a request started as, with the conductor on, and no split recorded for it yet."""
    return (project.policy.conductor["max_parts"] > 1 and project.task(task_id).get("request") == task_id
            and split_entry(project, task_id) is None)


def split_rules(project: Project) -> str:
    return f"At most {project.policy.conductor['max_parts']} parts."


def material(project: Project, task_id: str) -> str:
    """For a part's intent and plan: which part it is, its scope, and what the other parts do."""
    found = part_of(project, task_id)
    if not found:
        return ""
    req, part = found
    others = "; ".join(f"part {p.n}, {p.goal}" for p in req.parts if p.n != part.n)
    return (f"This task is part {part.n} of {len(req.parts)} of that request, split by Focus and checked by code. "
            f"Draft for this part only: {part.goal}\n"
            f"Its scope, which the intent's scope and the plan's files must stay inside: {', '.join(part.scope)}\n"
            f"The other parts are tasks of their own, built, checked and accepted apart from this one: {others}")


def draft_split(project: Project, task_id: str, drafter_for) -> str:
    """Draft the split, normalize and lint it, and check it by code. Returns "fit" or "stuck"."""
    from .pilot import MAX_REDRAFTS
    problems: dict[str, list[str]] = {}
    for attempt in range(MAX_REDRAFTS + 1):
        if not lifecycle.draft(project, task_id, ["split"], drafter_for, problems):
            why = (lifecycle.state(project, task_id).failed or ("split", "no reply"))[1]
            project.ledger.append("stuck.raised", "parallax", lint.one_sentence(f"the split stopped: {why}"),
                                  task=task_id, split=True)
            return "stuck"
        text = lifecycle._read(project, task_id, "split")
        parts = lint.split_parts(text)
        found = [m for _, m in lint.lint_lifecycle(text, "split")] or split.problems(parts, project.policy.conductor["max_parts"])
        if not found:
            _record(project, task_id, parts, text)
            return "fit"
        problems = {"split": found}
        project.ledger.append("draft.misfit", "parallax", "; ".join(found), task=task_id, attempt=attempt + 1, doc="split")
    project.ledger.append("stuck.raised", "parallax", f"the split still failed after {MAX_REDRAFTS} redrafts: "
                          + "; ".join(problems["split"]), task=task_id, split=True)
    return "stuck"


def _record(project: Project, task_id: str, parts: list[dict], text: str) -> dict:
    after = split.order(parts)
    bottom = next((line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("Bottom line:")), "")
    listed = [{"n": p["n"], "goal": p["goal"], "scope": p["scope"], "after": after[p["n"]]} for p in parts]
    return project.ledger.append("request.split", "parallax", bottom or f"{len(parts)} parts", request=task_id, task=task_id,
                                 parts=listed, sha=lifecycle.file_hash(lifecycle.doc_path(project, task_id, "split")))


def one_task(project: Project, task_id: str, reason: str) -> dict:
    """Your answer to a split that didn't hold: the whole request, as one task."""
    goal = project.task(task_id)["goal"]
    return project.ledger.append("request.split", "human", reason, request=task_id, task=task_id,
                                 parts=[{"n": 1, "goal": goal, "scope": [], "after": None}])


def scope_problems(project: Project, task_id: str, plan: dict) -> list[str]:
    """A part's plan stays inside its part's scope: that is what lets parts run side by side."""
    found = part_of(project, task_id)
    if not found:
        return []
    scope = found[1].scope
    return [f"the plan lists {p.split('::', 1)[0]}, which this part's scope ({', '.join(scope)}) doesn't allow; "
            "the request's other parts do the rest" for p in [*plan["files"], *plan["tests"]] if not planfit.in_scope(p, scope)]


# the launch -----------------------------------------------------------------------------------------

def hold(project: Project, task_id: str, why: str) -> str | None:
    """A part whose plan fits: it waits for the rest of its request, then they launch together.
    Returns None for a task that isn't a part, "go" if it was just approved, "ask" if it carries the
    request's question, "wait" otherwise. why: its own launch rule, named again at the approval."""
    found = part_of(project, task_id)
    if not found:
        return None
    with file_lock(project.state / LOCK):
        project.ledger.append("launch.held", "parallax", why, task=task_id, request=found[0].id)
        out = _round(project, request_of(project, found[0].id), task_id, None)
    return out.get(task_id, "wait")


def _entries_for(project: Project, task_id: str, kind: str) -> list[tuple[int, dict]]:
    return [(i, e) for i, e in enumerate(project.ledger.entries()) if e["kind"] == kind and e["data"].get("task") == task_id]


def _last_ask(project: Project, request_id: str) -> tuple[int, dict] | None:
    asks = [(i, e) for i, e in enumerate(project.ledger.entries())
            if e["kind"] == "review.requested" and e["data"].get("request") == request_id]
    return asks[-1] if asks else None


def _yes(project: Project, ask: tuple[int, dict]) -> dict | None:
    """Your approval of the asked part, given after the question."""
    at, e = ask
    return next((x for i, x in _entries_for(project, e["data"]["task"], "gate.approved") if i > at and x["actor"] == "human"), None)


def _cap(project: Project, task_id: str) -> float:
    return float((lifecycle.plan_data(project, task_id) or {}).get("budget_cap_usd", 0))


def _approved_cap(project: Project, task_id: str) -> float:
    caps = [e["data"].get("cap_usd") for _, e in _entries_for(project, task_id, "gate.approved") if e["data"].get("cap_usd")]
    return float(caps[-1]) if caps else 0.0


def _held_why(project: Project, task_id: str) -> str:
    held = _entries_for(project, task_id, "launch.held")
    return held[-1][1]["reason"] if held else "launch rule"


def _limit_words(project: Project, limit: float, over: bool) -> str:
    mode = budgets.mode(project)
    if mode == "none":
        return "budget mode none (no spending limit)"
    if mode == "ceiling":
        return (f"auto_launch_usd and the budget mode's ceiling (${limit:.2f})" if over
                else f"the budget mode's ${limit:.2f} ceiling")
    return f"auto_launch_usd (${limit:.2f})" if over else f"auto_launch_usd ${limit:.2f}"


def _round(project: Project, req: Request | None, current: str | None, spawn) -> dict[str, str]:
    """With the lock held: launch the request's held parts, or ask you once. task -> "go" or "ask"."""
    from . import build, pilot
    if req is None:
        return {}
    tasks = project.tasks()
    made = [p for p in req.parts if p.task in tasks]
    st = {p.task: tasks[p.task]["status"] for p in made}
    if "stopped" in st.values():  # your Stop holds the whole request until you answer it
        return {}
    gate_open = {p.task: lifecycle.state(project, p.task).gate is not None for p in made}
    held = [p for p in made if st[p.task] == "planned" and gate_open[p.task]]
    running = build.running_builds(project)
    if not held or any(st[p.task] == "drafting" and gate_open[p.task] and p.task in running for p in made):
        return {}  # the last part to finish its plan launches them all
    out: dict[str, str] = {}

    def go(task: str, **how) -> None:
        lifecycle.approve(project, task, **how)
        out[task] = "go"
        if task != current:
            pilot._spawn_pilot(project, task, spawn)  # its pilot finds the plan approved and builds it
    ask = _last_ask(project, req.id)
    if ask:
        yes = _yes(project, ask)
        if yes:  # you launched them: each part you were asked about starts, as your launch
            named = set(ask[1]["data"].get("parts") or [])
            for p in [p for p in held if p.task in named]:
                go(p.task, answer={"id": yes["id"], "reason": f"your launch of this request's parts (ledger {yes['id']})"})
            held = [p for p in held if p.task not in out]
        elif st.get(ask[1]["data"]["task"]) == "needs you":
            return out  # the question is with you
    if not held:
        return out
    limit = budgets.launch_limit(project)
    caps = {p.task: _cap(project, p.task) for p in held}
    for p in made:
        if p.task not in caps and (cap := _approved_cap(project, p.task)):
            caps[p.task] = cap
    total = round(sum(caps.values()), 2)
    if total <= limit + 0.005:
        for p in held:
            go(p.task, rule=f"{_held_why(project, p.task)}; this request's {len(caps)} parts together cap "
                            f"${total:.2f}, within {_limit_words(project, limit, False)}")
        return out
    listing = ", ".join(f"{t} ${c:.2f}" for t, c in caps.items())
    asker = held[0].task
    project.ledger.append("review.requested", "parallax", f"this request's {len(caps)} parts together cap ${total:.2f} "
                          f"({listing}), over {_limit_words(project, limit, True)}", task=asker, request=req.id,
                          parts=[p.task for p in held], total_usd=total)
    out[asker] = "ask"
    return out


# moving every request on ----------------------------------------------------------------------------

def advance(project: Project, spawn=None) -> None:
    """Start the parts that can start, and launch the ones that are ready to. Safe to call any time."""
    if inside_task(project.root) or not requests(project):
        return
    try:
        with file_lock(project.state / LOCK):
            for req in requests(project):
                _move(project, req, spawn)
    except Exception as err:  # recorded once per error, never quiet: the next command tries again
        print(f"parallax: the conductor hit an error\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        error = " ".join(f"{type(err).__name__}: {err}".split())[:300]
        project.ledger.append_if(lambda entries: not any(e["kind"] == "conductor.failed" and e["data"].get("error") == error
                                                         for e in entries),
                                 "conductor.failed", "parallax", lint.one_sentence(f"the conductor hit an error: {error}"),
                                 error=error)


def _accepted(project: Project, status_of: str, task_id: str) -> dict | None:
    if status_of not in ACCEPTED:
        return None
    acc = _entries_for(project, task_id, "task.accepted")
    return acc[-1][1] if acc else None


def _move(project: Project, req: Request, spawn) -> None:
    from . import pilot
    tasks = project.tasks()
    if any(tasks[p.task]["status"] == "stopped" for p in req.parts if p.task in tasks):
        return  # your Stop holds the whole request until you answer it
    room = project.policy.conductor["max_parallel"] - sum(
        status.board(tasks[p.task]["status"]) in WORKING for p in req.parts if p.task in tasks)
    for p in req.parts:
        if p.task:
            continue
        prev, base = None, None
        if p.after is not None:
            prev = req.part(p.after).task
            if prev is None or prev not in tasks:
                continue
            acc = _accepted(project, tasks[prev]["status"], prev)
            if acc is None:
                if tasks[prev]["status"] in ENDED:  # it ended without an accept: yours to say how this part goes on
                    tid = _make(project, req, p, prev, None)
                    project.ledger.append("stuck.raised", "parallax", f"part {p.n} was to follow task {prev}, which ended "
                                          "without being accepted", task=tid, waiting_on=prev, request=req.id)
                continue
            base = acc["data"]["commit"]  # the accepted commit: never a tree you haven't accepted
        if room <= 0:
            continue
        pilot._spawn_pilot(project, _make(project, req, p, prev, base), spawn)
        room -= 1
    _round(project, request_of(project, req.id), None, spawn)


def _make(project: Project, req: Request, part: Part, after: str | None, base: str | None) -> str:
    return project.new_task(part.goal, "parallax", intent=True, request=req.id, part=part.n, after=after, base=base)["task"]


# what the board, the inbox and stats show -----------------------------------------------------------

def waiting_line(req: Request, part: Part) -> str:
    if part.after is not None:
        prev = req.part(part.after).task
        return f"waiting on task {prev}" if prev else f"waiting on part {part.after}"
    return "waiting for a part to finish"


def groups(project: Project, unfinished: bool = False) -> list[dict]:
    """Each split request with its parts in order, each with its own state. A part not made yet says
    what it waits on. unfinished: only requests with a part not done."""
    tasks = project.tasks()
    out = []
    for req in requests(project):
        parts = []
        for p in req.parts:
            if p.task in tasks:
                s = tasks[p.task]["status"]
                parts.append({"n": p.n, "task": p.task, "goal": p.goal, "status": s, "state": status.board(s), "waiting": ""})
            else:
                parts.append({"n": p.n, "task": None, "goal": p.goal, "status": "waiting", "state": "waiting",
                              "waiting": waiting_line(req, p)})
        if unfinished and all(x["state"] == "done" for x in parts):
            continue
        out.append({"request": req.id, "words": " ".join(req.words.split()), "parts": parts})
    return out


def held_line(project: Project, task_id: str) -> str:
    """What a held part waits for, in one sentence."""
    found = part_of(project, task_id)
    ask = _last_ask(project, found[0].id) if found else None
    if ask and ask[1]["data"]["task"] != task_id and task_id in (ask[1]["data"].get("parts") or []) \
            and project.task(ask[1]["data"]["task"])["status"] == "needs you":
        return (f"Its plan fits, and it waits for your answer on task {ask[1]['data']['task']}, "
                "which launches the request's parts together.")
    return "Its plan fits, and it launches once the request's other parts have their plans."

