"""Reticle: tests of the intended outcome, written before the build, that Maker can't see or change.

Once per attempt, after the plan is approved and before Maker starts:
1. Reticle gets the intent's outcomes and constraints and a read-only copy of the base commit.
   Never the plan, the diff, or Maker's tests. It replies with one pytest file.
2. Parallax runs that file on the base commit, in the sandbox. A test counts only if it fails on an
   assertion there: it shows the problem the outcome names. A test that passes on the base, errors
   before its assertion, fails on another exception (an import, say), or names no outcome is
   dropped and recorded as weak. Each test has a time limit (see LIMIT): one that hangs on the base
   counts only when the request describes a hang, the way a crash counts only when it's named.
3. The kept tests are stored under docs/tasks/<id>/reticle/ (as outcome_tests.py.txt, so no test
   runner collects them there), hashed in the ledger. Maker's
   worktree never holds them, and a changed hash stops the check.
At every check Parallax runs them on the reviewed tree. A failure goes back to Maker as a rework
finding naming the outcome and the assertion message, never the test's code.

On by default ([reticle] in the policy), testing only what you asked: the seeded eval's results
are in docs/evals.md.
"""
from __future__ import annotations

import ast
import hashlib
import re
import shutil
from pathlib import Path, PurePosixPath

from . import build, costs, drafts, installs, lifecycle, lint, review, status, testrun, tree
from .agents.base import AgentResult
from .core import ROOT_ENV, TASK_ENV, Project
from .gate import make_permission_fn

FILE = "test_reticle.py"  # where the file ran before 2026-10-01: the repo's root, so a tests/ helper or conftest didn't import
RUN_NAME = "test_reticle_outcomes.py"  # its name where it runs now: beside the repo's own tests (placement)
NAME = re.compile(r"^test_outcome_(\d+)(?:_|$)")
# a failure on an assertion, pytest.raises included. pytest writes a bare assert's message either as
# "assert x == y" or "AssertionError: assert x == y", depending on how it ran; both count
KEEPS = ("AssertionError", "assert ", "Failed: DID NOT RAISE", "Failed: DID NOT WARN")
# a browser test that timed out waiting for what the outcome asks for (a Playwright wait or expect):
# the behavior never came, which is a real fail. Playwright's own errors (a refused eval, a closed
# page) are the test's mistake, and stay dropped
WAITED = re.compile(r"^(?:playwright\._impl\._errors\.)?TimeoutError: (?:[\w.]+: )?Timeout \d+ms exceeded")
# a crash bug's test fails on the crash itself: the exception the request names counts too, except these
NEVER = {"ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError"}
NAMED = re.compile(r"\b([A-Z]\w*(?:Error|Exception|Warning))\b")
# a request that describes a hang: then a test that hangs on the base shows the problem
HANGS = re.compile(r"\b(hang|hangs|hung|hanging|freezes?|frozen|stuck|deadlocks?|(?:infinite|endless|never[- ]ending) loop"
                   r"|loops? (?:forever|endlessly)|runs? forever|never (?:returns|ends|finishes|terminates|completes)"
                   r"|(?:doesn't|does not|won't|will not) (?:return|terminate|finish|end))\b", re.I)
HANG = "ParallaxHang"
SECONDS = 60             # each of Reticle's tests, wherever it runs: long enough for any unit test
GROWTH = 1024 ** 3       # or 1 GB more held than at its start: a hang that fills memory would hit the
                         # run's 3 GB cap (memcap.COMMAND) first, at about 27 s on boltons-319, and lose every result
