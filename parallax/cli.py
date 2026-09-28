"""parallax command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agents.base import AgentUnavailable
from .core import ParallaxError, Project, inside_task, refuse_inside_task
from .inbox import batched, label

MARK = {"allow": "ALLOWED", "ask": "NEEDS YOU", "deny": "REFUSED"}
LOG_FIELDS = ("task", "action", "stage", "status", "verdict", "outcome", "why")


GUIDE = """\
parallax: agents do the work. you make the calls.

start here:
  parallax doctor                       check this machine can run agents in a sandbox
  parallax init                         set up parallax in your repo's folder
  parallax task new "what you want"     a task in its own copy of the repo
  parallax run <task>                   an agent does it, a second one checks it
  parallax ui                           everything waiting on you, in your browser
  parallax approve <id> --reason "..."  (or reject) every call needs a reason

more: parallax inbox, log, verify, eval. `parallax <command> -h` for details.
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stdout.reconfigure(errors="replace")
        print(GUIDE, end="")
        return 0
    p = argparse.ArgumentParser(prog="parallax", description="Agents do the work. You make the calls.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check this machine can run agents in a sandbox")
    sub.add_parser("init", help="set up parallax in this git repo")

    t = sub.add_parser("task", help="manage tasks")
    tsub = t.add_subparsers(dest="tcmd", required=True)
    tn = tsub.add_parser("new", help="create a task in its own worktree")
    tn.add_argument("goal")
    tn.add_argument("--plan", action="store_true", help="plan first when it runs")
    tsub.add_parser("list", help="list tasks")
    td = tsub.add_parser("diff", help="show a task's diff against its base")
    td.add_argument("task")

    rn = sub.add_parser("run", help="run the maker on a task, then the blind checker")
    rn.add_argument("task")
    rn.add_argument("--plan", action="store_true", default=None, help="plan first and have the plan checked")
    rn.add_argument("--model", default=None)
    sub.add_parser("inbox", help="everything waiting on you, grouped by task")
    ui = sub.add_parser("ui", help="open the inbox in your browser: every decision with what you need to make it")
    ui.add_argument("--port", type=int, default=0)
    ui.add_argument("--no-open", action="store_true", help="don't open the browser, just print the link")
    for name in ("approve", "reject"):
        r = sub.add_parser(name, help=f"{name} inbox items (on a disagreement: side with the maker / the checker)")
        r.add_argument("items", nargs="+", metavar="item")
        r.add_argument("--reason", required=True, help="one reason, recorded on every item")

    el = sub.add_parser("eval", help="test parallax on real merged fixes (run from the parallax repo folder)")
    esub = el.add_subparsers(dest="ecmd", required=True)
    ec = esub.add_parser("check", help="make sure every case is sound. no model, no cost")
    ec.add_argument("--case", action="append", help="just this case (repeat for more)")
    er = esub.add_parser("run", help="run parallax on the cases and write a report")
    er.add_argument("--case", action="append", help="just this case (repeat for more)")
    er.add_argument("--budget", type=float, default=25.0, help="stop before spending more than this, in dollars")
    er.add_argument("--model", default=None)
    ep = esub.add_parser("report", help="rebuild a run's report")
    ep.add_argument("run", nargs="?", help="the run's .jsonl file (default: the latest)")

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
    if args.cmd == "doctor":
        return _doctor()

    if args.cmd == "init":
        proj = Project.init(cwd)
        print(f"initialized parallax in {proj.root}")
        print("  parallax.policy.toml  what agents may do. anything unlisted is denied.")
        print('next: parallax task new "what you want done"')
        return 0

    if args.cmd == "eval":
        return _eval(args, cwd)

    proj = Project.find(cwd)
    if not inside_task(proj.root):
        from .runner import flag_stale_runs
        for tid in flag_stale_runs(proj):  # housekeeping on every command, in place of a pulse
            print(f"task {tid} went quiet and is flagged stuck. it's in your inbox.")

    if args.cmd == "task":
        if args.tcmd == "new":
            t = proj.new_task(args.goal, plan=args.plan)
            print(f"task {t['task']}  [{t['status']}]  {t['goal']}\n  branch   {t['branch']}\n  worktree {t['worktree']}")
            print(f"next: parallax run {t['task']}")
        elif args.tcmd == "list":
            tasks = proj.tasks()
            if not tasks:
                print("no tasks")
            for tid, t in tasks.items():
                cost = f"  ${t['cost_usd']:.2f}" if t.get("cost_usd") else ""
                print(f"{tid}  [{t['status']}]{cost}  {_line(t['goal'], 100)}")
        elif args.tcmd == "diff":
            print(proj.diff(args.task) or "no changes")
        return 0

    if args.cmd == "run":
        refuse_inside_task(proj.root)
        return _agents(proj, args)

    if args.cmd == "ui":
        from .ui import UI
        app = UI(proj.root, args.port)
        print(f"parallax ui is {'running' if args.no_open else 'open in your browser'}: {app.url}")
        print("keep this window open while you use it. Ctrl+C to stop.", flush=True)
        try:
            app.serve(open_browser=not args.no_open)
        except KeyboardInterrupt:
            print("stopped.")
        return 0

    if args.cmd == "inbox":
        groups = batched(proj)
        if not groups:
            print("inbox empty. nothing waiting on you.")
        for header, items in groups:
            print(header)
            for e in items:
                print(f"  {e['id']}  {label(e)}  {_line(e['reason'])}")
        return 0

    if args.cmd in ("approve", "reject"):
        failed = 0
        for item in args.items:
            try:
                e = proj.resolve(item, args.cmd == "approve", args.reason)
            except ParallaxError as err:
                print(f"parallax: {err}", file=sys.stderr)
                failed += 1
                continue
            d = e["data"]
            what = d.get("action") or (f"disagreement ({d['stage']})" if d.get("stage") else d["about"].split(".")[0])
            print(f"{d['outcome']}  {what}  (ledger {e['id']})")
        return 1 if failed else 0

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


