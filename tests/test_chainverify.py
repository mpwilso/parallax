"""The dual-version chain verifier: Parallax's own ledgers are v0 and verify; Polarizer's v1 chains
verify from a file or a folder; `parallax verify <path>` reports one line and never writes."""
import json
import os
import sys
from pathlib import Path

import pytest

from parallax import chainverify
from parallax.cli import main
from parallax.core import Project
from parallax.ledger import Ledger
from polarizer_fake import Ledger as V1Ledger

CONFORMANCE = Path(__file__).resolve().parent.parent / "conformance"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Parallax's ledger writes text mode; Linux and WSL only")


@posix_only
def test_a_ledger_parallax_writes_is_intact_v0(repo):
    proj = Project.init(repo)
    proj.new_task("fix the thing")
    proj.ledger.append("note", "agent", "café   \U0001f600", ratio=0.5, items=[1, 2.25, None, True])
    verdict, _ = chainverify.read(proj.ledger.path)
    n = len(proj.ledger.entries())
    assert (verdict.status, verdict.version, verdict.line, verdict.seq, verdict.entries) == ("intact", 0, n, None, n)
    assert verdict.head == proj.ledger.entries()[-1]["hash"]
    assert proj.ledger.verify() == (True, "ledger intact")  # Parallax's own check is unchanged beside it


