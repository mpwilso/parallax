import subprocess
from pathlib import Path

import pytest


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
