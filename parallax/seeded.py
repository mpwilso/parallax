"""`parallax eval --seeded`: catch rates from broken versions of the maintainers' real fix. No Maker.

Per case:
1. Focus drafts the intent from the issue (as `parallax do` would), and Reticle writes its tests,
   asked and inferred outcomes both, each kept test marked with its kind.
2. Code, never a model, breaks the maintainers' merged fix: flip one comparison, shift one integer
   constant, or undo one hunk, one change at a time, on the fix's own changed lines. A version counts
   only if it fails the hidden tests; up to three per case.
3. Each broken version gets Second Eye's exact normal input (the intent's outcomes and constraints,
   REVIEW.md, and the version's diff) and a run of Reticle's kept tests. Each scores catch or miss.
   Second Eye scores on findings about behavior: no version here has tests (they're the hidden
   ones), so a finding that only says a test is missing would flag every version and every real fix.
4. The real fix gets both too: anything they flag there is a false alarm by definition.
The hidden test files are never in any diff Second Eye sees, and never in Reticle's input.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from . import build, evals, installs, lifecycle, lint, reticle, review, sandbox
from .core import POLICY_FILE, ParallaxError, Project

MAX_VERSIONS = 3
MAX_TRIES = 12            # candidate versions checked against the hidden tests per case
PER_CASE = 1.0            # the ceiling a seeded case runs under: no Maker, so far below a task's
# estimated dollars per case, from this repo's eval ledgers: an intent draft about $0.05, Reticle
# $0.02 to $0.08, Second Eye $0.01 to $0.06 a call, up to four calls
ESTIMATE = 0.25
FLIPS = {"<=": "<", ">=": ">", "<": "<=", ">": ">=", "==": "!=", "!=": "=="}
COMPARISON = re.compile(r"(?<![<>=!\-])(<=|>=|==|!=|<(?![<=])|>(?![>=]))(?!=)")
INTEGER = re.compile(r"(?<![\w.])(\d+)(?![\w.])")
# a finding that only says a test is missing: it says one is absent, and every sentence is about tests
ABSENT = re.compile(r"\b(no|missing|without|lacks?|adds? no|has no|doesn't add|does not add|nothing)\b[^.]*\btests?\b"
                    r"|\btests?\b[^.]*\b(missing|absent|not (added|included|in the diff|changed))\b", re.I)
ABOUT_TESTS = re.compile(r"\btest|regression|\bcover|unnoticed|\bverif|would fail|\breverted\b"
                         r"|nothing (shows|proves)|\bnone (is|are) (present|there)\b", re.I)


def _git(repo: Path, *args: str, env: dict | None = None, data: bytes | None = None) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=data,
                         env={**os.environ, **(env or {})})
    if out.returncode != 0:
        raise ParallaxError(f"git {' '.join(args[:2])} failed: {out.stderr.decode(errors='replace').strip()[-300:]}")
    return out.stdout.decode(errors="replace").strip()


def fix_files(cache: Path, case) -> dict[str, bytes | None]:
    """What the maintainers' fix changed, except the hidden tests: path -> content at the fix (None: deleted)."""
    out = {}
    for line in _git(cache, "diff", "--name-status", "--no-renames", case.base, case.fix).splitlines():
        status, path = line.split("\t", 1)
        if path in case.tests:
            continue
        out[path] = None if status.startswith("D") else subprocess.run(
            ["git", "-C", str(cache), "show", f"{case.fix}:{path}"], capture_output=True, check=True).stdout
    return out


def _hunks(cache: Path, case, path: str) -> list[tuple[int, int, int, int]]:
    """(base start, base count, fix start, fix count) for each hunk of one file, no context."""
    diff = _git(cache, "diff", "-U0", case.base, case.fix, "--", path)
    out = []
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        a, b, c, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
        out.append((a, b, c, d))
    return out


