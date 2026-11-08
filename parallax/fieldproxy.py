"""Field's browser calls through Polarizer, when you turn it on: `polarizer = true` in [ui_tester].

Off by default; without the setting nothing here runs and Field is exactly as before. On, Field's
Playwright MCP server becomes the one upstream of `polarizer serve`, so Polarizer pins the tool
definitions Field gets and records every call in its own hash-chained ledger. Polarizer is the same
author's MCP proxy; it's called as a program, like srt and git, never imported.

- Which polarizer: the one on your PATH, then in ~/.local/bin, where `uv tool install` puts it, as
  `parallax doctor` looks (tools.py). The builder's PATH stays plain (build.scrubbed_env), so launch
  finds it first, where your PATH is known, and hands the builder its absolute path. A relative PATH
  entry never counts, and no policy file can name the program.
- Where it runs: outside the sandbox, as you, started by Field's Claude Code session in place of the
  MCP server. The app, the browser and the MCP server stay inside srt exactly as before: Polarizer's
  upstream is the same memory-capped srt command. Nothing in the sandbox can reach Polarizer's files.
- Its ledger: one folder for every Field run on this machine, `polarizer-field` beside Parallax's
  data folder (~/.local/share/polarizer-field), so the tool definitions you approve once stay
  approved. Never inside ~/.local/share/parallax or ~/.config/parallax, which Polarizer refuses, and
  never somewhere a policy file names: a cloned repo can turn the route on, but can't point it.
- The config: written by Parallax for each run, in the task's data folder, with a run id in a comment
  so this run's session is the one whose config hash matches. Every browser tool Field may use is
  classed open-world (its answers are page text from the app, outside input), so no call of Field's
  is held by Polarizer's rules; the tools Field may not use stay unclassified and are refused before
  they reach Polarizer.
- The stops: nothing is retried and `--no-holds` is never passed. Before every run Parallax verifies
  Polarizer's ledger: serve refuses to start on anything but an intact v1 ledger, a torn tail
  included, so any other verdict stops the task then, before Field spends anything, with
  `polarizer repair` for a torn tail (the one thing repair fixes) and a person for the rest. When
  nothing of Field's is approved yet, a definition waits, or the latest serve couldn't serve one of
  Field's tools, Polarizer first lists the server's tools with its input closed, and the task stops
  for you with the exact commands, before Field spends anything. After a run, Parallax verifies the
  ledger again (parallax/chainverify.py) and reads this run's session: a definition waiting for
  approval or changed since, a tool serve couldn't serve in this run, a held call, a refused call, or
  no call at all stops the task for you, naming it. A held call waits in Polarizer for `polarizer
  allow` or `deny` until Polarizer's own timeout, then is refused; either way the task stops for you
  afterwards.
- The record: one `uitest.polarizer` ledger entry per run, naming Polarizer's ledger folder, its chain,
  this run's session and seqs, and its calls, so what Field touched can be found and checked later. Its
  line in `parallax log` is the problem, or the counts when there was none.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import chainverify, tools
from .core import ParallaxError

SETTING = "polarizer"
VERSION = "0.6.2"  # the Polarizer release this route was written against; the integration test pins its wheel
WHEEL = f"https://github.com/mpwilso/polarizer/releases/download/v{VERSION}/polarizer-{VERSION}-py3-none-any.whl"
INSTALL = f"uv tool install {WHEEL}"
ENV = "PARALLAX_POLARIZER"  # the polarizer launch found, handed to the builder, whose own PATH stays plain
PREFIX = "playwright"  # the upstream's prefix: Field's tools arrive as playwright__<tool>
CLASS = "open-world"
CONNECT_SECONDS = 20  # Polarizer's longest wait for an upstream: the app must answer within it
LIST_SECONDS = 150  # the listing pass: the app's start, the connect, and Polarizer's own shutdown


class RouteError(ParallaxError):
    """Polarizer can't take Field's calls here. Not the maker's to fix: it comes to you."""


def on(settings: dict) -> bool:
    return settings.get(SETTING) is True


def ledger_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "polarizer-field"


