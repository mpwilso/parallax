"""parallax command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import ParallaxError, Project

MARK = {"allow": "ALLOWED", "ask": "NEEDS YOU", "deny": "REFUSED"}


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

    sub.add_parser("inbox", help="decisions waiting on you")
    for name in ("approve", "reject"):
        r = sub.add_parser(name, help=f"{name} a pending decision")
        r.add_argument("decision")
        r.add_argument("--reason", required=True)

    lg = sub.add_parser("log", help="show the ledger")
    lg.add_argument("-n", type=int, default=20)
    sub.add_parser("verify", help="check the ledger hasn't been edited")

    args = p.parse_args(argv)
    try:
        return _run(args)
    except ParallaxError as err:
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

    if args.cmd == "inbox":
        items = proj.inbox()
        if not items:
            print("inbox empty. nothing waiting on you.")
        for e in items:
            print(f"{e['id']}  task {e['data']['task']}  {e['data']['action']}  {e['reason']}")
        return 0

    if args.cmd in ("approve", "reject"):
        e = proj.resolve(args.decision, args.cmd == "approve", args.reason)
        print(f"{e['data']['outcome']}  {e['data']['action']}  (ledger {e['id']})")
        return 0

    if args.cmd == "log":
        for e in proj.ledger.entries()[-args.n:]:
            extra = " ".join(f"{k}={v}" for k, v in e["data"].items() if k in ("task", "action", "outcome", "why"))
            print(f"{e['ts']}  {e['id']}  {e['kind']:<18} {e['actor']:<6} {extra}  {e['reason']}")
        return 0

    if args.cmd == "verify":
        ok, msg = proj.ledger.verify()
        print(msg)
        return 0 if ok else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
