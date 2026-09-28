"""Adapter over claude-agent-sdk. The only module that imports a vendor SDK, and only lazily.

Every tool call is ruled on by a PreToolUse hook, which fires before the SDK's own permission
flow. can_use_tool alone isn't enough: the default mode auto-approves reads without calling it.
can_use_tool stays as a backstop that refuses anything the hook somehow didn't rule on.
No allowed_tools and no user or project settings, so no outside allow rule applies.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path

from .base import AgentResult, AgentUnavailable, CheckerError, Permission, PermissionFn, Verdict

DEFAULT_MODEL = "claude-opus-5"
HUMAN_WAIT_SECONDS = 24 * 3600  # hook timeout; an `ask` can wait this long for you

READ_TOOLS = {"Read", "Glob", "Grep"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
FETCH_TOOLS = {"WebFetch", "WebSearch"}
BUILD_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit", "Bash", "WebFetch", "WebSearch"]
PLAN_TOOLS = ["Read", "Glob", "Grep"]

MAKER_PROMPT = """\
You are the maker for one task. Your working directory is the task's own git worktree; stay inside it.
Every action is checked against a policy. Some need a human's approval, so a call may pause while they decide.
If a call is refused, don't retry the same thing. Adapt, or stop.
Never edit parallax.policy.toml, mission.md, or anything under .parallax/.
When you finish, reply with a short summary of what you changed.
If you can't finish, start your final reply with "gave up:" and say why."""

PLAN_PROMPT = """\
You are the maker for one task, in the planning stage. You can read files but not change anything.
Your final reply is the plan and nothing else: the steps, the files you'd touch, and how you'd test it.
If you can't make a plan, start your final reply with "gave up:" and say why."""

CHECKER_PROMPT = """\
You review a {kind} for a task. You see only the task goal and the {kind}. You do not see the author's
reasoning, on purpose. Judge whether it achieves the goal and whether it introduces problems.
- pass: it achieves the goal with no blocking problems.
- fail: it doesn't achieve the goal, or has a blocking problem. List each finding.
- no_finding: you found nothing wrong but can't confirm the goal is met from this alone.
"no_finding" is a valid answer. Don't invent findings to look thorough.
Reply with JSON only: {{"verdict": "pass" | "fail" | "no_finding", "findings": ["..."]}}"""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "no_finding"]},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "findings"],
    "additionalProperties": False,
}


def tool_to_action(tool_name: str, tool_input: dict) -> tuple[str, str, list[str]]:
    """Map an SDK tool call to a parallax action, a detail for the ledger, and paths written."""
    if tool_name in READ_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("pattern") or ""
        return "fs.read", str(target), []
    if tool_name in WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return "fs.write", str(path), [str(path)] if path else []
    if tool_name == "Bash":
        return "shell.run", str(tool_input.get("command", "")), []
    if tool_name in FETCH_TOOLS:
        return "net.fetch", str(tool_input.get("url") or tool_input.get("query") or ""), []
    # anything else is unlisted unless someone deliberately lists it, so denied by default
    return f"tool.{tool_name}", json.dumps(tool_input, sort_keys=True)[:200], []


async def rule_on_tool_call(permission_fn: PermissionFn, tool_name: str, tool_input: dict) -> dict:
    """PreToolUse hook output for one tool call, decided by the parallax gate."""
    action, detail, paths = tool_to_action(tool_name, tool_input)
    p: Permission = await asyncio.to_thread(permission_fn, action, detail, paths)
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if p.allowed else "deny",
        "permissionDecisionReason": p.message or ("allowed by parallax policy" if p.allowed else "refused"),
    }}


def _load_sdk():
    try:
        import claude_agent_sdk
    except ImportError as err:
        raise AgentUnavailable("claude-agent-sdk not installed. pip install -e .[claude]") from err
    return claude_agent_sdk


async def _final_result(sdk, options, prompt: str):
    result = None
    async with sdk.ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, sdk.ResultMessage):
                result = msg
    return result


class ClaudeAgent:
    def __init__(self, model: str = DEFAULT_MODEL, max_turns: int | None = None):
        self.sdk = _load_sdk()
        self.model = model
        self.max_turns = max_turns

    def run(self, goal: str, cwd: Path, permission_fn: PermissionFn, stage: str = "build") -> AgentResult:
        return asyncio.run(self._run(goal, cwd, permission_fn, stage))

    async def _run(self, goal: str, cwd: Path, permission_fn: PermissionFn, stage: str) -> AgentResult:
        sdk = self.sdk

        async def pre_tool_use(input_data, tool_use_id, context):
            return await rule_on_tool_call(permission_fn, input_data["tool_name"], input_data["tool_input"])

        async def no_ruling(tool_name, tool_input, context):
            return sdk.PermissionResultDeny(message="refused: no parallax ruling for this call")

        options = sdk.ClaudeAgentOptions(
            model=self.model,
            cwd=str(cwd),
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": PLAN_PROMPT if stage == "plan" else MAKER_PROMPT},
            tools=PLAN_TOOLS if stage == "plan" else BUILD_TOOLS,
            permission_mode="default",
            hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[pre_tool_use],
                                                  timeout=HUMAN_WAIT_SECONDS)]},
            can_use_tool=no_ruling,
            setting_sources=[],
            max_turns=self.max_turns,
        )
        result = await _final_result(sdk, options, goal)
        if result is None:
            return AgentResult("error", "agent ended without a result")
        text = result.result or ""
        if result.is_error:
            return AgentResult("error", text or str(result.subtype))
        if text.strip().lower().startswith("gave up"):
            return AgentResult("gave_up", text)
        return AgentResult("done", text)


class ClaudeChecker:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.sdk = _load_sdk()
        self.model = model

    def review(self, goal: str, material: str, kind: str) -> Verdict:
        return asyncio.run(self._review(goal, material, kind))

    async def _review(self, goal: str, material: str, kind: str) -> Verdict:
        sdk = self.sdk

        async def no_tools(tool_name, tool_input, context):
            return sdk.PermissionResultDeny(message="the checker has no tools")

        prompt = f"task goal:\n{goal}\n\n{kind} to review:\n{material or '(empty)'}"
        with tempfile.TemporaryDirectory() as empty:  # nothing to read even if a tool slipped through
            options = sdk.ClaudeAgentOptions(
                model=self.model,
                cwd=empty,
                system_prompt=CHECKER_PROMPT.format(kind=kind),
                tools=[],
                permission_mode="default",
                can_use_tool=no_tools,
                setting_sources=[],
                output_format={"type": "json_schema", "schema": VERDICT_SCHEMA},
            )
            result = await _final_result(sdk, options, prompt)
        if result is None or result.is_error:
            raise CheckerError("checker ended without a verdict")
        data = getattr(result, "structured_output", None) or _parse_json(result.result or "")
        return _to_verdict(data)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise CheckerError(f"checker reply wasn't JSON: {text[:120]!r}") from err


def _to_verdict(data) -> Verdict:
    if not isinstance(data, dict) or not isinstance(data.get("findings", []), list):
        raise CheckerError(f"checker reply had the wrong shape: {str(data)[:120]!r}")
    return Verdict(str(data.get("verdict")), [str(f) for f in data.get("findings", [])])