def test_only(finding: str) -> bool:
    """Whether a finding only says a test is missing, with nothing about what the code does."""
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", finding.strip()) if x]
    return bool(ABSENT.search(finding)) and all(ABOUT_TESTS.search(x) for x in sentences)


def second_eye_scored(findings: list, blocking: set) -> dict:
    """Second Eye's blocking findings, split: the ones about behavior count, the missing-test ones don't."""
    said = [f"{f.severity}: {' '.join(f.text.split())[:240]}" for f in findings if f.severity in blocking]
    counted = [x for x, f in zip(said, [f for f in findings if f.severity in blocking]) if not test_only(f.text)]
    return {"flags": bool(counted), "findings": counted, "test_only": [x for x in said if x not in counted]}


def _code_part(line: str) -> str:
    """The line without a trailing comment or string contents, so a mutation only touches code."""
    line = re.sub(r"(['\"]).*?\1", lambda m: "_" * len(m.group(0)), line)
    return line.split("#", 1)[0]


def candidates(cache: Path, case) -> list[tuple[str, dict[str, bytes | None]]]:
    """Broken versions of the fix, by code: (what was broken, the fix's files with that one change).
    Interleaved by kind, so the first few try every kind."""
    files = fix_files(cache, case)
    flips, consts, drops = [], [], []
    for path, data in files.items():
        if data is None or not path.endswith(".py"):
            continue
        base = subprocess.run(["git", "-C", str(cache), "show", f"{case.base}:{path}"], capture_output=True).stdout
        fixed, old = data.decode().splitlines(keepends=True), base.decode().splitlines(keepends=True)
        hunks = _hunks(cache, case, path)
        for a, b, c, d in hunks:
            for i in range(c - 1, c - 1 + d) if d else []:
                code = _code_part(fixed[i])
                for m in COMPARISON.finditer(code):
                    line = fixed[i][:m.start()] + FLIPS[m.group(1)] + fixed[i][m.end():]
                    flips.append((f"flip {m.group(1)} to {FLIPS[m.group(1)]} at {path}:{i + 1}",
                                  {**files, path: "".join(fixed[:i] + [line] + fixed[i + 1:]).encode()}))
                for m in INTEGER.finditer(code):
                    line = fixed[i][:m.start()] + str(int(m.group(1)) + 1) + fixed[i][m.end():]
                    consts.append((f"change {m.group(1)} to {int(m.group(1)) + 1} at {path}:{i + 1}",
                                   {**files, path: "".join(fixed[:i] + [line] + fixed[i + 1:]).encode()}))
            if len(hunks) > 1 or len(files) > 1:  # undoing the only change is just the base: not a fix at all
                kept = fixed[:c - 1 if d else c] + old[a - 1:a - 1 + b] + fixed[(c - 1 if d else c) + d:]
                drops.append((f"undo the hunk at {path}:{c}", {**files, path: "".join(kept).encode()}))
    out, seen = [], set()
    for name, version in _interleave(flips, consts, drops):
        path = next(p for p in version if version[p] != files.get(p))
        text = version[path]
        try:
            ast.parse(text)
        except SyntaxError:
            continue
        if text not in seen:
            seen.add(text)
            out.append((name, version))
    return out


def _interleave(*lists):
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x):
                yield x[i]


def tree_with(repo: Path, base: str, files: dict[str, bytes | None], index: Path) -> str:
    """A git tree: the base commit with these files replaced (None: removed). Written to repo's objects."""
    env = {"GIT_INDEX_FILE": str(index)}
    index.unlink(missing_ok=True)
    _git(repo, "read-tree", base, env=env)
    for path, data in files.items():
        if data is None:
            _git(repo, "update-index", "--force-remove", path, env=env)
        else:
            blob = _git(repo, "hash-object", "-w", "--stdin", data=data)
            _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
    return _git(repo, "write-tree", env=env)


