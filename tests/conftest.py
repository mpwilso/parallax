import subprocess
from pathlib import Path

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """The outcome, on the item, so a fixture's teardown can keep a trace when the test failed."""
    outcome = yield
    setattr(item, "rep_" + outcome.get_result().when, outcome.get_result())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    return tmp_path


@pytest.fixture(autouse=True)
def private_home(tmp_path_factory, monkeypatch):
    """Worktrees and the approval key go under XDG dirs. Keep them out of the real home."""
    base = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    return base


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    """Commits in tests carry their own identity, never borrowed from your ~/.gitconfig.

    Inside the sandbox $HOME is hidden, so a test that leaned on yours failed there (seen in M12)."""
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Parallax Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@parallax.invalid")


@pytest.fixture(autouse=True)
def no_background_agents(monkeypatch):
    """Tests never call a model, so they never start a real pilot or builder.

    A test that reaches a real spawn fails here, loudly, instead of launching an agent in the
    background. Tests that need a spawn fake it with their own monkeypatch, which wins over this."""
    from parallax import build

    def refuse(argv, env, cwd, log):
        raise AssertionError(f"a test tried to start a real background process: {' '.join(argv[-3:])}. fake build._spawn")

    monkeypatch.setattr(build, "_spawn", refuse)


@pytest.fixture(autouse=True)
def no_real_ui_tester(monkeypatch):
    """The UI tester is a model, and its flows need a browser: tests fake both, or fail loudly."""
    from parallax import uitest

    def refuse(*a, **k):
        raise AssertionError("a test reached the real UI tester. fake uitest.TESTER")

    monkeypatch.setattr(uitest, "TESTER", refuse)
    monkeypatch.setattr(uitest, "ensure_tools", lambda: (_ for _ in ()).throw(AssertionError("fake uitest.ensure_tools")))


@pytest.fixture(autouse=True)
def no_real_reticle(monkeypatch):
    """Reticle is a model: tests fake reticle.WRITER. pytest.fail, not an assertion: Reticle records
    and survives an agent's errors, so an ordinary exception would pass unnoticed."""
    from parallax import reticle

    def refuse(*a, **k):
        pytest.fail("a test reached the real Reticle. fake reticle.WRITER")

    monkeypatch.setattr(reticle, "WRITER", refuse)


@pytest.fixture(autouse=True)
def memory_guards(monkeypatch):
    """parallax eval caps its own process with a watcher thread. In a test that process is pytest,
    so the guard is recorded here instead of started. tests/test_memcap.py runs real ones in a child."""
    from parallax import memcap
    calls: list[tuple] = []
    monkeypatch.setattr(memcap, "guard", lambda *a: calls.append(a))
    return calls


from parallax import core as _core, policy as _policy  # noqa: E402

SHIPPED = {"policy": _core.DEFAULT_POLICY, "reticle": dict(_policy.DEFAULT_RETICLE)}  # before any test changes them


@pytest.fixture(autouse=True)
def reticle_off_unless_turned_on(monkeypatch):
    """Reticle is on by default, and it's a model. A test project starts with it off, so tests of
    other things don't depend on it; Reticle's own tests turn it on in their policy. The shipped
    defaults are in SHIPPED, and tests/test_policy_and_stuck.py checks Reticle is on there."""
    text = SHIPPED["policy"].replace("[reticle]\nenabled = true ", "[reticle]\nenabled = false", 1)
    assert text != SHIPPED["policy"], "the default policy's [reticle] table moved: update this fixture"
    monkeypatch.setattr(_core, "DEFAULT_POLICY", text)
    monkeypatch.setattr(_policy, "DEFAULT_RETICLE", {**SHIPPED["reticle"], "enabled": False})