def forbidden() -> list[Path]:
    """Where Polarizer's ledger must never be: Parallax's runtime and config folders, both where
    Polarizer looks for them and where this machine's XDG settings put them."""
    from .approvals import config_home
    from .sandbox import data_home
    home = Path.home()
    return [home / ".local" / "share" / "parallax", home / ".config" / "parallax", data_home(), config_home() / "parallax"]


def check_dir(folder: Path) -> Path:
    real = Path(os.path.realpath(folder))
    for bad in forbidden():
        b = Path(os.path.realpath(bad))
        if real == b or real.is_relative_to(b):
            raise RouteError(f"Polarizer's ledger for Field would be inside {b}, where Polarizer must not write")
    return real


def find(environ: dict | None = None) -> str | None:
    """Polarizer's absolute path, or None. The builder's PATH (build.scrubbed_env) leaves out
    ~/.local/bin, where uv tool install puts polarizer, so launch looks it up from your PATH and hands
    the builder the path in ENV. Without one: your PATH, then the installers' folders, the lookup
    parallax doctor uses. Only absolute folders count: a "." on PATH never runs a cloned repo's file."""
    environ = os.environ if environ is None else environ
    given = environ.get(ENV, "")
    if os.path.isabs(given) and os.path.isfile(given) and os.access(given, os.X_OK):
        return given
    folders = [d for d in tools.path(environ).split(os.pathsep) if os.path.isabs(d)]
    return shutil.which("polarizer", path=os.pathsep.join(folders))


def program() -> str:
    found = find()
    if not found:
        raise RouteError(f"[ui_tester] polarizer is on, but polarizer isn't on your PATH or in ~/.local/bin. install it: {INSTALL}")
    return found


def tool_prefix(routed: bool) -> str:
    """How Field's browser tools are named to its Claude Code session."""
    return f"mcp__playwright__{PREFIX}__" if routed else "mcp__playwright__"


def _toml(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")  # a JSON string is a TOML basic string
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml(str(k))} = {_toml(v)}" for k, v in value.items()) + " }"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(int(value))


def config_text(server: dict, folder: Path, tools, run_id: str) -> str:
    """polarizer.toml for one run: the ledger folder, and the MCP server as its one upstream."""
    for key, value in (server.get("env") or {}).items():
        if "${" in str(value):  # PROXY-SPEC.md, the upstream's environment: there is no escape for it
            raise RouteError(
                f"Field can't run through Polarizer here: its browser server's {key} is {json.dumps(value)}, and Polarizer "
                'reads "${" in an env value itself: a value that is exactly "${NAME}" is replaced from its own '
                'environment, and any other value with "${" is refused. Its config has no escape for it. take "${" '
                "out of that value (a folder's path or the [ui_tester] url), or turn polarizer off under [ui_tester]")
    lines = [f"# Written by Parallax for one Field run ({run_id}). Field's browser server is the one upstream.",
             f"ledger_dir = {_toml(str(folder))}", "",
             f"[upstream.{PREFIX}]",
             f"command = {_toml(server['command'])}",
             f"args = {_toml(list(server['args']))}",
             f"env = {_toml(dict(server.get('env') or {}))}",
             f"connect_timeout_seconds = {CONNECT_SECONDS}", "",
             f"[upstream.{PREFIX}.tools]"]
    lines += [f"{tool} = {{ class = {_toml(CLASS)} }}" for tool in sorted(tools)]
    text = "\n".join(lines) + "\n"
    parsed = tomllib.loads(text)  # written by hand, so read back before Polarizer sees it
    up = parsed["upstream"][PREFIX]
    if (up["command"], up["args"], up["env"]) != (server["command"], list(server["args"]), dict(server.get("env") or {})):
        raise RouteError("couldn't write Polarizer's config for Field: it didn't read back the same")
    return text


@dataclass
class Route:
    server: dict  # what Field's session starts in place of the MCP server
    config: Path
    sha: str  # the config file's sha256, which Polarizer records in session.started
    ledger: Path
    run_id: str
    tools: frozenset = frozenset()  # the browser tools Field may use: the ones whose approval matters


