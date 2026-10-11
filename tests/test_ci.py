"""The CI workflow runs untrusted pull requests, so it gets the least it can work with."""
import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def test_every_workflow_is_least_privilege_and_pinned():
    files = list(WORKFLOWS.glob("*.yml"))
    assert files
    for wf in files:
        text = wf.read_text()
        assert re.search(r"^permissions:\n  contents: read\n", text, re.M), f"{wf.name}: set permissions to contents: read"
        assert "pull_request_target" not in text, f"{wf.name}: pull_request_target runs untrusted code with secrets"
        assert "secrets." not in text, f"{wf.name}: no secrets reach the tests"
        for line in re.findall(r"uses:\s*(\S+)", text):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", line), f"{wf.name}: pin {line} to a commit sha"


def test_ci_runs_the_suite_again_from_a_second_clone_at_a_path_with_a_space():
    """Four tests once failed in a scratch copy: CI now proves the suite passes wherever it's cloned."""
    text = (WORKFLOWS / "tests.yml").read_text()
    m = re.search(r'second="([^"]+)"', text)
    assert m and " " in m.group(1) and not m.group(1).startswith("$GITHUB_WORKSPACE")
    assert 'git clone --quiet --no-checkout "$GITHUB_WORKSPACE" "$second"' in text
    assert text.count("scripts/test.sh\n") >= 2  # the usual run, then the second clone's


def test_ci_runs_once_per_push_and_again_only_for_a_fork_pull_request():
    """Every branch push runs (the merge waits on the branch run and on master's). A pull request from
    this repo already ran on its push, so only one from a fork runs again; a newer push cancels an
    older run of the same branch, except on master."""
    text = (WORKFLOWS / "tests.yml").read_text()
    assert re.search(r"^on:\n  push:\n    branches: \['\*\*'\]\n  pull_request:\n", text, re.M)
    jobs = text.split("\njobs:\n", 1)[1]
    job_count = len(re.findall(r"^  [\w-]+:\n", jobs, re.M))
    fork_only = ("    if: github.event_name != 'pull_request' || "
                 "github.event.pull_request.head.repo.full_name != github.repository\n")
    assert job_count >= 1 and jobs.count(fork_only) == job_count
    assert ("concurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n"
            "  cancel-in-progress: ${{ github.ref != 'refs/heads/master' }}\n") in text


def test_dependabot_proposes_action_updates_a_week_after_release():
    text = (WORKFLOWS.parent / "dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in text and "interval: monthly" in text
    assert "cooldown:\n      default-days: 7\n" in text
