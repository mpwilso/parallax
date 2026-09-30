"""parallax command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import lifecycle, lint
from .agents.base import AgentUnavailable
from .core import ParallaxError, Project, inside_task, refuse_inside_task

LOG_FIELDS = ("task", "action", "stage", "status", "verdict", "outcome", "why")


GUIDE = """\
parallax: agents do the work. you make the calls.

the agents: Focus drafts the intent and plan. Maker builds in the sandbox. Second Eye checks blind,
seeing only the result. Field tests the UI. Reticle, off until the evals say, writes tests of
the outcome before the build. which one runs next is decided by code, never by a model.

start here:
  parallax do "the work"                describe it once. drafting, building and checking run without you
  parallax inbox                        what waits on you: one item per task
  parallax show <task>                  its card: everything you need to decide
  parallax accept <task>                commit what was reviewed. merging is yours
  parallax reject <task> --reason "..." send it back with your reason
  parallax stats                        how many touches each task took. the target is 1
  parallax ui                           the same, in your browser

setup: parallax doctor, init. power use: diff, stop, approve, preflight, build, recheck, lint, log, verify, eval.
`parallax <command> -h` for details.
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
    tsub.add_parser("list", help="list tasks")
    td = tsub.add_parser("diff", help="show a task's diff against its base")
    td.add_argument("task")

    dw = sub.add_parser("do", help="describe the work once; it's drafted, built and checked without you")
    dw.add_argument("work", help="a few plain sentences")
    dw.add_argument("--wait", action="store_true", help="stay and show where it is until it needs you")
    df = sub.add_parser("diff", help="the change a task made: the reviewed tree against its base")
    df.add_argument("task")
    sub.add_parser("stats", help="human touches per task, from the ledger")
    ev = sub.add_parser("eval", help="run real merged fixes through the do pipeline, scored by hidden tests")
    ev.add_argument("cases", nargs="*", help="case ids, all by default. `parallax eval check` checks the cases instead, with no model")
    ev.add_argument("--budget", type=float, help="the most the whole run may spend, in estimated dollars. needed for a run")
    ev.add_argument("--per-case", type=float, help="each case's ceiling, in estimated dollars. default: your small_cap_usd")
    ev.add_argument("--reticle", action="store_true", help="run with Reticle on: tests of the outcomes, written before the build")
    ev.add_argument("--reticle-inferred", action="store_true",
                    help="Reticle on, testing inferred outcomes too: their failures are card notes, never rework")
    ev.add_argument("--seeded", action="store_true",
                    help="no Maker: break each real fix by code and score Second Eye and Reticle on the broken versions")
    ln = sub.add_parser("lint", help="check a file against the output shape")
    ln.add_argument("file")

    pf = sub.add_parser("preflight", help="test both sandbox layers for a task, without launching")
    pf.add_argument("task")
    bd = sub.add_parser("build", help="build an approved plan in the sandbox, in the background")
    bd.add_argument("task")
    sub.add_parser("stop", help="end every running build now")
    rc = sub.add_parser("recheck", help="check a built task again, in the background: code checks, tests, Second Eye (the blind checker)")
    rc.add_argument("task")
    ac = sub.add_parser("accept", help="commit exactly what was reviewed, for you to merge")
    ac.add_argument("task")
    ac.add_argument("--reason", default="", help="needed only to accept a risk, such as a secrets-scan hit")
    sh = sub.add_parser("show", help="where a task stands, in the output shape")
    sh.add_argument("task")

    sub.add_parser("inbox", help="what waits on you: one item per task")
    ui = sub.add_parser("ui", help="the main way to use parallax: intake, the board, and each task's decision card")
    ui.add_argument("--port", type=int, default=None, help="default: this project's own port, the same every time")
    ui.add_argument("--new-token", action="store_true", help="replace the link's token; old links stop working")
    ui.add_argument("--no-open", action="store_true", help="don't open the browser, just print the link")
    for name in ("approve", "reject"):
        r = sub.add_parser(name, help="approve a plan that waits for you" if name == "approve" else
                           "send a task back to Focus with your reason (--drop ends it instead)")
        r.add_argument("items", nargs="+", metavar="task")
        r.add_argument("--reason", default="", help="why. a reject needs one: it's what Focus redrafts from")
        if name == "reject":
            r.add_argument("--drop", action="store_true", help="end the task instead of redrafting it")
    dc = sub.add_parser("decide", help="answer the one decision a task is waiting on")
    dc.add_argument("task")
    dc.add_argument("option", help="one of the options its card lists")
    dc.add_argument("--reason", default="", help="needed for accept, reject and drop")

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
        already = (cwd / "parallax.policy.toml").exists() and (cwd / ".parallax").exists()
        proj = Project.init(cwd, confirm_setup=_confirm_setup)
        print(f"{'already set up' if already else 'initialized parallax'} in {proj.root}")
        print("  parallax.policy.toml  what agents may do. anything unlisted is denied.")
        print("  REVIEW.md             how Second Eye (the blind checker) reviews, and what blocks ready. you own it.")
        print('next: parallax do "what you want done"')
        return 0

    proj = Project.find(cwd)
    if not inside_task(proj.root):
        from .build import flag_stale_runs
        from .sessions import reconcile
        reconcile(proj)  # sessions whose process died mid-run: recorded, with what they cost
        for tid in flag_stale_runs(proj):  # housekeeping on every command, in place of a pulse
            print(f"task {tid} went quiet and is flagged stuck. it's in your inbox.")
        from .accept import confirm_merges
        for tid in confirm_merges(proj):
            print(f"task {tid}: you merged it unchanged. recorded.")

    if args.cmd == "task":
        if args.tcmd == "list":
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

    if args.cmd == "do":
        from . import pilot
        t = pilot.intake(proj, args.work)
        print(f"task {t['task']}: on it. drafting, building and checking run without you.")
        print("it comes to parallax inbox when it needs you." if not args.wait else "watching it. Ctrl+C stops watching, not the task.")
        return _watch(proj, t["task"]) if args.wait else 0

    if args.cmd == "diff":
        print(_reviewed_diff(proj, args.task) or "no changes")
        return 0

    if args.cmd == "decide":
        from . import decide
        print(decide.apply(proj, args.task, args.option, args.reason))
        return 0

    if args.cmd == "eval":
        return _eval(proj, args)

    if args.cmd == "stats":
        from . import stats
        print("\n".join(stats.report(proj)))
        return 0

    if args.cmd in ("preflight", "build", "recheck") and proj.tasks().get(args.task, {}).get("intent"):
        proj.ledger.append("human.command", "human", f"parallax {args.cmd}", task=args.task)  # a touch, for stats

    if args.cmd == "preflight":
        from . import build, preflight
        lines = preflight.report(build.run_preflight(proj, build.prepare(proj, args.task)))
        print("\n".join(lines))
        return 0 if lines[-1].startswith("ready") else 1

    if args.cmd == "build":
        from . import build
        p = build.prepare(proj, args.task)
        build.launch(proj, p)  # the builder preflights first, whoever starts it
        print(f"building {args.task}, estimated budget ${p.left:.2f}. parallax stop ends it.")
        return 0

    if args.cmd == "recheck":
        from . import build, check
        refuse_inside_task(proj.root)
        check.can_check(proj, args.task)
        build.launch(proj, build.prepare(proj, args.task), mode="check")
        print(f"checking {args.task} in the background. parallax show {args.task} tells you where it stands.")
        return 0

    if args.cmd == "accept":
        from .accept import accept, merge_command
        e = accept(proj, args.task, args.reason)
        print(f"accepted {args.task} as {e['data']['commit'][:7]}. merge it yourself:")
        print(merge_command(e))
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

    if args.cmd == "ui":
        from . import doctor
        from .ui import UI
        app = UI(proj.root, args.port, new_token=args.new_token)
        wsl = doctor.is_wsl(doctor.Machine())  # with interop off, WSL can't open your Windows browser
        opening = not (args.no_open or wsl)
        if wsl:
            print(f"parallax ui is running. open this in your Windows browser: {app.windows_url}")
        else:
            print(f"parallax ui is {'open in your browser' if opening else 'running'}: {app.url}")
        print("keep this window open while you use it. Ctrl+C to stop.", flush=True)
        try:
            app.serve(open_browser=opening)
        except KeyboardInterrupt:
            print("stopped.")
        return 0

    if args.cmd == "inbox":
        from . import inbox
        items, busy, merges = inbox.items(proj), inbox.working(proj), inbox.to_merge(proj)
        if not items:
            print("nothing waits on you.")
        for it in items:
            print(f"{it['task']}  {it['state']:<10}{_line(it['title'], 90)}")
        if items:
            print("parallax show <task> for its card.")
        if busy:
            print(f"{busy} working without you.")
        for m in merges:
            print(f"to merge, task {m['task']}: {m['command']}")
        return 0

    if args.cmd in ("approve", "reject"):
        failed = 0
        for task_id in args.items:
            try:
                lifecycle.lifecycle_task(proj, task_id)
                _gate(proj, task_id, args)
            except ParallaxError as err:
                print(f"parallax: {err}", file=sys.stderr)
                failed += 1
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


