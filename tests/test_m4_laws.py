from pathlib import Path

import pytest

from fakes import FakeChecker, FakeConductor, ScriptedAgent
from parallax.agents.base import Law
from parallax.agents.claude import _to_report
from parallax.core import POLICY_FILE, ParallaxError, Project
from parallax.evidence import raise_promotions
from parallax.inbox import resolve_item
from parallax.mission import MISSION_FILE, load
from parallax.pulse import pulse
from parallax.runner import run_task

POLICY = """\
[actions]
"fs.read"   = "allow"
"fs.write"  = "ask"
"shell.run" = "ask"
"""
MISSION = "# Mission\n\n## who\nA maintainer.\n\n## how\nKeep diffs small.\n\n## what\n- flag repeated rejections\n"
TEXT = "Never edit files under tests/ without asking first."


def setup(repo: Path) -> Project:
    (repo / POLICY_FILE).write_text(POLICY)
    (repo / MISSION_FILE).write_text(MISSION)
    return Project.init(repo)


def reject(proj: Project, n: int, action="fs.write", detail="tests/test_calc.py", reason="don't touch the tests") -> list[str]:
    ids = []
    for i in range(n):
        t = proj.new_task(f"task {i}")
        req = proj.check(t["task"], action, detail)["entry"]
        ids.append(proj.resolve(req["id"], False, reason)["id"])
    return ids


def run_pulse(proj: Project, laws) -> dict:
    return pulse(proj, FakeConductor(laws=laws), launch=lambda project, tid: 0)


def raised_laws(proj: Project) -> list[dict]:
    return [e for e in proj.ledger.entries() if e["kind"] == "law.raised"]


def test_a_law_with_real_evidence_reaches_the_inbox(repo):
    proj = setup(repo)
    ids = reject(proj, 3)
    seen = {}

    def conductor_laws(snapshot):
        seen["snapshot"] = snapshot
        return [Law(TEXT, ids)]

    res = run_pulse(proj, conductor_laws)
    assert all(i in seen["snapshot"] for i in ids)
    assert "don't touch the tests" in seen["snapshot"] and "- default: fs.read=allow" in seen["snapshot"]
    assert f"law proposed: {TEXT}" in res["findings"]
    [law] = [e for e in proj.inbox() if e["kind"] == "law.raised"]
    assert law["actor"] == "conductor" and law["data"]["evidence"] == ids and law["data"]["rule"] is None


def _promotion_rejection(proj: Project) -> str:
    t = proj.new_task("p")
    for _ in range(10):
        req = proj.check(t["task"], "shell.run", "pytest -q")["entry"]
        proj.resolve(req["id"], True, "fine")
    [p] = raise_promotions(proj)
    return resolve_item(proj, p["id"], False, "not yet").entry["id"]


@pytest.mark.parametrize("case", ["fake id", "too few", "loosens", "not tighter", "readonly", "promotion evidence"])
def test_laws_that_dont_hold_up_are_dropped(repo, case):
    proj = setup(repo)
    ids = reject(proj, 3)
    law = {
        "fake id": Law(TEXT, ids[:2] + ["deadbeef"]),
        "too few": Law(TEXT, ids[:2]),
        "loosens": Law(TEXT, ids, {"profile": "default", "action": "fs.write", "key": None, "ruling": "allow"}),
        "not tighter": Law(TEXT, ids, {"profile": "default", "action": "fs.write", "key": None, "ruling": "ask"}),
        "readonly": Law(TEXT, ids, {"profile": "readonly", "action": "fs.write", "key": None, "ruling": "deny"}),
        "promotion evidence": None,
    }[case]
    if law is None:
        law = Law(TEXT, ids[:2] + [_promotion_rejection(proj)])
    res = run_pulse(proj, [law])
    assert raised_laws(proj) == []
    assert any(f.startswith("dropped a law from the conductor") for f in res["findings"])


def test_each_rejection_backs_one_law(repo):
    proj = setup(repo)
    ids = reject(proj, 3)
    run_pulse(proj, [Law(TEXT, ids)])
    run_pulse(proj, [Law("Ask before editing tests.", ids)])
    [first] = raised_laws(proj)  # the second cited evidence already in use
    resolve_item(proj, first["id"], False, "too broad")
    run_pulse(proj, [Law("Ask before editing tests.", ids)])
    assert len(raised_laws(proj)) == 2  # freed by the rejection


def test_approving_a_prose_law_updates_the_mission(repo):
    proj = setup(repo)
    run_pulse(proj, [Law(TEXT, reject(proj, 3))])
    [law] = raised_laws(proj)
    out = resolve_item(proj, law["id"], True, "yes, that's the rule")
    assert out.changed == "mission.md updated: law added to how"

    m = load(repo)
    assert m.how == f"Keep diffs small.\n- {TEXT}" and m.who == "A maintainer."
    assert f"<!-- ledger {law['id']} -->" in (repo / MISSION_FILE).read_text()

    tid = proj.new_task("add mul()")["task"]
    agent = ScriptedAgent()
    run_task(proj, tid, agent, FakeChecker())
    assert TEXT in agent.goals[-1][1]  # makers get it on their next run


def test_approving_a_rule_law_writes_the_policy(repo):
    proj = setup(repo)
    ids = reject(proj, 3, action="shell.run", detail="curl http://example.com", reason="no network from agents")
    rule = {"profile": "default", "action": "shell.run", "key": "curl http://example.com", "ruling": "deny"}
    run_pulse(proj, [Law("Agents don't fetch URLs with curl.", ids, rule)])
    [law] = raised_laws(proj)
    out = resolve_item(proj, law["id"], True, "agreed")
    assert out.changed == 'policy updated: exact shell.run "curl http://example.com" = deny'
    assert proj.policy.ruling("shell.run", key="curl http://example.com") == "deny"
    assert proj.policy.ruling("shell.run", key="pytest -q") == "ask"  # only that command


def test_a_rule_law_that_no_longer_tightens_is_refused(repo):
    proj = setup(repo)
    rule = {"profile": "default", "action": "shell.run", "key": None, "ruling": "ask"}
    (repo / POLICY_FILE).write_text(POLICY.replace('"shell.run" = "ask"', '"shell.run" = "allow"'))
    proj.reload_policy()
    run_pulse(proj, [Law("Ask before running shell commands.", reject(proj, 3), rule)])
    [law] = raised_laws(proj)
    (repo / POLICY_FILE).write_text(POLICY.replace('"shell.run" = "ask"', '"shell.run" = "deny"'))  # you tightened it
    proj.reload_policy()
    with pytest.raises(ParallaxError, match="no longer applies"):
        resolve_item(proj, law["id"], True, "yes")
    assert law["id"] in [e["id"] for e in proj.inbox()]
    assert '"shell.run" = "deny"' in (repo / POLICY_FILE).read_text()  # not loosened back to ask


def test_conductor_law_fields_parse():
    report = _to_report({"laws": [
        {"text": "prose", "evidence": ["a"], "rule_profile": "", "rule_action": "", "rule_key": "", "rule_ruling": ""},
        {"text": "rule", "evidence": ["b"], "rule_profile": "", "rule_action": "net.fetch", "rule_key": "",
         "rule_ruling": "deny"},
    ]})
    assert report.laws[0].rule is None
    assert report.laws[1].rule == {"profile": "default", "action": "net.fetch", "key": None, "ruling": "deny"}
