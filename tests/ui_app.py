"""The UI with a task in every state, on fake agents: no model, no cost. For the UI tester and people.

usage: PYTHONPATH=. python3 tests/ui_app.py [port]

It builds a throwaway project under $TMPDIR and serves `parallax ui` for it at
http://127.0.0.1:<port>/#parallax-ui-tester-demo-token. Nothing runs in the background: work typed
into the page stays in drafting.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

TOKEN = "parallax-ui-tester-demo-token"


def main(port: int) -> None:
    root = Path(tempfile.mkdtemp(prefix="ui-app-"))
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ["XDG_CONFIG_HOME"] = str(root / "config")
    os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"  # the sandbox refuses any .gitconfig, even a missing one
    for who in ("AUTHOR", "COMMITTER"):
        os.environ[f"GIT_{who}_NAME"] = "Demo"
        os.environ[f"GIT_{who}_EMAIL"] = "demo@parallax.invalid"

    try:
        import pytest  # noqa: F401
    except ImportError:  # the app runs without the test tools: test_m8 only needs pytest's fixture decorator
        import types
        sys.modules["pytest"] = types.SimpleNamespace(fixture=lambda f=None, **k: f or (lambda g: g))
    from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
    from parallax import build, lifecycle, pilot, ui
    from parallax.accept import accept
    from parallax.agents.base import Review
    from parallax.core import Project
    import test_lifecycle_gates as test_m8

    build._spawn = lambda argv, env, cwd, log: os.getpid()  # nothing runs in the background; a live pid keeps the board's stale check off it
    import random
    import uuid
    ids = random.Random(7)  # the same task ids every start: the UI tester's tests may name them (a42589)
    uuid.uuid4 = lambda: uuid.UUID(int=ids.getrandbits(128), version=4)

    repo = root / "calc"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("# calc\n\nA calculater that adds numbers.\n\n## Install\n\nRun it in PowerShell.\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    test_m8.make_key()
    proj = Project.init(repo)
    from parallax import budgets
    budgets.choose(proj, "ask")  # the demo shows tasks, not the first-run spending question

    def docs(title, bottom, outcome):
        d = test_m8.docs()
        d["intent"] = (d["intent"].replace("fixing the README install steps", title)
                       .replace("Fix the README install steps so they work in WSL.", bottom)
                       .replace("A new user on WSL can follow them.", outcome))
        return d

    def run(work, title, bottom, outcome, steps, review=None):
        tid = pilot.intake(proj, work)["task"]
        maker = ScriptedAgent(steps=steps)
        build.run_mode(proj, tid, "pilot", FakeDrafter(docs(title, bottom, outcome)), lambda left, s: maker,
                       FakeChecker(reviews=[review or Review("pass")]), test_runner=junit_runner(),
                       preflight_runner=good_probe)
        return tid

    def launched(work, title, bottom, outcome):
        tid = pilot.intake(proj, work)["task"]
        pilot.draft_until_fit(proj, tid, FakeDrafter(docs(title, bottom, outcome)))
        lifecycle.approve(proj, tid, rule=pilot.launch_rule(proj, tid)[1])
        return tid

    def stopped(tid, why, **data):
        proj.ledger.append("stuck.raised", "parallax", why, task=tid, **data)
        proj.ledger.append("builder.finished", "parallax", "", task=tid, status="stuck")

    done = run("fix the typo calculater in the README", "fixing the calculator typo", "Fix the typo in the README.",
               "The README says calculator.", [("write", "README.md", "# calc\n\nA calculator that adds numbers.\n")])
    accept(proj, done)
    run("add a WSL install section", "adding WSL install steps", "Add WSL install steps to the README.",
        "A new user on WSL can install it.",
        [("write", "README.md", "# calc\n\n## Install on WSL\n\nsudo apt install python3\n"),
         ("write", ".env", "API_KEY=sk-live-123\n"), ("write", "notes.txt", "scratch\n")])
    cap = launched("rewrite the README install section", "rewriting the install section", "Rewrite the install section.",
                   "Each platform has its own steps.")
    stopped(cap, "the budget cap ran out ($2.10 of $2.00 estimated)", budget=True, cost_usd=2.1)
    err = launched("add a CONTRIBUTING file", "adding a contributing guide", "Add a short CONTRIBUTING.md.",
                   "New contributors know how to run the tests.")
    stopped(err, "error: the sandbox runtime exited with code 1 (srt: bwrap: No permissions to create new namespace)",
            error=True)
    run("fix the README install steps for WSL", "fixing the README install steps",
        "Fix the README install steps so they work in WSL.", "A new user on WSL can follow them.",
        [("write", "README.md", "# calc\n\nA calculater that adds numbers.\n\n## Install\n\nIn WSL: `pip install .`\n")],
        review=Review("pass", [], "whether pip is on PATH in a fresh WSL distro"))
    run("say which Python versions are supported", "stating supported Python versions",
        "Say which Python versions are supported.", "The README names Python 3.11 and later.",
        [("write", "README.md", "# calc\n\nA calculater that adds numbers.\n\nNeeds Python 3.11 or later.\n")])
    rw = launched("link the changelog from the README", "linking the changelog", "Link the changelog from the README.",
                  "The README links CHANGELOG.md.")
    proj.ledger.append("maker.started", "parallax", "", task=rw, stage="build")
    proj.ledger.append("rework.started", "parallax", "the link points at a file that doesn't exist", task=rw, cycle=1)
    b = launched("add a license badge", "adding a license badge", "Add a license badge.", "The README shows the license.")
    proj.ledger.append("maker.started", "parallax", "", task=b, stage="build")

    ui._save_link(repo, port, TOKEN)
    app = ui.UI(repo, port=port, find=lambda tool: f"/usr/bin/{tool}")  # fake agents: nothing to install
    print(app.url, flush=True)
    app.server.serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
