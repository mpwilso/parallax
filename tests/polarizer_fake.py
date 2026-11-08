"""A stand-in for what `polarizer serve` writes to its ledger, for tests that need no Polarizer install.

Entries are v1 chain lines (LEDGER-SPEC.md, Part 1) with the kinds and data keys the spec lists. The
conformance fixtures, not this, are the independent check of the verifier."""
import hashlib
import shlex
from pathlib import Path

from parallax import chainverify

CHAIN = "5f0c9e2a7b14d3e8a1c6f9b2d4e7a0c3"


class Ledger:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / "ledger.jsonl"
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or not self.path.read_bytes():
            self.path.write_bytes(b"")
            self.add("ledger.genesis", chain_id=CHAIN)

    def entries(self) -> list[dict]:
        return chainverify.entries(self.path.read_bytes())

    def add(self, kind: str, **data) -> dict:
        old = self.entries()
        entry = {"v": 1, "seq": len(old), "ts": "2026-11-07T12:00:00.000Z", "kind": kind, "data": data,
                 "prev": old[-1]["hash"] if old else "0" * 64}
        entry["hash"] = chainverify.v1_hash(entry)
        with self.path.open("ab") as f:
            f.write(chainverify.canonical(entry) + b"\n")
        return entry

    def head(self) -> None:
        last = self.entries()[-1]
        (self.folder / "ledger.head").write_bytes(
            chainverify.canonical({"chain_id": CHAIN, "hash": last["hash"], "seq": last["seq"]}) + b"\n")


def config_of(server: dict) -> Path:
    """The polarizer.toml a routed server starts serve with."""
    args = server["args"]
    assert args[:2] == ["serve", "--config"], shlex.join(args)
    return Path(args[2])


def session(ledger: Ledger, server: dict, session: str = "8c1d4f7a2b5e9036", connected: bool = True) -> str:
    """serve's start, for the run whose config the server names."""
    sha = hashlib.sha256(config_of(server).read_bytes()).hexdigest()
    ledger.add("session.started", session=session, polarizer_version="0.6.0", config_sha256=sha)
    ledger.add("policy.loaded", session=session, holds="on", policy_sha256="1" * 64, classified=19,
               workspace_roots=[], hold_timeout_seconds=300, planted_calls_per_session=0, client_session=None)
    if connected:
        ledger.add("upstream.connected", prefix="playwright", protocol_version="2025-11-25", tools=21, skipped_tools=[])
    return session


def call(ledger: Ledger, session: str, tool: str = "browser_navigate") -> None:
    sent = ledger.add("call.sent", session=session, tool=f"playwright__{tool}", args_commit="3" * 64, meta_dropped=[],
                      client_call_id="toolu_01", allowed_by="open-world")
    ledger.add("call.returned", call_seq=sent["seq"], outcome="ok", latency_ms=40, result_bytes=200)
