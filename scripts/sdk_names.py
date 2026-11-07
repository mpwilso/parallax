"""Every name parallax/agents/claude.py uses from claude-agent-sdk exists in the installed version.

CI runs this after installing the claude extra, so a release inside the pinned range that renames
or drops one fails here, not on a real task. It builds the SDK's option objects and calls no model:
    uv run --no-project --python 3.12 --with-editable ".[claude]" python scripts/sdk_names.py
"""
import dataclasses
import inspect
import sys
from importlib.metadata import version

from parallax.agents import claude

sdk = claude._load_sdk()  # the adapter's own import, so its error message is the one tested
FIELDS = {
    "ClaudeAgentOptions": ("model", "cwd", "system_prompt", "tools", "mcp_servers", "permission_mode", "hooks",
                           "can_use_tool", "setting_sources", "settings", "max_turns", "max_budget_usd", "env",
                           "output_format"),
    "HookMatcher": ("matcher", "hooks", "timeout"),
    "PermissionResultDeny": ("message",),
    "ResultMessage": ("subtype", "is_error", "result", "total_cost_usd", "structured_output", "session_id"),
}
METHODS = {"ClaudeSDKClient": ("query", "receive_response", "__aenter__", "__aexit__")}


def names(cls) -> set[str]:
    if dataclasses.is_dataclass(cls):
        return {f.name for f in dataclasses.fields(cls)}
    return set(inspect.signature(cls).parameters)


missing = []
for name, wanted in FIELDS.items():
    cls = getattr(sdk, name, None)
    have = names(cls) if cls is not None else set()
    missing += [f"{name}.{f}" for f in wanted if f not in have] if cls is not None else [name]
for name, wanted in METHODS.items():
    cls = getattr(sdk, name, None)
    missing += [f"{name}.{m}" for m in wanted if not hasattr(cls, m)] if cls is not None else [name]
if not missing:  # the adapter's calls, built as it builds them: no session starts
    sdk.ClaudeAgentOptions(model=claude.DEFAULT_MODEL, cwd=".", tools=[], permission_mode="default", setting_sources=[],
                           hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[], timeout=1)]},
                           max_turns=1, max_budget_usd=0.01, env={})
    sdk.PermissionResultDeny(message="refused")
print(f"claude-agent-sdk {version('claude-agent-sdk')}: "
      + (f"missing {', '.join(missing)}" if missing else "every name the adapter uses is there"))
sys.exit(1 if missing else 0)
