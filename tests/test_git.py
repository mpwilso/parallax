"""parallax/git.py: the one way Parallax runs git, with what each of its callers needs."""
import subprocess

import pytest

from parallax import git
from parallax.core import ParallaxError


def commit(repo, name, data: bytes):
    (repo / name).write_bytes(data)
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", name], check=True)


def test_out_strips_unless_asked_and_run_never_raises(repo):
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    assert git.out(repo, "rev-parse", "HEAD") == head.strip()
    assert git.out(repo, "rev-parse", "HEAD", strip=False) == head
    done = git.run(repo, "rev-parse", "-q", "--verify", "refs/heads/nope")
    assert done.returncode == 1 and done.stdout == ""


def test_a_failure_says_the_command_and_gits_own_error(repo):
    with pytest.raises(ParallaxError, match=r"^git checkout nope failed: error: pathspec 'nope' did not match"):
        git.out(repo, "checkout", "nope")
    with pytest.raises(ParallaxError, match="^git rev-parse -q --verify nope failed: no error from git$"):
        git.out(repo, "rev-parse", "-q", "--verify", "nope")


def test_text_is_utf8_with_bad_bytes_replaced_and_bytes_are_left_as_written(repo):
    commit(repo, "f.txt", "café │\r\n".encode() + b"\xff\n")
    assert git.out(repo, "show", "HEAD:f.txt") == "café │\n�"
    assert git.out(repo, "show", "HEAD:f.txt", text=False, strip=False) == "café │\r\n".encode() + b"\xff\n"


def test_env_adds_to_the_environment_and_input_goes_to_git(repo, tmp_path_factory, monkeypatch):
    commit(repo, "a.txt", b"a\n")
    index = tmp_path_factory.mktemp("index") / "index"
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Kept From This Process")
    blob = git.out(repo, "hash-object", "-w", "--stdin", input="new\n")
    git.out(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(index)})
    git.out(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},b.txt", env={"GIT_INDEX_FILE": str(index)})
    assert git.out(repo, "ls-files", env={"GIT_INDEX_FILE": str(index)}).split() == ["a.txt", "b.txt"]
    assert git.out(repo, "ls-files").split() == ["a.txt"]  # the repo's own index is untouched
    tree = git.out(repo, "write-tree", env={"GIT_INDEX_FILE": str(index)})
    made = git.out(repo, "commit-tree", tree, "-m", "x", env={"GIT_INDEX_FILE": str(index)})
    assert git.out(repo, "log", "-1", "--format=%an", made) == "Kept From This Process"