def _doctor() -> int:
    from . import doctor

    checks = doctor.run()
    for line in doctor.report(checks):
        print(line)
    failed = [c for c in checks if c.status == doctor.FAIL]
    print("not ready: fix what failed above." if failed else "ready.")
    return 1 if failed else 0


def _eval(args, cwd: Path) -> int:
    from . import evals

    refuse_inside_task(cwd)
    root = evals.find_cases(cwd)
    if args.ecmd == "report":
        runs = sorted((root / evals.RESULTS_DIR).glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        path = Path(args.run) if args.run else (runs[-1] if runs else None)
        if path is None:
            raise ParallaxError("no eval runs yet. start one with `parallax eval run`")
        print(f"report: {evals.write_report(path)}")
        print(evals.summary_line(path))
        return 0

    cases = evals.load_cases(root, args.case)
    if args.ecmd == "check":
        bad = 0
        for case in cases:
            print(f"checking {case.id} ...", flush=True)
            problem = evals.check_case(case, evals.home() / "check")
            print(f"  {'ok' if problem is None else 'broken: ' + problem}")
            bad += problem is not None
        print(f"{len(cases) - bad} of {len(cases)} cases are sound.")
        return 1 if bad else 0

    from .agents.claude import ClaudeAgent, ClaudeChecker
    model = {"model": args.model} if args.model else {}
    n = f"{len(cases)} case{'s' if len(cases) != 1 else ''}"
    print(f"running {n}, budget ${args.budget:.2f}. this takes a while; each case prints when done.")
    out = evals.run(root, cases,
                    make_maker=lambda cap: ClaudeAgent(**model, max_budget_usd=cap),
                    make_checker=lambda cap: ClaudeChecker(**model, max_budget_usd=cap),
                    budget=args.budget, say=lambda s: print(s, flush=True))
    print(f"report: {out.with_suffix('.md')}")
    print(evals.summary_line(out))
    return 0


def _agents(proj: Project, args) -> int:
    from .agents.claude import ClaudeAgent, ClaudeChecker
    from .runner import run_task

    model = {"model": args.model} if args.model else {}
    checker = ClaudeChecker(**model)

    def waiting(e: dict) -> None:
        print(f"waiting on you: {e['data']['action']} {_line(e['reason'], 100)}")
        print(f"  decide in `parallax ui`, or: parallax approve {e['id']} --reason \"...\"", flush=True)

    status = run_task(proj, args.task, ClaudeAgent(**model), checker,
                      plan=args.plan, on_wait=waiting, say=print)
    print(f"task {args.task}: {status}")
    print(NEXT.get(status, "").format(task=args.task), end="")
    return 0


NEXT = {
    "ready": "next: look at the change with `parallax task diff {task}`. merging it is your call.\n",
    "disputed": "next: it's waiting on you. decide in `parallax ui`, or `parallax inbox`.\n",
    "stuck": "next: it's waiting on you. decide in `parallax ui`, or `parallax inbox`.\n",
    "maker failed": "next: see what happened with `parallax log`, then try `parallax run {task}` again.\n",
}


if __name__ == "__main__":
    sys.exit(main())
