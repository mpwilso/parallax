"""The UI sweep: the page with fake agents, at desktop and phone widths, light and dark, by keyboard
and with reduced motion, and the screenshots the README and docs/ui-runs show. No model runs.

    uv run --no-project --python 3.12 --with playwright==1.63.0 --with pytest --with-editable . scripts/ui_sweep.py

It prints one line per check and exits 1 if any fails. Screenshots go to docs/ui-runs/final-ui/.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
OUT = Path(os.environ.get("PARALLAX_SWEEP_OUT") or ROOT / "docs" / "ui-runs" / "final-ui")
DESKTOP, PHONE = (1280, 860), (390, 844)


def project():
    """A demo repo with a task in each state: Ready, working, stopped at its cap, and merged."""
    sys.modules.setdefault("pytest", types.SimpleNamespace(fixture=lambda f=None, **k: f or (lambda g: g)))
    from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
    from parallax import build, lifecycle, pilot, reticle
    from parallax.accept import accept, merge_now
    from parallax.agents.base import AgentResult, Finding, Review
    from parallax.core import Project
    import test_lifecycle_gates as gates

    home = Path(tempfile.mkdtemp(prefix="parallax-sweep-"))
    os.environ["XDG_DATA_HOME"], os.environ["XDG_CONFIG_HOME"] = str(home / "data"), str(home / "config")
    for who in ("AUTHOR", "COMMITTER"):
        os.environ[f"GIT_{who}_NAME"], os.environ[f"GIT_{who}_EMAIL"] = "Demo", "demo@parallax.invalid"
    repo = home / "calc"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("# calc\n\nA calculater that adds numbers.\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    gates.make_key()
    proj = Project.init(repo)
    build._spawn = lambda argv, env, cwd, log: os.getpid()  # a live process, so the working task stays working

    class Writer:  # Reticle's test of what was asked: fails on the typo, passes once it's fixed
        def __call__(self, limit, model):
            return self

        def run(self, goal, cwd, fn, stage="build", env=None):
            return AgentResult("done", "from pathlib import Path\n\n\ndef test_outcome_1_shows_usage():\n"
                                       "    assert 'Usage:' in Path('README.md').read_text()\n", 0.02)
    reticle.WRITER = Writer()

    def runner(cfg, cwd, cmd, env):
        if reticle.FILE not in cmd:
            return junit_runner()(cfg, cwd, cmd, env)
        out = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True,
                             env={**env, "PATH": f"{Path(sys.executable).parent}:{env['PATH']}"})
        return out.returncode, out.stdout + out.stderr

    def docs(title, outcome):
        d = gates.docs()
        d["intent"] = d["intent"].replace("fixing the README install steps", title).replace(
            "A new user on WSL can follow them.", outcome)
        return d

    def to_ready(title, outcome, review=None, text="# calc\n\nA calculator that adds numbers.\n"):
        tid = pilot.intake(proj, title)["task"]
        maker = ScriptedAgent(steps=[("write", "README.md", text)])
        build.run_mode(proj, tid, "pilot", FakeDrafter(docs(title, outcome)), lambda left, s: maker,
                       FakeChecker(reviews=[review or Review("pass")]), test_runner=runner, preflight_runner=good_probe)
        return tid

    done = to_ready("fixing the calculator typo", "The README says calculator.")
    accept(proj, done)
    merge_now(proj, done)
    to_ready("adding a usage line", "The README shows how to add two numbers.",
             Review("pass", [Finding("minor", "README.md:3", "the example could show a negative number", "behavior")],
                    "whether the example runs as written"),
             "# calc\n\nA calculator that adds numbers.\n\nUsage: calc 2 3\n")
    stuck = pilot.intake(proj, "supporting subtraction")["task"]
    pilot.draft_until_fit(proj, stuck, FakeDrafter(docs("supporting subtraction", "calc 5 3 prints 2.")))
    lifecycle.approve(proj, stuck, rule="demo")
    for _ in range(2):
        proj.ledger.append("tests.recorded", "parallax", "", task=stuck, tree="t", exit=1,
                           per_file={"tests/test_readme.py": [1, 3, 0]}, passed=1, total=3, harness_reset=[])
    proj.ledger.append("stuck.raised", "parallax", "the budget cap ran out ($2.30 of $2.20 estimated) while rework was fixing tests",
                       task=stuck, budget=True)
    proj.ledger.append("builder.finished", "parallax", "", task=stuck, status="stuck")
    working = pilot.intake(proj, "adding a --help flag")["task"]
    pilot.draft_until_fit(proj, working, FakeDrafter(docs("adding a --help flag", "calc --help prints the usage.")))
    lifecycle.approve(proj, working, rule="demo")
    proj.ledger.append("reticle.started", "parallax", "", task=working)
    proj.ledger.append("reticle.recorded", "reticle", "1 test kept", task=working, kept=[], weak=[], cost_usd=0.02)
    proj.ledger.append("maker.started", "parallax", "", task=working, stage="build")
    return proj


def main() -> int:
    from playwright.sync_api import sync_playwright
    from parallax.ui import UI
    proj = project()
    app = UI(proj.root, port=0)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""), flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PARALLAX_BROWSER") or None)

        def page(size, scheme, motion="no-preference"):
            ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme, reduced_motion=motion)
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(app.url)
            pg.wait_for_selector(".row")
            time.sleep(0.4)
            return ctx, pg, errors

        def open_first(pg, chip):
            target = pg.locator(f".row:has(.tag:text-is('{chip}'))").first
            title = target.locator(".title").inner_text()
            target.click()
            pg.wait_for_function("t => (document.getElementById('card-title') || {}).textContent === t", arg=title)
            time.sleep(0.3)

        for scheme in ("light", "dark"):
            sfx = "" if scheme == "light" else "-dark"
            ctx, pg, errors = page(DESKTOP, scheme)
            check(f"desktop {scheme}: the overview replaces the empty pane", pg.locator("#idle .overview li").count() >= 2)
            pg.screenshot(path=str(OUT / f"queue{sfx}.png"))
            rows = pg.locator("#queue .row:not(.finished)")
            check(f"desktop {scheme}: every open row has a chip, a strip and spend",
                  all(rows.nth(i).locator(".tag").count() == 1 and rows.nth(i).locator(".st").count() == 5
                      for i in range(rows.count())))
            moving = pg.evaluate("[...document.querySelectorAll('.portrait')].filter(p => "
                                 "getComputedStyle(p.querySelector('.bust')).animationName === 'bob').map(p => p.dataset.agent)")
            check(f"desktop {scheme}: only the working agent moves", moving == ["maker"], str(moving))
            open_first(pg, "Ready")
            check(f"desktop {scheme}: the Ready card offers Accept and merge", pg.locator("#opt-merge").count() == 1)
            check(f"desktop {scheme}: the card has one strip and an Ask box",
                  pg.locator("#card .strip").count() == 1 and pg.locator("#ask").count() == 1)
            check(f"desktop {scheme}: ledger ids are quiet links", pg.locator("#card a.cite").count() >= 1)
            pg.screenshot(path=str(OUT / f"ready{sfx}.png"), full_page=True)
            open_first(pg, "Failed")
            check(f"desktop {scheme}: a stalled cap card recommends sending it back",
                  pg.locator("#opt-send-back.primary").count() == 1)
            pg.screenshot(path=str(OUT / f"needs-you{sfx}.png"), full_page=True)
            check(f"desktop {scheme}: no script errors", not errors, "; ".join(errors))
            ctx.close()

            ctx, pg, errors = page(PHONE, scheme)
            wide = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(f"phone {scheme}: no sideways scroll on the list", wide <= 0, f"{wide}px")
            pg.screenshot(path=str(OUT / f"phone-queue{sfx}.png"), full_page=True)
            open_first(pg, "Ready")
            wide = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(f"phone {scheme}: no sideways scroll on a card", wide <= 0, f"{wide}px")
            pg.screenshot(path=str(OUT / f"phone-ready{sfx}.png"), full_page=True)
            check(f"phone {scheme}: no script errors", not errors, "; ".join(errors))
            ctx.close()

        ctx, pg, errors = page(DESKTOP, "light", "reduce")
        check("reduced motion: nothing moves", pg.evaluate(
            "[...document.querySelectorAll('.portrait .bust, .portrait .glow')].every(n => getComputedStyle(n).animationName === 'none')"))
        ctx.close()

        ctx, pg, errors = page(DESKTOP, "light")
        pg.keyboard.press("n")
        pg.wait_for_selector("#card:not([hidden])")
        check("keyboard: n opens what waits on you, with focus on its title",
              pg.evaluate("document.activeElement.id") == "card-title")
        pg.keyboard.press("j")
        time.sleep(0.4)
        pg.keyboard.press("k")
        time.sleep(0.4)
        tabbed = []
        for _ in range(40):
            pg.keyboard.press("Tab")
            tabbed.append(pg.evaluate("document.activeElement.id || document.activeElement.className"))
        check("keyboard: Tab reaches the options, the Ask box and the ledger links",
              any(t.startswith("opt-") for t in tabbed) and "ask" in tabbed and any("cite" in t for t in tabbed), str(tabbed[:12]))
        pg.keyboard.press("Escape")
        check("keyboard: Escape closes the card", pg.locator("#card").is_hidden())
        check("keyboard: no script errors", not errors, "; ".join(errors))
        ctx.close()
        browser.close()
    app.close()
    print(f"{sum(results)} of {len(results)} checks passed; screenshots in {OUT}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
