"""`parallax eval --seeded --second-eye-only`: Second Eye again on the versions a seeded run made.

For each case, the newest seeded run that finished it gives what stays fixed: Focus's intent from
that run's scratch repo, and Reticle's results, copied as they are. REVIEW.md is today's general
template, the one an eval gets, so a rerun measures Second Eye's input as it is now. The broken
versions are made again by code (seeded.candidates is deterministic) and picked by name, and the
real fix is the same. Only Second Eye is called, with its exact normal input, and it's scored on
findings about behavior (seeded.second_eye_scored). No Focus, no Reticle, no hidden tests run.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import build, evals, lifecycle, review, seeded
from .core import ParallaxError, Project

CALL = 0.25  # the most one Second Eye call may spend


def source(root: Path, case_id: str) -> tuple[str, dict] | None:
    """(run id, result) of the newest seeded run that finished this case, not counting reruns of Second Eye."""
    found = []
    for header_path in (root / evals.RESULTS_DIR).glob("*-seeded/run.json"):
        header = json.loads(header_path.read_text())
        if header.get("second_eye_only") or case_id not in header.get("done", []):
            continue
        r = json.loads((header_path.parent / f"{case_id}.json").read_text())
        if "error" not in r:
            found.append((header.get("finished") or header["started"], header["run"], r))
    return max(found, key=lambda x: x[0])[1:] if found else None


def rescore_case(case, run_id: str, r: dict, checker_for) -> dict:
    repo = evals.home() / "runs" / run_id / case.id / "repo"
    if not repo.exists():
        raise ParallaxError(f"run {run_id}'s scratch repo for {case.id} is gone, so its intent is too")
    project = Project(repo)
    [tid] = list(project.tasks())
    base = project.task(tid)["base"]
    intent = lifecycle._read(project, tid, "intent")
    review_text = review.TEMPLATE
    blocking = review.blocking(review_text)
    cache = evals.cache_repo(case)
    made = dict(seeded.candidates(cache, case))
    home = evals.home() / "runs" / run_id / case.id / "second-eye"
    home.mkdir(parents=True, exist_ok=True)
    cost = 0.0

    def judge(files: dict) -> dict:
        nonlocal cost
        tree = seeded.tree_with(repo, base, files, home / "index")
        diff = seeded._git(repo, "diff", "--binary", base, tree, "--", ".", ":(exclude)docs/tasks/")
        rv = checker_for(CALL, project.policy.check["model"]).check(review.brief(intent, review_text, "", diff))
        cost += rv.cost_usd or 0
        return seeded.second_eye_scored(rv.findings, blocking)

    out = {**r, "second_eye_from": run_id, "reticle_from": run_id, "versions": []}
    for v in r["versions"]:
        if v["broken"] not in made:
            raise ParallaxError(f"{case.id}: the version \"{v['broken']}\" can't be made again")
        se = judge(made[v["broken"]])
        out["versions"].append({**v, "second_eye": "catch" if se["flags"] else "miss", "findings": se["findings"],
                                "test_only": se["test_only"]})
    se = judge(seeded.fix_files(cache, case))
    out["real_fix"] = {**r["real_fix"], "second_eye": "false alarm" if se["flags"] else "right",
                       "findings": se["findings"], "test_only": se["test_only"]}
    out["cost_usd"] = round(cost, 4)
    return out


def run(project: Project, cases: list, budget: float, say: Callable[[str], None] | None = None,
        checker_for=None, run_id: str | None = None) -> Path:
    """Second Eye on every case's versions and real fix, under one budget. Returns the run's folder."""
    say = say or evals.say_now
    need = round(CALL * (seeded.MAX_VERSIONS + 1) * (1 + evals.MARGIN), 2)
    run_id = run_id or uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc)
    out = project.root / evals.RESULTS_DIR / f"{now:%Y-%m-%d}-{run_id}-seeded"
    out.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "-C", str(project.root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    header = {"run": run_id, "mode": "seeded", "second_eye_only": True, "started": now.isoformat(timespec="seconds"),
              "parallax": head, "budget_usd": budget, "per_case_usd": need, "cases": [c.id for c in cases],
              "fingerprint": evals.fingerprint.current(project.root, project.policy)}
    checker_for = checker_for or build._checker
    say(f"Second Eye only, on the versions earlier seeded runs made: up to {seeded.MAX_VERSIONS + 1} calls a case, "
        f"${CALL:.2f} at most each. the budget is ${budget:.2f}.")
    spent, done, stopped = 0.0, [], None
    for n, case in enumerate(cases, 1):
        if spent + need > budget:
            stopped = {"case": case.id, "why": f"${spent:.2f} spent, and the next case needs up to ${need:.2f}"}
            say(f"stopped before {case.id}: {stopped['why']} of the ${budget:.2f} budget.")
            break
        found = source(project.root, case.id)
        if found is None:
            r = {"case": case.id, "mode": "seeded", "error": "no seeded run finished this case", "cost_usd": 0.0}
        else:
            try:
                r = rescore_case(case, *found, checker_for)
            except Exception as err:  # a crashed case is a result too
                r = {"case": case.id, "mode": "seeded", "error": f"{type(err).__name__}: {err}"[:300], "cost_usd": 0.0}
        spent = round(spent + (r.get("cost_usd") or 0), 4)
        (out / f"{case.id}.json").write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8")
        done.append(r)
        v = r.get("versions", [])
        say(f"[{n}/{len(cases)}] {case.id}: " + (r["error"] if "error" in r else
            f"Second Eye caught {sum(x['second_eye'] == 'catch' for x in v)} of {len(v)}; on the real fix "
            f"{r['real_fix']['second_eye']}") + f", ${r.get('cost_usd') or 0:.2f} (total ${spent:.2f})")
    header.update({"finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "spent_usd": spent,
                   "done": [r["case"] for r in done], "stopped": stopped})
    (out / "run.json").write_text(json.dumps(header, indent=1) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(seeded.summary(project.root, out, header, done), encoding="utf-8")
    return out