def route(server: dict, home: Path, tools) -> Route:
    """Put the MCP server behind `polarizer serve`. Raises RouteError when it can't run here."""
    exe = program()
    folder = check_dir(ledger_dir())
    run_id = uuid.uuid4().hex[:12]
    cfg = home / "polarizer.toml"
    data = config_text(server, folder, tools, run_id).encode()
    cfg.write_bytes(data)
    # HOME is yours: Polarizer finds the folders it must refuse from it
    routed = {"command": exe, "args": ["serve", "--config", str(cfg)], "env": {"HOME": str(Path.home())}}
    return Route(routed, cfg, hashlib.sha256(data).hexdigest(), folder, run_id, frozenset(tools))


# reading Polarizer's ledger --------------------------------------------------------------------------

@dataclass
class Session:
    problem: str  # empty: nothing for you
    chain_id: str | None = None
    sessions: tuple[str, ...] = ()
    first_seq: int | None = None
    last_seq: int | None = None
    head: str | None = None
    calls: int = 0
    refused: int = 0
    holds: int = 0
    pending: tuple[str, ...] = ()
    version: str | None = None
    approve: tuple[str, ...] = ()  # Field's tools that wait for a person's polarizer approve: the card says so

    def record(self, r: Route) -> dict:
        """The uitest.polarizer entry's data."""
        return {"ledger_dir": str(r.ledger), "config_sha256": r.sha, "run": r.run_id, "chain_id": self.chain_id,
                "sessions": list(self.sessions), "first_seq": self.first_seq, "last_seq": self.last_seq,
                "head": self.head, "calls": self.calls, "refused": self.refused, "holds": self.holds,
                "pending": list(self.pending), "approve": list(self.approve), "polarizer_version": self.version,
                "problem": self.problem}

    def summary(self) -> str:
        """The entry's line when nothing stopped the run, such as "Polarizer: 14 calls, 0 refused, 0 holds"."""
        def n(count: int, word: str) -> str:
            return f"{count} {word}{'' if count == 1 else 's'}"
        return f"Polarizer: {n(self.calls, 'call')}, {self.refused} refused, {n(self.holds, 'hold')}"


def _few(names: list[str]) -> str:
    return ", ".join(names[:5]) + (", ..." if len(names) > 5 else "")


def _ledger_stop(verdict: chainverify.Verdict, folder: Path) -> str:
    """Why serve won't start on this ledger, with the step that applies; "" when it will. Serve takes
    an empty ledger (it writes the genesis) and an intact v1 one, and refuses every other verdict, a
    torn tail included, and any v0 ledger (Polarizer's LEDGER-SPEC.md, Part 2)."""
    if verdict.status == "empty" or (verdict.ok and verdict.version == 1):
        return ""
    if verdict.version == 0:
        return (f"Polarizer's ledger for Field at {folder} is in Parallax's own format (v0), and Polarizer won't serve "
                f"on a v0 ledger. Parallax never writes one there, so this needs a person: see parallax verify {folder}")
    if verdict.status == "torn tail":
        return (f"Polarizer's ledger for Field ends in a torn tail ({verdict.reason}), and Polarizer won't serve on it "
                f"until it's repaired. run polarizer repair --ledger-dir {folder}, which moves the torn bytes to a file "
                "beside the ledger, then retry")
    return (f"Polarizer's ledger for Field doesn't verify: {verdict.text()}. Polarizer won't serve on it, and polarizer "
            f"repair only fixes a torn tail, so this needs a person. see polarizer verify --ledger-dir {folder}")


def _all_waiting(entries: list[dict]) -> list[str]:
    """Every tool of the browser server's that waits for a person, Field's or not: what polarizer
    pending lists, and what its group may approve."""
    names = {e["data"].get("tool") for e in entries
             if e["kind"] in ("tool.seen", "tool.drift") and e["data"].get("upstream") == PREFIX}
    return sorted(t for t, s in standings(entries, names).items() if s.state in ("new", "changed"))


