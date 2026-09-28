"""parallax command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import lifecycle, lint
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
  parallax intent new "what you want"   a task: agents draft its intent and plan
  parallax task new "what you want"     the old way: run straight from its goal
  parallax approve <task>               approve the task's pending gate
  parallax reject <task> --reason "..." reject it, with a reason
  parallax preflight <task>             test the sandbox for an approved task
  parallax build <task>                 build it in the sandbox, then check it. parallax stop ends it
  parallax show <task>                  where it stands: ready, or what needs you
  parallax lint <file>                  check a report or task file against the output shape

more: parallax run, inbox, ui, draft, log, verify, eval. `parallax <command> -h` for details.
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
    tsub.add_parser("list", help="list tasks")
    td = tsub.add_parser("diff", help="show a task's diff against its base")
    td.add_argument("task")

    it = sub.add_parser("intent", help="start a task from what you want: agents draft its intent and plan")
    isub = it.add_subparsers(dest="icmd", required=True)
    inew = isub.add_parser("new", help="create the task, branch and worktree, and draft the intent and plan")
    inew.add_argument("text", help="a few rough sentences")
    inew.add_argument("--model", default=None)
    dr = sub.add_parser("draft", help="draft what the task's pending gate still needs, again")
    dr.add_argument("task")
    dr.add_argument("--model", default=None)
    ln = sub.add_parser("lint", help="check a file against the output shape")
    ln.add_argument("file")

    pf = sub.add_parser("preflight", help="test both sandbox layers for a task, without launching")
    pf.add_argument("task")
    bd = sub.add_parser("build", help="build an approved plan in the sandbox, in the background")
    bd.add_argument("task")
    sub.add_parser("stop", help="end every running build now")
    rc = sub.add_parser("recheck", help="check a built task again, in the background: code checks, tests, the blind checker")
    rc.add_argument("task")
    sh = sub.add_parser("show", help="where a task stands, in the output shape")
    sh.add_argument("task")

    rn = sub.add_parser("run", help="run the maker on a task, then the blind checker")
    rn.add_argument("task")
    rn.add_argument("--model", default=None)
    sub.add_parser("inbox", help="everything waiting on you, grouped by task")
    ui = sub.add_parser("ui", help="open the inbox in your browser: every decision with what you need to make it")
    ui.add_argument("--port", type=int, default=0)
    ui.add_argument("--no-open", action="store_true", help="don't open the browser, just print the link")
    for name in ("approve", "reject"):
        r = sub.add_parser(name, help=f"{name} a task's pending gate, or inbox items "
                                      "(on a disagreement: side with the maker / the checker)")
        r.add_argument("items", nargs="+", metavar="task-or-item")
        r.add_argument("--reason", default="", help="one reason, recorded on every item. "
                                                    "rejects and inbox items need one; approving a gate doesn't")

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

    if args.cmd == "lint":
        return _lint(Path(args.file), cwd)

    if args.cmd == "init":
        proj = Project.init(cwd)
        print(f"initialized parallax in {proj.root}")
        print("  parallax.policy.toml  what agents may do. anything unlisted is denied.")
        print("  REVIEW.md             how the blind checker reviews, and what blocks ready. you own it.")
        print('next: parallax intent new "what you want done"')
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
            t = proj.new_task(args.goal)
            print(f"task {t['task']}  [{t['status']}]  {t['goal']}\n  branch   {t['branch']}\n  worktree {t['worktree']}")
            print(f"next: parallax run {t['task']}")
        elif args.tcmd == "list":
            tasks = proj.tasks()
            if not tasks:
                print("no tasks")
            for tid, t in tasks.items():
                cost = f"  ${t['cost_usd']:.2f} est" if t.get("cost_usd") else ""
                st = lifecycle.status_line(proj, tid) if t.get("intent") else t["status"]
                print(f"{tid}  [{st}]{cost}  {_line(t['goal'], 100)}")
        elif args.tcmd == "diff":
            print(proj.diff(args.task) or "no changes")
        return 0

    if args.cmd in ("intent", "draft"):
        refuse_inside_task(proj.root)
        drafter = _drafter(args.model)
        if args.cmd == "intent":
            t = lifecycle.new_intent(proj, args.text, drafter)
        else:
            t = lifecycle.lifecycle_task(proj, args.task)
            st = lifecycle.state(proj, args.task)
            if st.gate is None:
                raise ParallaxError(f"the plan for {args.task} is already approved")
            lifecycle.draft(proj, args.task, lifecycle.redraft_docs(st), drafter)
        print(f"task {t['task']} on branch {t['branch']}")
        _shaped(proj, lifecycle.report(proj, t["task"]))
        return 0

    if args.cmd in ("preflight", "build"):
        from . import build, preflight
        p = build.prepare(proj, args.task)
        lines = preflight.report(build.run_preflight(proj, p))
        if args.cmd == "preflight" or not lines[-1].startswith("ready"):
            print("\n".join(lines))
            return 0 if lines[-1].startswith("ready") else 1
        build.launch(proj, p)
        print(f"building {args.task}, estimated budget ${p.left:.2f}. parallax stop ends it.")
        return 0

    if args.cmd == "recheck":
        from . import build, check
        refuse_inside_task(proj.root)
        check.can_check(proj, args.task)
        build.launch(proj, build.prepare(proj, args.task), mode="check")
        print(f"checking {args.task} in the background. parallax show {args.task} tells you where it stands.")
        return 0

    if args.cmd == "show":
        from . import show
        _shaped(proj, show.report(proj, args.task))
        return 0

    if args.cmd == "stop":
        from . import build
        stopped = build.stop(proj)
        print(f"stopped {', '.join(stopped)}." if stopped else "nothing is running.")
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
        tasks = proj.tasks()
        for item in args.items:
            if tasks.get(item, {}).get("intent"):
                try:
                    _gate(proj, item, args)
                except ParallaxError as err:
                    print(f"parallax: {err}", file=sys.stderr)
                    failed += 1
                continue
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


def _drafter(model: str | None):
    """Drafters for the lifecycle files: read-only agents, each call capped. Tests swap this out."""
    from .agents.claude import ClaudeAgent
    extra = {"model": model} if model else {}
    return lambda cap: ClaudeAgent(**extra, max_budget_usd=cap)


def _shaped(proj: Project, text: str) -> None:
    """Print a report only if it passes lint. A lint failure blocks the output."""
    ids = {e["id"] for e in proj.ledger.entries()}
    problems = lint.lint_report(text, root=proj.root, ledger_ids=ids)
    if problems:
        raise ParallaxError("this output failed lint, so it isn't shown: "
                            + "; ".join(f"line {n}: {m}" for n, m in problems))
    print(text)


def _gate(proj: Project, task_id: str, args) -> None:
    if args.cmd == "reject":
        e = lifecycle.reject(proj, task_id, args.reason)
        if e["kind"] == "task.rejected":
            print(f"rejected task {task_id} (ledger {e['id']}). your reason stays in the ledger.")
            return
        print(f"rejected {e['data']['gate'].replace('+', ' and ')} for {task_id} (ledger {e['id']}).")
        print(f"next: edit docs/tasks/{task_id}/ and approve it, or run parallax draft {task_id} to redraft with your reason.")
        return
    e = lifecycle.approve(proj, task_id)
    print(f"approved {e['data']['gate'].replace('+', ' and ')} for {task_id} (ledger {e['id']}).")
    st = lifecycle.state(proj, task_id)
    if st.gate is not None:  # a large task: the spec and plan are drafted now
        lifecycle.draft(proj, task_id, lifecycle.redraft_docs(st), _drafter(None))
        _shaped(proj, lifecycle.report(proj, task_id))
        return
    plan = lifecycle.plan_data(proj, task_id) or {}
    if plan:
        print(f"estimated cost ${plan['estimated_cost_usd']:.2f} (unverified), budget cap ${plan['budget_cap_usd']:.2f}.")
        if plan.get("review_tightening"):
            print(f"review tightening for this task: {_line(plan['review_tightening'])}")
    print("the build step lands in m9.")


def _lint(path: Path, cwd: Path) -> int:
    if not path.is_file():
        raise ParallaxError(f"no file {path}")
    try:
        proj = Project.find(cwd)
        root, ids = proj.root, {e["id"] for e in proj.ledger.entries()}
    except ParallaxError:
        root, ids = cwd, None
    problems = lint.lint_file(path, root, ids)
    for n, msg in problems:
        print(f"{path}:{n} {msg}")
    if not problems:
        print("ok")
    return 1 if problems else 0


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

    status = run_task(proj, args.task, ClaudeAgent(**model), checker, say=print)
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
