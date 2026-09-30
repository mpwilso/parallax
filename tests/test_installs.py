"""[build] setup works where the code runs: a src layout, a version from git tags, an editable install.

Setup runs once, as you, in a checkout of the base that's deleted afterwards. The setup commands
here do what installers do, offline: a .pth pointing into the checkout (setuptools and hatchling
editable installs), an editable finder mapping a package to its folder (setuptools' strict mode),
and a _version.py written from `git describe --tags` (setuptools_scm, hatch-vcs). No per-repo rule
in Parallax knows about any of them."""
import subprocess
import sys
from pathlib import Path

import pytest

from parallax import build, installs, lifecycle, testrun, tree
from parallax.core import POLICY_FILE, Project
from test_evals import plain_runner
from test_lifecycle_gates import docs, make_key
from test_sandbox_and_preflight import kinds

PYTEST_SITE = str(Path(pytest.__file__).resolve().parent.parent)  # so the venv's python can run pytest here
VENV = (f'{sys.executable} -m venv --without-pip "$PARALLAX_VENV" && SP=$(echo "$PARALLAX_VENV"/lib/python*/site-packages)'
        f' && echo {PYTEST_SITE} > "$SP/runner.pth"')


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def task_with(repo: Path, files: dict[str, str], setup: str, plan_files=("src/pkg/__init__.py", "tests/test_pkg.py")):
    """A repo at a tagged base with these files and this setup, and an approved task on it."""
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "tag", "v1.2.3")
    (repo / POLICY_FILE).write_text(f"[build]\nsetup = '{setup}'\n")
    make_key()
    proj = Project.init(repo)
    tid = proj.new_task("add mul", intent=True)["task"]
    plan = docs()["plan"].replace('files = ["README.md", "tests/test_readme.py"]', f"files = {list(plan_files)!r}".replace("'", '"'))
    plan = plan.replace('tests = ["tests/test_readme.py"]', 'tests = ["tests/test_pkg.py"]')
    for doc, text in {"intent": docs()["intent"], "plan": plan}.items():
        lifecycle._write(lifecycle.doc_path(proj, tid, doc), text)
    lifecycle.approve(proj, tid)
    return proj, tid


SRC = {
    ".gitignore": "src/pkg/_version.py\n",
    "src/pkg/__init__.py": "from ._version import __version__\n\n\ndef add(a, b):\n    return a + b\n",
    "tests/test_pkg.py": "def test_nothing_yet():\n    pass\n",
}
SRC_SETUP = VENV + (' && echo "$PWD/src" > "$SP/_pkg.pth"'
                    ' && echo "__version__ = \\"$(git describe --tags)\\"" > src/pkg/_version.py')


def maker_adds_mul(wt: Path) -> None:
    init = wt / "src/pkg/__init__.py"
    init.write_text(init.read_text() + "\n\ndef mul(a, b):\n    return a * b\n")
    (wt / "tests/test_pkg.py").write_text("from pkg import __version__, mul\n\n\n"
                                          "def test_mul():\n    assert mul(2, 3) == 6 and __version__ == 'v1.2.3'\n")


def test_a_src_layout_with_a_version_from_git_tags_runs_from_the_tree_under_test(repo):
    proj, tid = task_with(repo, SRC, SRC_SETUP)
    p = build.prepare(proj, tid)
    [ran] = kinds(proj, "setup.ran")
    assert ran["data"]["exit"] == 0 and ran["data"]["roots"] == ["src"]  # from the .pth; the pytest one is outside
    assert ran["data"]["generated"] == ["src/pkg/_version.py"]  # made from the tag: the checkout had .git and tags
    assert not (p.home / "setup-base").exists()

    # the maker's tests in the worktree: its own code, and the generated version, which git ignores
    assert (p.worktree / "src/pkg/_version.py").read_text() == '__version__ = "v1.2.3"\n'
    assert git(p.worktree, "status", "--porcelain") == ""
    maker_adds_mul(p.worktree)
    env = {"PATH": f"{p.venv}/bin:/usr/bin:/bin", **installs.env(p.venv, p.worktree)}
    assert env["PYTHONPATH"] == str(p.worktree / "src")
    out = subprocess.run(["python", "-c", "import pkg; print(pkg.mul(2, 3), pkg.__version__)"], cwd=p.worktree,
                         env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "6 v1.2.3", out.stderr

    # the check: the reviewed tree's copy, not the worktree and not the deleted checkout
    s = tree.stage(p.worktree, p.task["base"], p.home / "t.index")
    assert s.files == ["src/pkg/__init__.py", "tests/test_pkg.py"]  # the generated file is never in the diff
    (p.worktree / "src/pkg/__init__.py").write_text("broken after the review\n")
    results, _ = testrun.run(p.worktree, p.task["base"], s.tree, p.plan, p.home, p.venv, build.scrubbed_env(p.venv),
                             proj.policy.check["test_command"], plain_runner)
    assert results.ok and results.passed == 1, results.tail


def test_an_editable_finder_is_read_and_its_package_runs_from_the_tree(repo):
    """setuptools' strict editable mode: a finder maps the package to its folder, here under lib/."""
    files = {"lib/flat/__init__.py": "def add(a, b):\n    return a + b\n", "tests/test_pkg.py": "def test_ok():\n    pass\n"}
    finder = ('"MAPPING = {\\047flat\\047: \\047$PWD/lib/flat\\047}\\nNAMESPACES = {}\\n"')
    setup = VENV + f' && printf {finder} > "$SP/__editable___flat_1_0_finder.py"' + \
        ' && echo "import __editable___flat_1_0_finder" > "$SP/__editable__.flat-1.0.pth"'
    proj, tid = task_with(repo, files, setup, plan_files=("lib/flat/__init__.py", "tests/test_pkg.py"))
    p = build.prepare(proj, tid)
    assert kinds(proj, "setup.ran")[0]["data"]["roots"] == ["lib"]  # the folder that holds the package
    (p.worktree / "lib/flat/__init__.py").write_text("def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n")
    (p.worktree / "tests/test_pkg.py").write_text("from flat import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n")
    s = tree.stage(p.worktree, p.task["base"], p.home / "t.index")
    results, _ = testrun.run(p.worktree, p.task["base"], s.tree, p.plan, p.home, p.venv, build.scrubbed_env(p.venv),
                             proj.policy.check["test_command"], plain_runner)
    assert results.ok and results.passed == 1, results.tail


def test_roots_come_only_from_paths_into_the_setup_checkout(tmp_path):
    where, venv = tmp_path / "setup-base", tmp_path / "venv"
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    where.mkdir()
    (site / "a.pth").write_text(f"{where}/src\n/usr/lib/elsewhere\nimport something\n# {where}/commented\n")
    (site / "__editable___b_finder.py").write_text(f"MAPPING = {{'b': '{where}/lib/b', 'c': '/opt/c'}}\n")
    (site / "__editable___d_finder.py").write_text(f'MAPPING = {{"d": "{where}/d.py"}}\n')
    assert installs.roots(venv, where) == ["src", "lib", "."]
    assert installs.env(None, tmp_path) == {} and installs.files(None) == {}


def test_a_repo_without_setup_or_without_an_install_is_unchanged(repo):
    proj, tid = task_with(repo, SRC, VENV)  # a venv with nothing of the repo's installed
    p = build.prepare(proj, tid)
    assert kinds(proj, "setup.ran")[0]["data"]["roots"] == [] and installs.env(p.venv, p.worktree) == {}
