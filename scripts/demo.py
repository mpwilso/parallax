"""Record docs/brand/demo.gif: the UI with scripted agents, dark theme, in about fifteen seconds.

Type a task, watch Focus, Reticle, Maker and Second Eye take their turns, the card turns Ready, open it,
accept. No model runs and nothing costs anything: the agents are the test suite's fakes, slowed
down so the page shows each stage. Rerun it whenever the UI changes:

    scripts/demo.py

It needs the pinned Playwright (scripts/test.sh fetches its Chromium), Pillow to write the GIF, and
pytest to run Reticle's test:

    uv run --no-project --python 3.12 --with playwright==1.63.0 --with pillow --with pytest --with-editable . scripts/demo.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

FRAME_EVERY = 0.25   # seconds between frames
SECONDS = 18         # the GIF's length, at most
WIDTH, HEIGHT = 1100, 700
GIF_WIDTH = 800
OUT = ROOT / "docs" / "brand" / "demo.gif"


def write_gif(frames: Path, out: Path, every: float) -> tuple[int, float]:
    """Frames to one GIF: scaled down, a shared palette, and a run of identical frames held as one."""
    from PIL import Image
    images, durations = [], []
    for path in sorted(frames.glob("frame*.png")):
        im = Image.open(path).convert("RGB")
        im = im.resize((GIF_WIDTH, round(im.height * GIF_WIDTH / im.width)), Image.LANCZOS)
        if images and list(im.getdata()) == list(images[-1].getdata()):
            durations[-1] += every * 1000
        else:
            images.append(im)
            durations.append(every * 1000)
    palette = images[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames_q = [im.quantize(colors=128, palette=palette, dither=Image.Dither.NONE) for im in images]
    frames_q[0].save(out, save_all=True, append_images=frames_q[1:], duration=durations, loop=0, optimize=True, disposal=1)
    return len(images), sum(durations) / 1000


def main() -> None:
    import types
    sys.modules.setdefault("pytest", types.SimpleNamespace(fixture=lambda f=None, **k: f or (lambda g: g)))
    from playwright.sync_api import sync_playwright

    from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
    from parallax import build, reticle, ui
    from parallax.agents.base import AgentResult, Review
    from parallax.core import Project
    import test_lifecycle_gates as gates

    home = Path(tempfile.mkdtemp(prefix="parallax-demo-"))
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
    from parallax import budgets
    budgets.choose(proj, "ask")  # the demo shows tasks, not the first-run spending question
    build._spawn = lambda argv, env, cwd, log: os.getpid()  # the pilot runs in this process, below

    class Slow(FakeDrafter):  # each draft takes a moment, so the page shows Focus at work
        def run(self, *a, **k):
            time.sleep(2.2)
            return super().run(*a, **k)

    class SlowChecker(FakeChecker):
        def check(self, brief):
            time.sleep(1.8)
            return super().check(brief)

    class SlowReticle:  # writes its test of what was asked, which fails on the typo and passes once it's fixed
        def __call__(self, limit, model):
            return self

        def run(self, goal, cwd, fn, stage="build", env=None):
            time.sleep(2.2)
            return AgentResult("done", "from pathlib import Path\n\n\ndef test_outcome_1_the_readme_says_calculator():\n"
                                       "    assert \"calculator\" in Path(\"README.md\").read_text()\n", 0.02)

    reticle.WRITER = SlowReticle()
    plan_tests = junit_runner()

    def runner(cfg, cwd, cmd, env):  # Reticle's file runs for real; the plan's tests are the fake's
        if reticle.RUN_NAME not in cmd:
            return plan_tests(cfg, cwd, cmd, env)
        out = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True,
                             env={**env, "PATH": f"{Path(sys.executable).parent}:{env['PATH']}"})
        return out.returncode, out.stdout + out.stderr

    docs = gates.docs()
    docs["intent"] = docs["intent"].replace("fixing the README install steps", "fixing the calculator typo") \
        .replace("Fix the README install steps so they work in WSL.", "Fix the typo in the README.") \
        .replace("A new user on WSL can follow them.", "The README says calculator.")

    def run_pilot(tid):
        maker = ScriptedAgent(steps=[("call", lambda cwd: time.sleep(3.0)),
                                     ("write", "README.md", "# calc\n\nA calculator that adds numbers.\n")])
        build.run_mode(proj, tid, "pilot", Slow(docs), lambda left, s: maker, SlowChecker(reviews=[Review("pass")]),
                       test_runner=runner, preflight_runner=good_probe)

    app = ui.UI(repo, port=0, find=lambda tool: f"/usr/bin/{tool}")  # fake agents: nothing to install
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    frames = Path(tempfile.mkdtemp(prefix="parallax-demo-frames-"))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PARALLAX_BROWSER") or None)
        page = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, color_scheme="dark").new_page()
        page.goto(app.url)
        page.wait_for_selector("#work")
        n = 0

        def frame():
            nonlocal n
            page.screenshot(path=str(frames / f"frame{n:03d}.png"))
            n += 1

        started = time.time()
        page.locator("#work").click()
        for ch in "fix the typo calculater in the README":
            page.keyboard.type(ch)
            if int((time.time() - started) / FRAME_EVERY) >= n:
                frame()
            time.sleep(0.03)
        page.keyboard.press("Enter")
        page.wait_for_selector(".row")
        tid = list(proj.tasks())[-1]
        threading.Thread(target=run_pilot, args=(tid,), daemon=True).start()
        accepted = False
        while time.time() - started < SECONDS - 0.5:
            frame()
            if not accepted and page.locator(".tag.good").count():
                time.sleep(0.6)
                frame()
                page.locator(".tag.good").first.click()
                page.wait_for_selector("#opt-accept")
                for _ in range(4):
                    frame()
                    time.sleep(FRAME_EVERY)
                page.locator("#opt-accept").click()
                page.wait_for_selector("#merge")
                accepted = True
            time.sleep(FRAME_EVERY)
        browser.close()
    app.close()
    kept, seconds = write_gif(frames, OUT, FRAME_EVERY)
    shutil.rmtree(frames, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.2f} MB, {kept} distinct frames of {n}, {seconds:.1f} s")


if __name__ == "__main__":
    main()
