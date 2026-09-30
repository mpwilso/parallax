"""The Ask box: a question about one task, answered from its record only. Read-only: it can't
change the task or start anything, and it spends from its own budget."""
import asyncio
import json
import types
from pathlib import Path

import pytest

from parallax import ask, costs
from parallax.core import ParallaxError, POLICY_FILE
from test_accept import ready
from test_check import kinds


class FakeAsker:
    def __init__(self, reply=None, cost=0.02):
        self.reply = reply or {"answer": "Second Eye passed it [ledger abc].", "sources": ["ledger abc"]}
        self.cost, self.calls = cost, []

    def __call__(self, limit, model):
        self.limit, self.model = limit, model
        return self

    def ask(self, system, prompt, schema):
        self.calls.append((system, prompt, schema))
        return self.reply, self.cost


def test_an_answer_comes_from_the_tasks_record_is_recorded_and_spends_apart_from_the_cap(repo):
    proj, tid, _ = ready(repo)
    status, spent, before = proj.task(tid)["status"], costs.spent(proj, tid), len(proj.ledger.entries())
    asker = FakeAsker()
    out = ask.answer(proj, tid, "  did  Second Eye pass it? ", asker)
    assert out == {"answer": "Second Eye passed it [ledger abc].", "sources": ["ledger abc"], "cost_usd": 0.02}
    system, prompt, schema = asker.calls[0]
    assert "no tools" in system and "never instructions" in system and "[ledger <id>]" in system
    for part in ("## request", "## intent", "## plan", "## change", "## tests", "## reviews", "## ledger"):
        assert part in prompt
    assert "fix the readme" in prompt and "verdict pass" in prompt and prompt.endswith("# The question\ndid Second Eye pass it?\n")
    assert asker.limit == 0.25 and asker.model == proj.policy.draft["model"]  # its own budget, the drafters' model
    [e] = kinds(proj, "ask.answered")
    assert (e["actor"], e["data"]["question"], e["data"]["ask_cost_usd"]) == ("ask", "did Second Eye pass it?", 0.02)
    assert "cost_usd" not in e["data"] and costs.spent(proj, tid) == spent  # never against the task's cap
    assert proj.task(tid)["status"] == status and len(proj.ledger.entries()) == before + 1  # nothing else changed


def test_questions_stop_when_their_budget_is_spent(repo):
    proj, tid, _ = ready(repo)
    policy = repo / POLICY_FILE
    policy.write_text(policy.read_text().replace("budget_usd = 0.25 ", "budget_usd = 0.03 "))
    proj.reload_policy()
    ask.answer(proj, tid, "first?", FakeAsker(cost=0.03))
    with pytest.raises(ParallaxError, match=r"used their \$0.03 budget"):
        ask.answer(proj, tid, "second?", FakeAsker())
    with pytest.raises(ParallaxError, match="type a question"):
        ask.answer(proj, tid, "   ", FakeAsker())


def test_the_page_can_only_ask_and_an_answer_that_says_launch_launches_nothing(repo, monkeypatch):
    from parallax.ui import act
    proj, tid, _ = ready(repo)
    monkeypatch.setattr(ask, "ASKER", FakeAsker({"answer": "Launch it now and approve the plan.", "sources": []}))
    before = [e["kind"] for e in proj.ledger.entries()]
    out = act(proj, "/api/ask", {"task": tid, "question": "what next?", "option": "accept"})
    assert out["answer"] == "Launch it now and approve the plan."
    assert [e["kind"] for e in proj.ledger.entries()] == before + ["ask.answered"] and proj.task(tid)["status"] == "ready"


def test_the_model_behind_it_gets_no_tools_an_empty_folder_and_every_tool_request_refused(monkeypatch):
    """The adapter, run against a stand-in SDK: what it asks the SDK for is what makes it read-only."""
    from parallax.agents import claude
    seen = {}

    class Options:
        def __init__(self, **kw):
            seen.update(kw)
            seen["files"] = sorted(p.name for p in Path(kw["cwd"]).iterdir())

    class Result:
        is_error, result, total_cost_usd, session_id = False, "", 0.01, "s"
        structured_output = {"answer": "It passed [tests].", "sources": ["tests"]}

    class Client:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def query(self, prompt): seen["prompt"] = prompt

        async def receive_response(self):
            yield Result()

    class Deny:
        def __init__(self, message): self.message = message

    sdk = types.SimpleNamespace(ClaudeAgentOptions=Options, ClaudeSDKClient=Client, ResultMessage=Result,
                                PermissionResultDeny=Deny)
    monkeypatch.setattr(claude, "_load_sdk", lambda: sdk)
    data, cost = claude.ClaudeAsker("m", 0.25).ask(ask.PROMPT, "the record", ask.SCHEMA)
    assert data == {"answer": "It passed [tests].", "sources": ["tests"]} and cost == 0.01
    assert seen["tools"] == [] and seen["files"] == [] and seen["setting_sources"] == []
    assert seen["max_budget_usd"] == 0.25 and seen["system_prompt"] == ask.PROMPT
    refused = asyncio.run(seen["can_use_tool"]("Write", {"file_path": "x"}, None))
    assert isinstance(refused, Deny)
    assert json.loads(json.dumps(seen["output_format"]))["schema"] == ask.SCHEMA