def _draft_intent(project: Project, task_id: str, drafter_for) -> str:
    problems: dict[str, list[str]] = {}
    for _ in range(3):
        if not lifecycle.draft(project, task_id, ["intent"], drafter_for, problems):
            raise ParallaxError(f"Focus couldn't draft the intent: {lifecycle.state(project, task_id).failed}")
        text = lifecycle._read(project, task_id, "intent")
        found = [m for _, m in lint.lint_lifecycle(text, "intent")]
        if not found:
            return text
        problems = {"intent": found}
    raise ParallaxError(f"Focus's intent still failed lint: {'; '.join(problems['intent'])}")


def run_case(case, where: Path, policy, drafter_for, checker_for, runner=None) -> dict:
    started = time.monotonic()
    r: dict = {"case": case.id, "mode": "seeded", "pr": case.pr, "issue": case.issue}
    try:
        cache = evals.cache_repo(case)
        repo = where / "repo"
        evals.clone_at(cache, case.base, repo)  # no history past the base: nothing an agent reads holds the fix
        (repo / POLICY_FILE).write_text(evals.eval_policy(policy, case, PER_CASE, reticle=True, inferred=True), encoding="utf-8")
        project = Project.init(repo, actor="parallax")
        t = project.new_task(case.goal.strip(), intent=True)
        tid = t["task"]
        intent = _draft_intent(project, tid, drafter_for)
        home = sandbox.task_home(project.root, tid)
        home.mkdir(parents=True, exist_ok=True)
        venv = None
        command = evals.setup_command(case)
        if command:
            out, _ = installs.make(command, repo, t["base"], home / "venv", home / "setup-base")
            if out.returncode != 0:
                raise ParallaxError("setup failed: " + ((out.stderr or out.stdout).strip().splitlines() or ["no output"])[-1])
            venv = home / "venv"
        p = SimpleNamespace(worktree=Path(t["worktree"]), task=t, home=home, venv=venv,
                            plan={"budget_cap_usd": PER_CASE, "outside_reads": [], "domains": []})
        reticle.write(project, tid, p, runner=runner)
        kept = reticle.kept(project, tid)
        r["inferred"] = sorted(n for n, k in lint.outcome_kinds(intent).items() if k == "inferred")
        r["reticle_kept"] = {k: sum(t.get("kind") == k for t in kept) for k in ("asked", "inferred")}
        rec = reticle.recorded(project, tid)
        r["reticle_weak"] = [w["why"] for w in (rec["data"].get("weak") or [])] if rec else []

        hidden = evals.hidden_tests(case, cache)
        test_command = project.policy.check["test_command"]
        review_text = review.load(project.root)
        blocking = review.blocking(review_text)

        def judge(files: dict, n: int) -> dict:
            tree = tree_with(repo, t["base"], files, home / f"seeded-{n}.index")
            diff = _git(repo, "diff", "--binary", t["base"], tree, "--", ".", ":(exclude)docs/tasks/")
            rv = checker_for(PER_CASE, project.policy.check["model"]).check(review.brief(intent, review_text, "", diff))
            project.ledger.append("verdict.recorded", "checker", "; ".join(f.text for f in rv.findings), task=tid,
                                  stage="seeded", tree=tree, cost_usd=rv.cost_usd, verdict=rv.verdict)
            ran = reticle.check(project, tid, p, tree, runner)
            failing = ran[1] if ran else []
            se = second_eye_scored(rv.findings, blocking)
            return {"tree": tree, "second_eye_flags": se["flags"], "findings": se["findings"], "test_only": se["test_only"],
                    "reticle_asked": None if not r["reticle_kept"]["asked"] else bool(reticle.blocking(failing)),
                    "reticle_any": None if not kept else bool(failing),
                    "notes": sum(t.get("kind") == "inferred" for t in failing)}

        versions, tried = [], 0
        for name, files in candidates(cache, case):
            if len(versions) >= MAX_VERSIONS or tried >= MAX_TRIES:
                break
            tried += 1
            tree = tree_with(repo, t["base"], files, home / "probe.index")
            res = evals.run_hidden(Path(t["worktree"]), t["base"], tree, hidden, case.tests, home / f"hidden-{tried}",
                                   venv, test_command, runner)
            if not res.reported or res.ok:  # it must fail the hidden tests, having run them
                continue
            j = judge(files, len(versions))
            versions.append({"broken": name, "hidden": f"{res.passed} of {res.total} pass",
                             "second_eye": "catch" if j["second_eye_flags"] else "miss", "findings": j["findings"],
                             "test_only": j["test_only"],
                             "reticle": {None: "no test", True: "catch", False: "miss"}[j["reticle_asked"]],
                             "reticle_with_inferred": {None: "no test", True: "catch", False: "miss"}[j["reticle_any"]]})
        r["versions"] = versions
        real = judge(fix_files(cache, case), len(versions))
        r["real_fix"] = {"second_eye": "false alarm" if real["second_eye_flags"] else "right", "findings": real["findings"],
                         "test_only": real["test_only"],
                         "reticle": {None: "no test", True: "false alarm", False: "right"}[real["reticle_asked"]],
                         "inferred_notes": real["notes"]}
        r["cost_usd"] = round(sum(e["data"].get("cost_usd") or 0 for e in project.ledger.entries()), 4)
    except Exception as err:  # a crashed case is a result too
        r.update({"error": f"{type(err).__name__}: {err}"[:300]})
        r.setdefault("cost_usd", 0.0)
    r["seconds"] = round(time.monotonic() - started, 1)
    return r