def _keeping(waiting: list[str], everyone: list[str]) -> str:
    """How many tools wait, counted as polarizer pending counts them: every one the server offers,
    though Field gets only its own. Field-demo task 70d1f6's card said 19, where the group held 21."""
    others = len(set(everyone) - set(waiting))
    if not others:
        return (f"Polarizer is keeping {len(waiting)} of the browser server's tools back until you approve them: "
                f"all {len(waiting)} are tools Field may use ({_few(waiting)})")
    return (f"Polarizer is keeping {len(waiting) + others} of the browser server's tools back until you approve them: "
            f"the {len(waiting)} Field may use ({_few(waiting)}) and {others} it never gets, which Parallax refuses "
            "before they reach Polarizer")


def _approve_steps(folder: Path, asks: dict) -> str:
    """The exact commands for these tools (PIN-SPEC.md, section 7): a group covers only tools with no
    decision and one definition waiting; every other tool is approved by name, with its hash."""
    grouped = [t for t, s in sorted(asks.items()) if not s.decided and len(s.waiting) == 1]
    named = [t for t in sorted(asks) if t not in grouped]
    steps = [f"read them with polarizer pending --ledger-dir {folder}"]
    if grouped:
        steps.append(f"approve {'them' if not named else _few(grouped)} with polarizer approve --ledger-dir {folder} "
                     "--group <the group id it prints> (and any it flags, by name)")
    for t in named[:5]:
        s = asks[t]
        why = "it has a decision, so no group covers it" if s.decided else f"{len(s.waiting)} definitions wait for it"
        pick = s.waiting[0] if len(s.waiting) == 1 else "<the hash you pick>"
        steps.append(f"approve {t} by name ({why}): polarizer approve --ledger-dir {folder} {PREFIX} {t} {pick}")
    if len(named) > 5:
        steps.append(f"and the {len(named) - 5} more the same way")
    return ", ".join(steps) + ", then retry"


NEVER = ("cannot be hashed", "definition larger than")  # PIN-SPEC.md, section 2: approve refuses these for good


def _unservable_step(folder: Path, tool: str, def_hash: str | None, problem: str) -> str:
    """What a tool.unservable from this run means for you (PIN-SPEC.md, sections 3 and 5)."""
    name = f"{PREFIX}__{tool}"
    copy = folder / "defs" / f"{def_hash}.json"
    if problem.startswith(NEVER):
        return (f"{name} {problem}, so it can never be approved and Field can't use it through Polarizer. to run "
                "Field, turn polarizer off under [ui_tester]")
    if problem.startswith("more than 16 definitions"):
        return (f"{name} has {problem}: approve one by name. read them with polarizer pending --ledger-dir {folder}, "
                f"then polarizer approve --ledger-dir {folder} {PREFIX} {tool} <the hash you pick>, then retry")
    if problem == "stored copy missing":
        return f"{name}: {problem} ({copy}). serve writes it again when it next lists that definition, so retry"
    if problem.startswith("stored copy could not be written"):
        return f"{name}: {problem}. let Polarizer write to {folder / 'defs'}, then retry"
    return (f"{name}: {problem} ({copy}). Polarizer keeps a bad copy until a person deletes it: look at it, delete "
            "it, and retry, and serve writes it again when it next lists that definition")


