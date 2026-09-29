"""Can this machine start the real sandbox? The three tests that need it skip, saying why, if not.

Having srt, bubblewrap and socat installed isn't enough: some machines (CI runners, say) block the
user namespaces bubblewrap needs. So this starts one sandbox that runs `true`, once per session.
"""
import functools
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


@functools.lru_cache(maxsize=None)
def why_not() -> str | None:
    """None if the real sandbox starts here, else the reason it doesn't."""
    if os.environ.get("SANDBOX_RUNTIME"):
        return "already inside a sandbox, and a sandbox can't start inside another (the M9 spike)"
    missing = [b for b in ("srt", "bwrap", "socat") if not shutil.which(b)]
    if missing:
        return f"needs srt, bubblewrap and socat; missing {', '.join(missing)}"
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "srt.json"
        cfg.write_text(json.dumps({"network": {"allowedDomains": [], "deniedDomains": []},
                                   "filesystem": {"allowWrite": [tmp], "denyWrite": [], "denyRead": [], "allowRead": []}}))
        try:
            out = subprocess.run(["srt", "--settings", str(cfg), "-c", "true"], cwd=tmp, capture_output=True,
                                 text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return "srt is installed but starting a sandbox timed out here"
    if out.returncode != 0:
        last = ((out.stderr or out.stdout).strip().splitlines() or ["no output"])[-1][:160]
        return f"srt is installed but can't start a sandbox here: {last}"
    return None