@posix_only
def test_an_edit_to_a_parallax_ledger_is_caught_by_both_checks(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for kind in ("task.created", "task.approved", "task.accepted"):
        ledger.append(kind, "human")
    lines = ledger.path.read_text().splitlines()
    second = json.loads(lines[1])
    second["actor"] = "agent"
    ledger.path.write_text("\n".join([lines[0], json.dumps(second, sort_keys=True), lines[2]]) + "\n")
    verdict, _ = chainverify.read(ledger.path)
    assert (verdict.status, verdict.check, verdict.line) == ("tampered", "hash", 2)
    assert verdict.text() == "tampered: line 2: hash does not match the entry"
    assert not ledger.verify()[0]


@posix_only
def test_a_blank_line_is_invalid_here_though_parallaxs_own_check_skips_it(tmp_path):
    """The one difference the spec names: v0's line form is checked byte for byte."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("task.created", "human")
    with ledger.path.open("a") as f:
        f.write("\n")
    assert ledger.verify() == (True, "ledger intact")
    verdict, _ = chainverify.read(ledger.path)
    assert (verdict.status, verdict.line, verdict.reason) == ("invalid", 2, "not valid JSON")


def test_a_polarizer_chain_verifies_with_its_head(tmp_path):
    chain = V1Ledger(tmp_path / "pz")
    chain.add("session.started", session="8c1d4f7a2b5e9036", polarizer_version="0.6.0", config_sha256="7" * 64)
    chain.head()
    verdict, _ = chainverify.read(*chainverify.target(tmp_path / "pz"))
    assert verdict.text() == f"intact (v1): 2 entries, chain {chain.entries()[0]['data']['chain_id']}, head {verdict.head} at seq 1"
    chain.add("call.refused", session="8c1d4f7a2b5e9036", tool="nope__x", reason='no upstream with prefix "nope"')
    assert chainverify.read(*chainverify.target(tmp_path / "pz"))[0].ok  # a head behind the last entry is normal
    (tmp_path / "pz" / "ledger.jsonl").write_bytes(b"".join((tmp_path / "pz" / "ledger.jsonl").read_bytes().splitlines(True)[:1]))
    verdict, _ = chainverify.read(*chainverify.target(tmp_path / "pz"))
    assert (verdict.status, verdict.seq) == ("truncated", 1)


def test_parallax_verify_checks_a_file_or_a_folder_from_anywhere(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # not a parallax project
    assert main(["verify", str(CONFORMANCE / "valid" / "v0-parallax.jsonl")]) == 0
    assert capsys.readouterr().out.startswith("intact (v0): 4 entries, head ")
    assert main(["verify", str(CONFORMANCE / "broken" / "bad_seq.jsonl")]) == 1
    assert capsys.readouterr().out == "tampered: line 6 (seq 15): seq is 15, expected 5\n"
    assert main(["verify", str(CONFORMANCE / "broken" / "truncated.jsonl"),
                 "--head", str(CONFORMANCE / "broken" / "truncated.head")]) == 1
    assert capsys.readouterr().out == "truncated: ledger ends at seq 7 but ledger.head records seq 10\n"
    assert main(["verify", str(CONFORMANCE / "broken" / "tear_last_line.jsonl")]) == 1
    assert capsys.readouterr().out == "torn tail: 167 bytes after line 12 (seq 11)\n"
    chain = V1Ledger(tmp_path / "pz")
    chain.head()
    assert main(["verify", str(tmp_path / "pz")]) == 0
    assert capsys.readouterr().out.startswith("intact (v1): 1 entries, chain ")
    assert main(["verify", str(tmp_path / "nothing.jsonl")]) == 1
    assert "no ledger at" in capsys.readouterr().err


def test_verify_never_writes(tmp_path):
    chain = V1Ledger(tmp_path / "pz")
    chain.head()
    (tmp_path / "pz" / "ledger.jsonl.lock").write_bytes(b"")

    def listing():
        return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in (tmp_path / "pz").iterdir())
    before = listing()
    assert main(["verify", str(tmp_path / "pz")]) == 0
    chainverify.read(tmp_path / "pz" / "ledger.jsonl", tmp_path / "pz" / "ledger.head", lock_wait=1)
    assert listing() == before


@pytest.mark.skipif(sys.platform == "win32", reason="Polarizer's lock is fcntl on POSIX")
def test_a_read_under_polarizers_lock_waits_and_then_gives_up(tmp_path):
    import fcntl
    chain = V1Ledger(tmp_path / "pz")
    lock = tmp_path / "pz" / "ledger.jsonl.lock"
    lock.write_bytes(b"")
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(TimeoutError, match="stayed locked"):
            chainverify.read(chain.path, lock_wait=0.2)
    finally:
        os.close(fd)
    assert chainverify.read(chain.path, lock_wait=0.2)[0].ok


def test_a_folder_checks_the_head_you_name_and_a_missing_one_is_an_error(tmp_path, capsys):
    chain = V1Ledger(tmp_path / "pz")
    chain.head()
    assert main(["verify", str(tmp_path / "pz"), "--head", str(tmp_path / "missing.head")]) == 1
    assert capsys.readouterr().err == f"parallax: no head file at {tmp_path / 'missing.head'}\n"
    ahead = tmp_path / "ahead.head"
    ahead.write_bytes(chainverify.canonical({"chain_id": chain.entries()[0]["data"]["chain_id"], "hash": "e" * 64, "seq": 5})
                      + b"\n")
    assert main(["verify", str(tmp_path / "pz"), "--head", str(ahead)]) == 1
    assert capsys.readouterr().out == "truncated: ledger ends at seq 0 but ledger.head records seq 5\n"


def nested(levels: int):
    """A value whose arrays and objects nest this many levels: as an entry's data["x"], the entry's
    deepest level is levels + 2 (the entry is level 1, its data level 2)."""
    value = 0
    for i in range(levels):
        value = {"a": value} if i % 2 else [value]
    return value


@posix_only
def test_a_line_may_nest_64_deep_and_no_deeper_in_either_version(tmp_path, capsys):
    """LEDGER-SPEC.md's nesting limit: measured from the bytes before parsing, for v0 and v1 alike,
    so even a line too deep for Python's parser gets a one-line verdict. Brackets in strings don't count."""
    chain = V1Ledger(tmp_path / "pz")
    chain.add("note.deep", x=nested(62), s="[[[[{{{{" * 20)
    verdict, _ = chainverify.read(chain.path)
    assert (verdict.status, verdict.entries) == ("intact", 2)
    chain.add("note.deep", x=nested(63))  # hashed and linked: depth is its only fault
    verdict, _ = chainverify.read(chain.path)
    assert (verdict.status, verdict.check, verdict.line, verdict.seq) == ("invalid", "structure", 3, None)
    assert verdict.text() == "invalid: line 3 (seq ?): nested more than 64 deep"
    assert main(["verify", str(tmp_path / "pz")]) == 1 and capsys.readouterr().out == verdict.text() + "\n"
    v0 = Ledger(tmp_path / "ledger.jsonl")
    v0.append("note", "agent", "", x=nested(62))
    assert chainverify.read(v0.path)[0].status == "intact"
    v0.append("note", "agent", "", x=nested(63))
    verdict, _ = chainverify.read(v0.path)
    assert (verdict.status, verdict.check, verdict.line, verdict.reason) == ("invalid", "structure", 2, "nested more than 64 deep")
    far = b"[" * 100_000 + b"]" * 100_000  # past what Python's json can parse at all
    verdict = chainverify.verify_bytes((CONFORMANCE / "valid" / "minimal.jsonl").read_bytes() + far + b"\n")
    assert (verdict.status, verdict.reason) == ("invalid", "nested more than 64 deep")


def test_messages_name_the_rule_and_the_place():
    assert chainverify.verify_bytes(b"").text() == "empty: the ledger has no entries"
    assert chainverify.verify_bytes(b"[]\n").text() == "invalid: line 1 (seq ?): not a JSON object"
    v0 = (CONFORMANCE / "valid" / "v0-parallax.jsonl").read_bytes()
    assert chainverify.verify_bytes(v0 + b"x").text() == "torn tail: 1 bytes after line 4"