def _default_drafter(project_policy):
    return lambda cap: build._drafter(cap, project_policy.draft["model"])


def run(project: Project, cases: list, budget: float, runner=None, say: Callable[[str], None] | None = None,
        drafter_for=None, checker_for=None, run_id: str | None = None) -> Path:
    """Every case under one hard total budget, each started only if its ceiling and the margin fit."""
    say = say or evals.say_now
    say(f"estimated ${ESTIMATE * len(cases):.2f} for {len(cases)} cases (about ${ESTIMATE:.2f} each: an intent, "
        f"Reticle, and up to {MAX_VERSIONS + 1} Second Eye calls; no Maker). the budget is ${budget:.2f}.")
    need = round(PER_CASE * (1 + evals.MARGIN), 2)
    run_id = run_id or uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc)
    out = project.root / evals.RESULTS_DIR / f"{now:%Y-%m-%d}-{run_id}-seeded"
    out.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "-C", str(project.root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    header = {"run": run_id, "mode": "seeded", "started": now.isoformat(timespec="seconds"), "parallax": head,
              "budget_usd": budget, "per_case_usd": PER_CASE, "cases": [c.id for c in cases],
              "fingerprint": evals.fingerprint.current(project.root, project.policy)}
    drafter_for = drafter_for or _default_drafter(project.policy)
    checker_for = checker_for or build._checker
    spent, done, stopped = 0.0, [], None
    for n, case in enumerate(cases, 1):
        if spent + need > budget:
            stopped = {"case": case.id, "why": f"${spent:.2f} spent, and the next case needs up to ${need:.2f}"}
            say(f"stopped before {case.id}: {stopped['why']} of the ${budget:.2f} budget.")
            break
        say(f"[{n}/{len(cases)}] {case.id}: breaking the real fix")
        r = run_case(case, evals.home() / "runs" / run_id / case.id, project.policy, drafter_for, checker_for, runner)
        spent = round(spent + (r.get("cost_usd") or 0), 4)
        (out / f"{case.id}.json").write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8")
        done.append(r)
        v = r.get("versions", [])
        say(f"[{n}/{len(cases)}] {case.id}: " + (r["error"] if "error" in r else
            f"{len(v)} broken versions; Second Eye caught {sum(x['second_eye'] == 'catch' for x in v)}, Reticle "
            f"{sum(x['reticle'] == 'catch' for x in v)}; on the real fix Second Eye {r['real_fix']['second_eye']}, Reticle "
            f"{r['real_fix']['reticle']}") + f", ${r.get('cost_usd') or 0:.2f} (total ${spent:.2f})")
    header.update({"finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "spent_usd": spent,
                   "done": [r["case"] for r in done], "stopped": stopped})
    (out / "run.json").write_text(json.dumps(header, indent=1) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(summary(project.root, out, header, done), encoding="utf-8")
    return out


def rates(results: list[dict]) -> dict:
    v = [x for r in results for x in r.get("versions", [])]
    real = [r["real_fix"] for r in results if "real_fix" in r]
    return {
        "versions": len(v),
        "second_eye": sum(x["second_eye"] == "catch" for x in v),
        "reticle": sum(x["reticle"] == "catch" for x in v),
        "reticle_tested": sum(x["reticle"] != "no test" for x in v),
        "reticle_with_inferred": sum(x["reticle_with_inferred"] == "catch" for x in v),
        "real": len(real),
        "second_eye_false": sum(x["second_eye"] == "false alarm" for x in real),
        "reticle_false": sum(x["reticle"] == "false alarm" for x in real),
        "notes_on_real": sum(x["inferred_notes"] for x in real),
    }


def summary(root: Path, out: Path, header: dict, results: list[dict]) -> str:
    rel = out.relative_to(root).as_posix()
    c = rates(results)
    left = [x for x in header["cases"] if x not in header["done"]]
    crashed = [r["case"] for r in results if "error" in r]
    bottom = f"Of {c['versions']} broken versions, Second Eye caught {c['second_eye']} and Reticle {c['reticle']}."
    why = "the run stopped before " + ("it" if len(left) == 1 else "them")  # the budget, or an interruption: run.json says
    gaps = [f"{', '.join(left)}: {why}"] if left else []
    gaps += [f"{', '.join(crashed)}: crashed"] if crashed else []
    found = [f"Second Eye caught {c['second_eye']} of {c['versions']} broken versions ({rel}/run.json:1)",
             f"Reticle's tests of asked outcomes caught {c['reticle']} of {c['versions']}; {c['reticle_tested']} had a test "
             f"({rel}/run.json:1)",
             f"with its inferred-outcome tests too, Reticle caught {c['reticle_with_inferred']} of {c['versions']} ({rel}/run.json:1)",
             f"on the {c['real']} real fixes: Second Eye {c['second_eye_false']} false alarms, Reticle {c['reticle_false']}, "
             f"and {c['notes_on_real']} inferred-outcome notes ({rel}/run.json:1)"]
    details = [f"Run {header['run']} on parallax {header['parallax']}, budget ${header['budget_usd']:.2f}, "
               f"${header['spent_usd']:.2f} spent."]
    if header.get("second_eye_only"):
        details.append("Second Eye alone, scored on findings about behavior. Each case's versions, intent and "
                       "Reticle results come from the seeded run named on its line.")
    for r in results:
        if "error" in r:
            details.append(f"{r['case']}: crashed: {r['error']}")
            continue
        vs = "; ".join(f"{x['broken']}: Second Eye {x['second_eye']}, Reticle {x['reticle']}" for x in r["versions"]) or "no broken version failed the hidden tests"
        src = f" From run {r['second_eye_from']}." if r.get("second_eye_from") else ""
        details.append(f"{r['case']}: {vs}. Real fix: Second Eye {r['real_fix']['second_eye']}, Reticle "
                       f"{r['real_fix']['reticle']}, {r['real_fix']['inferred_notes']} notes. ${r['cost_usd']:.2f}.{src}")
    text = lint.report("FYI", bottom, "; ".join(gaps) or "nothing", "you read Details, then decide on Reticle.", found, details)
    return lint.fit(text, root=root)[0] + "\n"