def read_session(r: Route, lock_wait: float = 10.0, listing: bool = False) -> Session:
    """This run's sessions in Polarizer's ledger, once the whole chain verifies. listing: the pass
    that only lists the tools, where no call is expected."""
    folder = r.ledger
    if not (folder / "ledger.jsonl").exists():
        return Session(f"Polarizer never started for Field: there is no ledger at {folder}. "
                       f"run polarizer serve --config {r.config} < /dev/null to see why")
    try:
        verdict, data = chainverify.read(folder / "ledger.jsonl", folder / "ledger.head", lock_wait=lock_wait)
    except TimeoutError as err:
        return Session(f"couldn't read Polarizer's ledger for Field: {err}")
    if verdict.status == "empty":
        return Session(f"Polarizer never started for Field: its ledger at {folder} is empty")
    problem = _ledger_stop(verdict, folder)
    if problem:
        return Session(problem, chain_id=verdict.chain_id)
    entries = chainverify.entries(data)
    starts = [e for e in entries if e["kind"] == "session.started" and e["data"].get("config_sha256") == r.sha]
    base = Session("", chain_id=verdict.chain_id, head=verdict.head)
    if not starts:
        base.problem = (f"Polarizer didn't start Field's session: its ledger has no session for this run's config. "
                        f"run polarizer serve --config {r.config} < /dev/null to see why")
        return base
    ids = {e["data"]["session"] for e in starts}
    mine = [e for e in entries if e["seq"] >= starts[0]["seq"] and e["data"].get("session") in ids]
    sent = {e["seq"] for e in mine if e["kind"] == "call.sent"}
    returned = [e for e in entries if e["kind"] == "call.returned" and e["data"].get("call_seq") in sent]
    seqs = [e["seq"] for e in mine + returned]
    base.sessions = tuple(sorted(ids))
    base.first_seq, base.last_seq = min(seqs), max(seqs)
    base.version = starts[-1]["data"].get("polarizer_version")
    base.calls = len(sent)
    refused = [e for e in mine if e["kind"] == "call.refused"]
    held = [e for e in mine if e["kind"] == "hold.created"]
    base.refused, base.holds = len(refused), len(held)
    state = standings(entries, r.tools)
    waiting = sorted(t for t, s in state.items() if s.state == "new")
    changed = sorted(t for t, s in state.items() if s.state == "changed")
    # what this run's serve couldn't serve: an older session's record may since have healed
    stuck = {t: next(v for k, v in s.stuck.items() if k in ids) for t, s in sorted(state.items()) if ids & set(s.stuck)}
    base.pending = tuple(waiting + changed + [t for t in stuck if t not in waiting + changed])
    asks = {t: state[t] for t in waiting + changed}
    base.approve = tuple(waiting + changed)
    if changed:
        base.problem = (f"Polarizer hid Field's browser tools that changed since you approved them ({_few(changed)})"
                        + (f", and is keeping {len(waiting)} more back until you approve them" if waiting else "")
                        + ": " + _approve_steps(folder, asks))
    elif waiting:
        base.problem = _keeping(waiting, _all_waiting(entries)) + ": " + _approve_steps(folder, asks)
    elif stuck:
        base.problem = (f"Polarizer couldn't serve {len(stuck)} of Field's browser tools in this run: "
                        + "; ".join(_unservable_step(folder, t, h, why) for t, (h, why) in stuck.items()))
    elif held:
        tools = sorted({e["data"].get("tool", "") for e in held})
        base.problem = (f"Polarizer held {len(held)} of Field's browser calls for a person ({', '.join(tools)}). "
                        f"nothing was retried. see them with polarizer holds --ledger-dir {folder}, then retry")
    elif refused:
        why = refused[0]["data"].get("reason", "")
        base.problem = f"Polarizer refused {len(refused)} of Field's browser calls (first: {why}). nothing was retried"
    elif not sent and (not listing or not any(e["kind"] == "upstream.connected" and e["seq"] > starts[0]["seq"]
                                              and e["data"].get("prefix") == PREFIX and not e["data"].get("error")
                                              for e in entries)):
        failed = [e["data"]["error"] for e in entries if e["kind"] == "upstream.connected" and e["seq"] > starts[0]["seq"]
                  and e["data"].get("prefix") == PREFIX and e["data"].get("error")]
        base.problem = (("Polarizer couldn't list Field's browser tools: " if listing else
                         "none of Field's browser calls reached Polarizer: ")
                        + (f"its browser server didn't connect ({failed[0]})" if failed else
                           f"its browser server may not have connected within {CONNECT_SECONDS} s, Polarizer's longest wait")
                        + f". see polarizer verify --ledger-dir {folder}")
    return base


@dataclass
class Standing:
    """One of Field's tools in Polarizer's pin state (PIN-SPEC.md, section 4), folded from the ledger."""
    state: str  # approved, rejected, new, changed, or unservable (never hashed, so never seen or decided)
    decided: bool  # it has an approval or a rejection, so no group can approve it
    waiting: tuple[str, ...] = ()  # the definitions seen since its latest decision, in the order serve saw them
    stuck: dict = field(default_factory=dict)  # session -> (def_hash, problem): what it couldn't serve, since then