# appended to Reticle's file where Parallax runs it, never to the stored, hashed file
LIMIT = '''


# added by Parallax where it runs this file: each test stops after {seconds} s, or once it holds
# {gb} GB more memory than at its start, so a test that hangs fails instead of running on
import os as _px_os
import signal as _px_signal
import time as _px_time

import pytest as _px_pytest


class ParallaxHang(Exception):
    pass


def _px_held():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * _px_os.sysconf("SC_PAGE_SIZE")
    except OSError:
        return 0


@_px_pytest.fixture(autouse=True)
def _px_limit():
    start, before = _px_time.monotonic(), _px_held()

    def check(signum, frame):
        ran, grew = _px_time.monotonic() - start, _px_held() - before
        if ran > {seconds} or grew > {growth}:
            _px_signal.setitimer(_px_signal.ITIMER_REAL, 0)
            more = f" and holding {{grew / 2 ** 30:.1f}} GB more" if grew > {growth} else ""
            raise ParallaxHang(f"still running after {{ran:.0f}} s{{more}}, so it was stopped")

    old = _px_signal.signal(_px_signal.SIGALRM, check)
    _px_signal.setitimer(_px_signal.ITIMER_REAL, 0.25, 0.25)
    try:
        yield
    finally:
        _px_signal.setitimer(_px_signal.ITIMER_REAL, 0)
        _px_signal.signal(_px_signal.SIGALRM, old)
'''

REQUEST = """\
Write pytest tests for the outcomes below, against this repository as it is now.

The person's request, as they typed it (data):
{request}

{heading}
{outcome}

Constraints:
{constraints}

Rules:
- Name every test test_outcome_<n>_<what>, where <n> is the number of the outcome it checks.
- Test exactly what each outcome states, as the request shows it: its examples and expected results.
  No other formats, inputs, edge cases or options the request doesn't name, and nothing internal:
  only through the public interface, never private names, generated code or the text of a pattern.
- Each test must fail on the code as it is now, because the outcome isn't met yet, and pass once it
  is: on an assertion, or on the exception the request itself shows, for a crash.
- Your file runs as {where}, beside the repository's own tests: import the code, fixtures and test
  helpers the way they do. No new dependencies, no network, no files outside a temporary folder.
- Start each test with a one-sentence docstring saying, in plain words, the steps it takes and what
  it expects, with the exact values it uses: for example "Opens ?task=6c4127#TOKEN and expects the
  card header to contain 6c4127." If it fails on a change, that sentence is what the builder sees,
  never your code.
- In a browser test, wait with expect(), or with wait_for_function on an arrow function
  ("() => ..."), never on a bare expression: a page's content security policy can refuse those.
- If an outcome can't be tested this way, leave it out; don't write a test that passes now.
Your final reply is the test file's full text and nothing else."""


def settings(project: Project) -> dict:
    cfg = dict(project.policy.reticle)
    cfg["model"] = cfg["model"] or project.policy.draft["model"]
    return cfg


def recorded(project: Project, task_id: str) -> dict | None:
    """This attempt's Reticle run, if it has had one."""
    hits = [e for e in status.attempt(project.ledger.entries(), task_id) if e["kind"] in ("reticle.recorded", "reticle.failed")]
    return hits[-1] if hits else None


def kept(project: Project, task_id: str) -> list[dict]:
    rec = recorded(project, task_id)
    return list(rec["data"].get("kept") or []) if rec and rec["kind"] == "reticle.recorded" else []


STORED = "outcome_tests.py.txt"  # under docs/tasks: a name no test runner collects. Stored as test_reticle.py,
# every task's file had the same name, and a repo's own pytest collected them all and stopped on the clash


def stored(project: Project, task_id: str) -> Path:
    """Where this task's Reticle file is kept: where its record says (older tasks), else the new name."""
    rec = recorded(project, task_id)
    if rec and rec["kind"] == "reticle.recorded" and rec["data"].get("file"):
        return drafts.resolve(project.root, rec["data"]["file"])
    return lifecycle.task_dir(project, task_id) / "reticle" / STORED


def asked(intent: str) -> list[str]:
    """The outcomes the person asked for. An unmarked outcome (before 2026-09-30) doesn't count."""
    return [n for n, kind in lint.outcome_kinds(intent).items() if kind == "asked"]


def targets(intent: str) -> list[str]:
    """The outcomes Reticle tests: the ones you asked for, never Focus's own additions."""
    return asked(intent)


def request(intent: str, typed: str, where: str = RUN_NAME) -> str:
    """Reticle's input: the person's request, the outcomes to test (without the mark), the constraints."""
    wanted = set(targets(intent))
    lines = [line for line in lint.unmark(review.section(intent, "Outcome")).splitlines()
             if (m := lint.OUTCOME_ITEM.match(line)) and m.group(1) in wanted]
    return REQUEST.format(request=typed.strip(), heading="The outcomes to test, each one the person asked for:",
                          outcome="\n".join(lines),
                          constraints=review.section(intent, "Constraints"), where=where)


