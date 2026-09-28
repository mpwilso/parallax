"""The secrets scan at accept: best effort, and THREAT_MODEL.md says so.

Built-in patterns run over the lines the change adds. If gitleaks is installed, it runs too, over
the changed files. A hit blocks accept unless you accept the risk, with a reason. A miss proves
nothing: a secret split across lines, encoded, or in a format no pattern knows gets through.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "aws access key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google api key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "assigned secret": re.compile(r"(?i)\b(api[_-]?key|secret|token|passw(or)?d)\b\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']"),
}


def scan_diff(diff: str) -> list[str]:
    """'path:line kind' for every added line that matches a pattern."""
    hits, path, line = [], "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("+"):
            for kind, pattern in PATTERNS.items():
                if pattern.search(raw):
                    hits.append(f"{path}:{line} {kind}")
                    break
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return hits


def gitleaks(files: dict[str, bytes]) -> tuple[list[str], str]:
    """(hits, note). The note says why gitleaks didn't run, or "" if it did."""
    if not shutil.which("gitleaks"):
        return [], "gitleaks isn't installed"
    with tempfile.TemporaryDirectory() as tmp:
        for rel, data in files.items():
            (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / rel).write_bytes(data)
        report = Path(tmp) / ".gitleaks-report.json"
        out = subprocess.run(["gitleaks", "detect", "--no-git", "--source", tmp, "--no-banner", "--redact",
                              "--report-format", "json", "--report-path", str(report), "--exit-code", "0"],
                             capture_output=True, text=True)
        if out.returncode != 0 or not report.exists():
            return [], f"gitleaks failed to run: {(out.stderr or out.stdout).strip()[:120]}"
        found = json.loads(report.read_text() or "[]")
    return [f"{f.get('File', '?').replace(tmp + '/', '')}:{f.get('StartLine', 0)} {f.get('RuleID', 'secret')} (gitleaks)"
            for f in found], ""
