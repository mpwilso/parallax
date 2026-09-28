"""parallax command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agents.base import AgentUnavailable
from .core import ParallaxError, Project, refuse_inside_task
from .inbox import batched, details, label, recommendations, resolve_item, split_goal

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
    tn.add_argument("--profile", default="default", help="policy profile, e.g. readonly")
    tn.add_argument("--plan", action="store_true", help="plan first when it runs")
    tn.add_argument("--queue", action="store_true", help="queue it for the next pulse")
    tq = tsub.add_parser("queue", help="queue a task for the next pulse")
    tq.add_argument("task")
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
    rn.add_argument("--plan", action="store_true", default=None, help="plan first and have the plan checked")
    rn.add_argument("--model", default=None)
    rv = sub.add_parser("review", help="run the blind checker on a task's current diff")
    rv.add_argument("task")
    rv.add_argument("--model", default=None)

    gl = sub.add_parser("goal", help="have the conductor split a goal into task proposals")
    gl.add_argument("text")
    gl.add_argument("--model", default=None)
    pl = sub.add_parser("pulse", help="check on tasks, start queued work, ask the conductor, record it")
    pl.add_argument("--no-conductor", action="store_true", help="checks and launches only")
    pl.add_argument("--model", default=None)

    sub.add_parser("inbox", help="everything waiting on you, grouped, with recommendations")
    for name in ("approve", "reject"):
        r = sub.add_parser(name, help=f"{name} inbox items (on a disagreement: side with the maker / the checker)")
        r.add_argument("items", nargs="+", metavar="item")
        r.add_argument("--reason", required=True, help="one reason, recorded on every item")

    ev = sub.add_parser("evidence", help="approvals and rejections per exact request, and where the line could move")
    ev.add_argument("-n", type=int, default=20)

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
            t = proj.new_task(args.goal, profile=args.profile, plan=args.plan, queue=args.queue)
            print(f"task {t['task']}  [{t['status']}]  {t['goal']}\n  branch   {t['branch']}\n  worktree {t['worktree']}")
            if t["profile"] != "default":
                print(f"  profile  {t['profile']}")
        elif args.tcmd == "queue":
            proj.queue_task(args.task)
            print(f"task {args.task} queued for the next pulse")
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
        refuse_inside_task(proj.root)
        return _agents(proj, args)

    if args.cmd in ("goal", "pulse"):
        refuse_inside_task(proj.root)
        return _conduct(proj, args)

    if args.cmd == "inbox":
        groups = batched(proj)
        if not groups:
            print("inbox empty. nothing waiting on you.")
        recs = recommendations(proj)
        for header, items in groups:
            print(header)
            for e in items:
                print(f"  {e['id']}  {label(e)}  {_line(e['reason'])}")
                for extra in details(e):
                    print(f"            {_line(extra)}")
                if e["id"] in recs:
                    r = recs[e["id"]]
                    print(f"            conductor recommends {r['data']['option']}: {_line(r['reason'])}")
        return 0

    if args.cmd in ("approve", "reject"):
        failed = 0
        for item in args.items:
            try:
                out = resolve_item(proj, item, args.cmd == "approve", args.reason)
            except ParallaxError as err:
                print(f"parallax: {err}", file=sys.stderr)
                failed += 1
                continue
            e, d = out.entry, out.entry["data"]
            what = d.get("action") or (f"disagreement ({d['stage']})" if d.get("stage") else d["about"].split(".")[0])
            print(f"{d['outcome']}  {what}  (ledger {e['id']})")
            if out.task:
                print(f"  task {out.task['task']} created and queued for the next pulse")
            if out.changed:
                print(f"  {out.changed}")
        return 1 if failed else 0

    if args.cmd == "evidence":
        from .evidence import table
        rows = table(proj)[:args.n]
        if not rows:
            print("no decisions in the evidence window yet.")
        else:
            print(f"{'approved':>8}  {'rejected':>8}  {'status':<22}  request")
        for (profile, action, key), s, status in rows:
            where = "" if profile == "default" else f" ({profile})"
            print(f"{len(s['approved']):>8}  {len(s['rejected']):>8}  {status:<22}  {action}{where}: {_line(key, 80)}")
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


def _line(text: str, width: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + "..."


def _conductor(model: str | None):
    """The conductor to use. Tests swap this out."""
    from .agents.claude import ClaudeConductor
    return ClaudeConductor(model) if model else ClaudeConductor()


def _conduct(proj: Project, args) -> int:
    from .pulse import pulse

    if args.cmd == "goal":
        entries = split_goal(proj, _conductor(args.model), args.text)
        if not entries:
            print("the conductor proposed no tasks.")
        for e in entries:
            print(f"{e['id']}  {label(e)}  {_line(e['reason'])}")
        if entries:
            print("approve with `parallax approve <id> --reason \"...\"`. approved tasks are queued.")
        return 0

    conductor = None
    if not args.no_conductor:
        try:
            conductor = _conductor(args.model)
        except AgentUnavailable as err:
            print(f"conductor unavailable, checks only: {err}")
    res = pulse(proj, conductor)
    for tid in res["launched"]:
        print(f"launched {tid}")
    for f in res["findings"]:
        print(f"finding: {_line(f)}")
    if not res["findings"]:
        print("no finding")
    c = res["counts"]
    print(f"conductor {res['conductor']}. {c['inbox']} waiting on you, {c['ready']} ready to merge, "
          f"{c['running'] + c['launched']} running, {c['queued']} queued")
    return 0


def _agents(proj: Project, args) -> int:
    from .agents.claude import ClaudeAgent, ClaudeChecker
    from .checker import diff_material, review
    from .runner import ensure_can_run, run_task

    model = {"model": args.model} if args.model else {}
    checker = ClaudeChecker(**model)
    if args.cmd == "review":
        ensure_can_run(proj, args.task)
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