def crashes(typed: str) -> set[str]:
    """The exceptions the request itself names, like the IndexError in a traceback it quotes."""
    return set(NAMED.findall(typed)) - NEVER


def hangs(typed: str) -> bool:
    """Whether the request describes a hang: it never returns, loops forever, freezes."""
    return bool(HANGS.search(typed))


def limited(text: bytes) -> bytes:
    """Reticle's file as Parallax runs it: with each test's time limit appended."""
    return text + LIMIT.format(seconds=SECONDS, growth=GROWTH, gb=GROWTH // 1024 ** 3).encode()


def hung(message: str) -> str | None:
    """What the limit said, if a test failed on it: "still running after 60 s, so it was stopped"."""
    return message.split(":", 1)[1].strip() if _exception(message) == HANG else None


CODE_START = re.compile(r"^(import |from |#|@|def |class |\"\"\"|\'\'\'|[A-Za-z_]\w*\s*=)")


def code_of(reply: str) -> str:
    """The Python file in Reticle's reply, prose before or after it removed: a fenced block if there
    is one; else from the first line that starts code, trimmed from the end until it parses."""
    import ast
    fenced = re.findall(r"^```(?:python|py)?[ \t]*\n(.*?)^```", reply, re.M | re.S)
    if fenced:
        return max(fenced, key=len).strip() + "\n"
    lines = reply.strip().splitlines()
    start = next((i for i, line in enumerate(lines) if CODE_START.match(line)), 0)
    body = lines[start:]
    for end in range(len(body), 0, -1):
        text = "\n".join(body[:end])
        try:
            ast.parse(text)
            return text.strip() + "\n"
        except SyntaxError:
            continue
    return reply.strip() + "\n"  # nothing parses: it won't load on the base, and is dropped as weak


def placement(p) -> str:
    """Where Reticle's file sits when it runs: in the folder the repo's own tests live in (the plan's
    first test's, else tests/ or test/), so it loads exactly as one of them would, with that folder's
    conftest and helpers importable. The root only when the repo has no such folder."""
    files = tree.files_in(p.worktree, p.task["base"])
    dirs = [str(PurePosixPath(t.split("::", 1)[0]).parent) for t in ((p.plan or {}).get("tests") or [])]
    for d in [*dirs, "tests", "test"]:
        if d not in ("", ".") and any(f.startswith(d + "/") for f in files):
            return f"{d}/{RUN_NAME}"
    return RUN_NAME


def placed(project: Project, task_id: str) -> str:
    """Where this attempt's kept tests run: where they ran on the base (older records: the root)."""
    rec = recorded(project, task_id)
    return (rec["data"].get("placed") if rec else None) or FILE


def node(case: dict, path: str = FILE) -> str:
    """A test's pytest id in the file, class included: tests/test_reticle_outcomes.py::SomeTest::test_outcome_1_x.
    A method of a class is only found by its full id (seen live: pathspec-77's unittest classes)."""
    stem = PurePosixPath(path).name.removesuffix(".py")
    parts = case.get("classname", "").split(".")
    classes = parts[parts.index(stem) + 1:] if stem in parts else []
    return "::".join([path, *classes, case["name"]])


def judge(cases: list[dict], outcomes: list[str], wanted: list[str] | None = None,
          named: set[str] = frozenset(), hang: bool = False, path: str = FILE) -> tuple[list[dict], list[dict]]:
    """(kept, weak) from the tests' run on the base commit. wanted: the asked outcomes (all by
    default); named: exceptions the request shows, whose failure counts like an assertion's;
    hang: the request describes a hang, so a test that hangs on the base counts too."""
    wanted = outcomes if wanted is None else wanted
    keep, weak = [], []
    for c in cases:
        if c["file"] != path and c["outcome"] != "error":  # a collection error names no class, so no file: it's ours
            continue
        m = NAME.match(c["name"])
        if c["outcome"] == "error" and not m:
            weak.append({"name": c["name"], "why": f"the file doesn't load on the base ({c['message'] or 'collection error'})"})
        elif not m or m.group(1) not in outcomes:
            weak.append({"name": c["name"], "why": "it names no outcome in the intent"})
        elif m.group(1) not in wanted:
            weak.append({"name": c["name"], "why": "it tests an outcome Focus inferred, not one you asked for"})
        elif c["outcome"] == "pass":
            weak.append({"name": c["name"], "why": "it passes on the base, so it doesn't show the problem"})
        elif c["outcome"] == "error":
            weak.append({"name": c["name"], "why": f"it errors before its assertion: {c['message']}"})
        elif c["outcome"] == "skip":
            weak.append({"name": c["name"], "why": "it skips on the base"})
        elif hung(c["message"]) and not hang:
            weak.append({"name": c["name"], "why": f"it hangs on the base ({hung(c['message'])}), "
                                                   "and the request describes no hang"})
        elif hung(c["message"]):
            keep.append({"node": node(c, path), "name": c["name"], "outcome": m.group(1), "base_message": c["message"]})
        elif not c["message"].startswith(KEEPS) and not WAITED.match(c["message"]) and _exception(c["message"]) not in named:
            kind = _exception(c["message"]) or "an error"
            said = " ".join(c["message"].split(":", 1)[-1].split())[:140]
            weak.append({"name": c["name"], "why": f"it fails on {kind}, not an assertion, a wait for the behavior, or a "
                                                   f"crash the request shows" + (f" ({said})" if said else "")})
        else:
            keep.append({"node": node(c, path), "name": c["name"], "outcome": m.group(1),
                         "base_message": c["message"]})
    return keep, weak


def _exception(message: str) -> str:
    """The exception's bare name in a failure message: "json.decoder.JSONDecodeError: x" -> JSONDecodeError."""
    return message.split(":", 1)[0].strip().rsplit(".", 1)[-1]


def _run(project: Project, p, treeish: str, text: bytes, path: str, where: str, runner=None) -> testrun.Results:
    plan = {"tests": [path], "outside_reads": [], "domains": []}
    results, _ = testrun.run(p.worktree, p.task["base"], treeish, plan, p.home / where, p.venv, build.scrubbed_env(p.venv),
                             project.policy.check["test_command"], runner, overlay={path: limited(text)})
    return results


def write(project: Project, task_id: str, p, writer=None, runner=None) -> str:
    """Once per attempt, before Maker. Returns "kept", "none" (no test was worth keeping), "failed",
    "done" (already ran this attempt) or "off". Never stops the task: without tests, it builds as before."""
    cfg = settings(project)
    if not cfg["enabled"]:
        return "off"
    if recorded(project, task_id):
        return "done"
    left = costs.budget(project, task_id, p.plan)[1]
    limit = round(min(float(cfg["max_usd"]), left), 2)
    if limit <= 0:
        project.ledger.append("reticle.failed", "parallax", "no budget left for Reticle", task=task_id)
        return "failed"
    base = p.home / "reticle" / "base"  # the base commit, never the worktree: it may hold earlier work
    shutil.rmtree(base.parent, ignore_errors=True)
    tree.export(p.worktree, p.task["base"], base)
    installs.place(p.venv, base)
    intent = lifecycle._read(project, task_id, "intent")
    typed = project.task(task_id)["goal"]  # the person's request as typed; in an eval, the issue
    if not asked(intent):
        project.ledger.append("reticle.failed", "parallax", "the intent marks no outcome as asked, so Reticle had nothing to test",
                              task=task_id)
        return "failed"
    project.ledger.append("reticle.started", "parallax", "", task=task_id)  # the UI shows Reticle at work
    fn = make_permission_fn(project, task_id, base, read_only=True)
    where = placement(p)
    try:
        res = (writer or WRITER)(limit, cfg["model"]).run(request(intent, typed, where), base, fn, stage="reticle",
                                                          env={TASK_ENV: task_id, ROOT_ENV: str(project.root)})
    except Exception as err:  # recorded, never retried silently; the build goes on without it
        res = AgentResult("error", f"{type(err).__name__}: {err}")
    common = dict(task=task_id, cost_usd=res.cost_usd, model=cfg["model"])
    if res.status != "done" or not (res.summary or "").strip():
        project.ledger.append("reticle.failed", "reticle", lint.one_sentence(res.summary or res.status), **common)
        return "failed"
    text = code_of(res.summary).encode()
    results = _run(project, p, p.task["base"], text, where, "reticle-base", runner)
    keep, weak = judge(results.cases, lint.outcomes_of(intent), targets(intent), crashes(typed), hangs(typed), where)
    if not results.cases:
        weak.append({"name": where, "why": f"its tests couldn't run on the base (exit {results.exit}): "
                                          f"{(results.tail.splitlines() or ['no output'])[-1][:200]}"})
    path = stored(project, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    rel = drafts.logical(project.root, path)
    project.ledger.append("reticle.recorded", "reticle",
                          lint.one_sentence(f"{len(keep)} tests kept, {len(weak)} weak ones dropped"),
                          file=rel, sha=hashlib.sha256(text).hexdigest(), kept=keep, weak=weak, placed=where, **common)
    return "kept" if keep else "none"


def tampered(project: Project, task_id: str) -> bool:
    rec = recorded(project, task_id)
    if not rec or rec["kind"] != "reticle.recorded":
        return False
    path = drafts.resolve(project.root, rec["data"]["file"])
    return not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != rec["data"]["sha"]


class Unrun(Exception):
    """Kept tests that didn't run, and not because of the change: Parallax's problem, never Maker's."""


def check(project: Project, task_id: str, p, treeish: str, runner=None) -> tuple[testrun.Results, list[dict]] | None:
    """Run Reticle's file on a reviewed tree and judge its kept tests. None if there are none.
    Returns (results, failing kept tests). The whole file runs, and each kept test is found by its id.

    A file that won't load on this tree fails every kept test with the load error: the change broke
    what the outcome's tests import. A kept test missing for any other reason raises Unrun."""
    tests = kept(project, task_id)
    if not tests:
        return None
    text = stored(project, task_id).read_bytes()
    where = placed(project, task_id)
    results = _run(project, p, treeish, text, where, "reticle-check", runner)
    ran = {node(c, where): c for c in results.cases if c["file"] == where and NAME.match(c["name"])}
    broken = next((c for c in results.cases if c["outcome"] == "error" and not NAME.match(c["name"])), None)
    missing = [t for t in tests if t["node"] not in ran]
    if missing and broken is None:
        raise Unrun(f"{len(missing)} of Reticle's {len(tests)} kept tests didn't run (exit {results.exit}), "
                    f"{missing[0]['node']} among them")
    failing, steps = [], steps_of(text)
    tests = [{**t, "steps": steps.get(t["name"], "")} for t in tests]
    for t in tests:
        c = ran.get(t["node"])
        if c is None:
            failing.append({**t, "message": f"its tests don't load on this change: {broken['message']}"})
        elif c["outcome"] != "pass":
            said = hung(c["message"])
            failing.append({**t, "message": f"it hangs on this change: {said}" if said else c["message"]})
    return results, failing


def steps_of(text: str | bytes) -> dict[str, str]:
    """Each test's docstring, by name: the steps it takes and what it expects, in Reticle's words.
    The one part of the file Maker may see. A file that doesn't parse has none."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {}
    return {n.name: " ".join(doc.split()) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and NAME.match(n.name) and (doc := ast.get_docstring(n))}


def finding(t: dict) -> str:
    """What Maker hears: the outcome, the steps the test took and what it expected (its docstring), and
    the failure message. Never the test's code (fb461d: Maker saw only "expected 6c4127", and couldn't
    tell the test opened a different link format)."""
    steps = (t.get("steps") or "").rstrip(".")
    if not steps:
        return f"blocker: a test of outcome {t['outcome']} that you can't see fails: {t['message']}"
    return (f"blocker: a test of outcome {t['outcome']} that you can't see fails. It {steps[0].lower() + steps[1:]}. "
            f"It got: {t['message']}")


def _default_writer(limit: float, model: str):
    from .agents.claude import ClaudeAgent
    return ClaudeAgent(model=model, max_budget_usd=limit)


WRITER = _default_writer  # tests replace this; they never call a model
