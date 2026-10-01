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