def standings(entries: list[dict], tools) -> dict[str, Standing]:
    """Where each of Field's tools stands, from its latest decision and what serve recorded since. New
    and changed last until a decision (changed is sticky). Unservable isn't: serve records it once per
    process, and a stored copy that passes again serves with no new entry, so only the sessions asked
    about say whether it still holds. A tool serve never listed isn't here."""
    fold: dict[str, dict] = {}
    for e in entries:
        d, kind = e["data"], e["kind"]
        if not kind.startswith("tool.") or d.get("upstream") != PREFIX or d.get("tool") not in tools:
            continue
        t = fold.setdefault(d["tool"], {"decision": None, "drift": False, "seen": [], "stuck": {}})
        if kind in ("tool.approved", "tool.rejected"):
            t.update(decision=kind, drift=False, seen=[], stuck={})
        elif kind in ("tool.seen", "tool.drift"):
            t["drift"] = t["drift"] or kind == "tool.drift"
            h = d.get("def_hash") if kind == "tool.seen" else d.get("live_hash")
            if h not in t["seen"]:
                t["seen"].append(h)
        elif kind == "tool.unservable":
            t["stuck"][d.get("session")] = (d.get("def_hash"), str(d.get("problem") or ""))
    out = {}
    for tool, t in fold.items():
        if t["decision"] == "tool.approved":
            state = "changed" if t["drift"] else "approved"
        elif t["seen"]:
            state = "new"
        elif t["decision"]:
            state = "rejected"
        else:
            state = "unservable"
        out[tool] = Standing(state, t["decision"] is not None, tuple(t["seen"]), t["stuck"])
    return out


def decisions(entries: list[dict], tools) -> dict[str, str]:
    """Each tool's state alone."""
    return {t: s.state for t, s in standings(entries, tools).items()}


def needs_listing(entries: list[dict], tools) -> bool:
    """List first when nothing of Field's is approved yet, something waits, or the latest serve couldn't
    serve one of Field's tools: the listing pass's own session then says whether that still holds."""
    state = standings(entries, tools)
    if not any(s.state == "approved" for s in state.values()):
        return True
    if any(s.state in ("new", "changed", "unservable") for s in state.values()):
        return True
    last = next((e["data"].get("session") for e in reversed(entries) if e["kind"] == "session.started"), None)
    return any(last in s.stuck for s in state.values())


LISTER = None  # tests replace this; None runs the real `polarizer serve` with its input closed


def listing_pass(r: Route, lock_wait: float = 10.0) -> Session | None:
    """Before every run, before Field spends anything. A ledger serve won't start on stops the task
    here: otherwise Field's session would run with no browser tools. Then, with nothing of Field's
    approved, a definition waiting, or a tool the latest serve couldn't serve: serve with its input
    closed lists the server's tools and exits, so the task stops for your approval. Returns the
    session read afterwards, or None when there was nothing to do."""
    path = r.ledger / "ledger.jsonl"
    entries: list[dict] = []
    if path.exists():
        try:  # under Polarizer's lock, as its verify reads: a line being written isn't a torn tail
            verdict, data = chainverify.read(path, r.ledger / "ledger.head", lock_wait=lock_wait)
        except TimeoutError as err:
            return Session(f"couldn't read Polarizer's ledger for Field: {err}")
        problem = _ledger_stop(verdict, r.ledger)
        if problem:
            return Session(problem, chain_id=verdict.chain_id)
        entries = chainverify.entries(data)
    if not needs_listing(entries, r.tools):
        return None
    run = LISTER or _list
    run(r)
    return read_session(r, listing=True)


def _list(r: Route) -> None:
    try:
        subprocess.run([r.server["command"], *r.server["args"]], stdin=subprocess.DEVNULL, capture_output=True,
                       timeout=LIST_SECONDS, env={**os.environ, **r.server["env"]})
    except subprocess.TimeoutExpired:
        pass  # read_session says what the ledger shows
