# Prior art

Open-source projects and published work Parallax learned from, and where it differs. Ideas only;
no code, names or UI copied.

| Source | The idea | Where Parallax differs |
|---|---|---|
| toryo | A trust score from a rolling quality average; agents earn autonomy | Autonomy comes only from rules you set in the policy file (`auto_launch_usd`, `review_paths`); code approves under them, signed and naming the rule |
| kodo | Separate reviewer agents; a rejection triggers a retry loop | The checker is blind to the maker's explanation, reworks are capped at 3 recorded cycles, and the next fail comes to you |
| claude-code-permissions-hook | A PreToolUse hook with allow and deny rules and an audit log | The hook enforces the approved plan's scope, behind an OS sandbox, with a hash-chained ledger |
| claude-agent-sdk-python (permission callback example) | Ruling on every tool call in code | Every call is ruled on by a hook first, since reads can skip the callback |
| claude-squad, container-use | Parallel agents in worktrees or containers | One task, one worktree, one sandbox, one budget; Parallax isn't a multiplexer |
| SWE-bench | Scoring a fix by the tests from the merged pull request, hidden from the agent | Also measured the blind checker's catches, misses and false alarms |
| The AI-native SDLC playbook | Files as the hand-off between stages; humans own the judgment calls | Drafting and launch run without you; you make one call per task |
| impeccable | A design critique and audit method | Used to review Parallax's own UI (see PRODUCT.md); not part of Parallax |
| Playwright, Playwright MCP | Driving a real browser from tests and from an agent | The UI tester runs the browser inside the sandbox, blind to the code |

## Sources

- toryo: https://github.com/JesseRWeigel/toryo
- kodo: https://github.com/ikamensh/kodo
- claude-code-permissions-hook: https://github.com/kornysietsma/claude-code-permissions-hook
- claude-agent-sdk-python, tool permission callback example: https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/tool_permission_callback.py
- claude-squad: https://github.com/smtg-ai/claude-squad
- container-use: https://github.com/dagger/container-use
- awesome-agent-orchestrators, the survey that showed the gap: https://github.com/andyrewlee/awesome-agent-orchestrators
- SWE-bench: https://www.swebench.com
- The AI-native SDLC playbook: https://claude.com/blog/the-ai-native-sdlc-playbook
- impeccable: https://github.com/pbakaus/impeccable
- Playwright: https://github.com/microsoft/playwright; Playwright MCP: https://github.com/microsoft/playwright-mcp
