"""parallax command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agents.base import AgentUnavailable
from .core import ParallaxError, Project

MARK = {"allow": "ALLOWED", "ask": "NEEDS YOU", "deny": "REFUSED"}
LOG_FIELDS = ("task", "action", "stage", "status", "verdict", "outcome", "why")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="parallax", description="Agents do the work. You make the calls.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="set up parallax in this git repo")

    t = sub.add_parser("task", help="manage tasks")
    tsub = t.add_subparsers(dest="tcmd", required=True)
    tn = tsub.add_parser("new", help="create a task in its own worktree")
    tn.add_argument("goal")
    tsub.add_parser("list", help="list tasks")
    td = tsub.add_parser("diff", help="show a task's diff against its base")
    td.add_argument("task")

    c = sub.add_parser("check", help="ask the policy whether an action is allowed")
    c.add_argument("task")
    c.add_argument("action")
    c.add_argument("--detail", default="")
    c.add_argument("--actor", default="agent")

    rn = sub.add_parser("run", help="run the maker on a task, then the blind checker")
    rn.add_argument("task")
    rn.add_argument("--plan", action="store_true", help="plan first and have the plan checked")
    rn.add_argument("--model", default=None)
    rv = sub.add_parser("review", help="run the blind checker on a task's current diff")
    rv.add_argument("task")
    rv.add_argument("--model", default=None)

    sub.add_parser("inbox", help="decisions waiting on you")
    for name in ("approve", "reject"):
        r = sub.add_parser(name, help=f"{name} a pending decision (for a disagreement: side with the maker / the checker)")
        r.add_argument("decision")
        r.add_argument("--reason", required=True)

    lg = sub.add_parser("log", help="show the ledger")
    lg.add_argument("-n", type=int, default=20)
    sub.add_parser("verify", help="check the ledger hasn't been edited")

    args = p.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # agent text can hold characters the console can't show
    try:
        return _run(args)
    except (ParallaxError, AgentUnavailable) as err:
        print(f"parallax: {err}", file=sys.stderr)
        return 1


def _run(args) -> int:
    cwd = Path.cwd()
    if args.cmd == "init":
        proj = Project.init(cwd)
        print(f"initialized parallax in {proj.root}")
        print("edit parallax.policy.toml to set what agents may do. anything unlisted is denied.")
        return 0

    proj = Project.find(cwd)

    if args.cmd == "task":
        if args.tcmd == "new":
            t = proj.new_task(args.goal)
            print(f"task {t['task']}  {t['goal']}\n  branch   {t['branch']}\n  worktree {t['worktree']}")
        elif args.tcmd == "list":
            tasks = proj.tasks()
            if not tasks:
                print("no tasks")
            for tid, t in tasks.items():
                print(f"{tid}  [{t['status']}]  {t['goal']}")
        elif args.tcmd == "diff":
            print(proj.diff(args.task) or "no changes")
        return 0

    if args.cmd == "check":
        res = proj.check(args.task, args.action, args.detail, args.actor)
        e = res["entry"]
        print(f"{MARK[res['ruling']]}  {args.action}  (ledger {e['id']})")
        if res["ruling"] == "ask":
            print(f"  waiting in inbox as decision {e['id']}")
        return 0 if res["ruling"] == "allow" else 2

    if args.cmd in ("run", "review"):
        return _agents(proj, args)

    if args.cmd == "inbox":
        items = proj.inbox()
        if not items:
            print("inbox empty. nothing waiting on you.")
        for e in items:
            d = e["data"]
            what = f"disagreement ({d['stage']})" if e["kind"] == "disagreement.raised" else d["action"]
            print(f"{e['id']}  task {d['task']}  {what}  {e['reason']}")
        return 0

    if args.cmd in ("approve", "reject"):
        e = proj.resolve(args.decision, args.cmd == "approve", args.reason)
        d = e["data"]
        what = d.get("action") or f"disagreement ({d.get('stage')})"
        print(f"{d['outcome']}  {what}  (ledger {e['id']})")
        return 0

    if args.cmd == "log":
        for e in proj.ledger.entries()[-args.n:]:
            extra = " ".join(f"{k}={v}" for k, v in e["data"].items() if k in LOG_FIELDS)
            e["reason"] = (e["reason"].splitlines() or [""])[0]  # one entry, one line; the ledger has it all
            print(f"{e['ts']}  {e['id']}  {e['kind']:<18} {e['actor']:<6} {extra}  {e['reason']}")
        return 0

    if args.cmd == "verify":
        ok, msg = proj.ledger.verify()
        print(msg)
        return 0 if ok else 1
    return 1


def _agents(proj: Project, args) -> int:
    from .agents.claude import ClaudeAgent, ClaudeChecker
    from .checker import diff_material, review
    from .runner import ensure_not_disputed, run_task

    model = {"model": args.model} if args.model else {}
    checker = ClaudeChecker(**model)
    if args.cmd == "review":
        ensure_not_disputed(proj, args.task)
        ve, dis = review(proj, args.task, checker, "diff", diff_material(proj, args.task))
        print(f"checker: {ve['data']['verdict']}  {ve['reason']}".rstrip())
        if dis:
            print(f"disagreement {dis['id']} is in your inbox")
        return 0

    def waiting(e: dict) -> None:
        print(f"waiting on decision {e['id']}  {e['data']['action']}  {e['reason']}")
        print("  approve or reject it from another terminal.")

    status = run_task(proj, args.task, ClaudeAgent(**model), checker,
                      plan=args.plan, on_wait=waiting, say=print)
    print(f"task {args.task}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