def _eval(proj: Project, args) -> int:
    from . import evals
    refuse_inside_task(proj.root)
    if args.cases[:1] == ["check"]:
        cases = evals.load_cases(proj.root, args.cases[1:])
        unsound = 0
        for case in cases:
            try:
                ok, seen = evals.check_case(case, proj.policy.check["test_command"])
            except ParallaxError as err:
                ok, seen = False, str(err)
            unsound += not ok
            print(f"{case.id:<16}{'sound' if ok else 'not sound'}: {seen}", flush=True)
        print("every case is sound." if not unsound else f"{unsound} of {len(cases)} cases are not sound.")
        return 1 if unsound else 0
    if args.budget is None or args.budget <= 0:
        raise ParallaxError("an eval run needs --budget: the most it may spend, in estimated dollars")
    if args.seeded:
        from . import seeded
        out = seeded.run(proj, evals.load_cases(proj.root, args.cases), args.budget)
    else:
        inferred = args.reticle_inferred
        out = evals.run(proj, evals.load_cases(proj.root, args.cases), args.budget, args.per_case,
                        reticle=args.reticle or inferred, inferred=inferred)
    print(f"results in {out.relative_to(proj.root)}. the summary is summary.md there.")
    return 0


def _line(text: str, width: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + "..."


def _shaped(proj: Project, text: str) -> None:
    """Print a report of Parallax's own, fitted until it lints. You never see a lint failure of
    Parallax's own making; if it had to repair something, that's recorded as a Parallax bug."""
    ids = {e["id"] for e in proj.ledger.entries()}
    fitted, repairs = lint.fit(text, root=proj.root, ledger_ids=ids)
    if repairs:
        proj.ledger.append("lint.fitted", "parallax", "; ".join(dict.fromkeys(repairs)), original=text)
    print(fitted.rstrip("\n"))


def _reviewed_diff(proj: Project, task_id: str) -> str:
    """The reviewed tree against its base, once there is one; the worktree before that."""
    import subprocess
    from .status import attempt
    staged = [e for e in attempt(proj.ledger.entries(), task_id) if e["kind"] == "check.staged"]  # this attempt's
    if not staged:
        return proj.diff(task_id)
    t = proj.task(task_id)
    return subprocess.run(["git", "-C", t["worktree"], "diff", t["base"], staged[-1]["data"]["tree"]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def _watch(proj: Project, task_id: str, every: float = 5.0) -> int:
    """--wait: say where the task is whenever that changes, until it needs you or is done."""
    import time
    from . import status
    last = ""
    try:
        while True:
            where = status.board(proj.task(task_id)["status"])
            if where != last:
                print(f"{task_id}: {where}", flush=True)
                last = where
            if where in ("ready", "needs you", "done"):
                print(f"parallax show {task_id} for its card.")
                return 0
            time.sleep(every)
    except KeyboardInterrupt:
        print("stopped watching. the task goes on.")
        return 0


def _gate(proj: Project, task_id: str, args) -> None:
    """approve and reject on a lifecycle task, through the task's one decision where there is one."""
    from . import decide, pilot
    dec = decide.decision(proj, task_id)
    if args.cmd == "reject":
        if args.drop:
            if dec is not None:
                print(decide.apply(proj, task_id, "drop", args.reason))
                return
            if not args.reason.strip():
                raise ParallaxError("dropping a task needs a reason")
            proj.ledger.append("task.rejected", "human", args.reason, task=task_id, was=proj.task(task_id)["status"])
            print(f"dropped {task_id}. it's out of the inbox.")
            return
        if dec is not None and any(o.name == "reject" for o in dec.options):
            print(decide.apply(proj, task_id, "reject", args.reason))
            return
        pilot.redraft(proj, task_id, args.reason)
        print(f"redrafting {task_id} from your reason. it comes back to the inbox.")
        return
    if dec is not None and dec.kind in ("launch", "review"):
        print(decide.apply(proj, task_id, "launch" if dec.kind == "launch" else "approve"))
        return
    e = lifecycle.approve(proj, task_id)
    print(f"approved {e['data']['gate'].replace('+', ' and ')} for {task_id} (ledger {e['id']}).")


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


def _confirm_setup(command: str) -> bool:
    """A repo's example policy may carry a [build] setup command, which runs as you. Never adopted unasked."""
    print("the repo's example policy has a [build] setup command. it would run as you, on the base commit, before each build:")
    print(f"  {command}")
    if not sys.stdin.isatty():
        print("not an interactive terminal, so it's left out. set [build] setup in parallax.policy.toml yourself if you want it.")
        return False
    return input("keep it? [y/N] ").strip().lower() in ("y", "yes")


def _doctor() -> int:
    from . import doctor

    checks = doctor.run()
    for line in doctor.report(checks):
        print(line)
    failed = [c for c in checks if c.status == doctor.FAIL]
    print("not ready: fix what failed above." if failed else "ready.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
