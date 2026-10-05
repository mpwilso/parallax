"""The one place that reads a sandbox that didn't start. Every pattern has real output behind it, or says it hasn't."""
import inspect

from parallax import sandboxfail
from realout import REAL, line, real

ENOSPC = line("bwrap-0.9.0-disable-userns.stderr")


def test_every_pattern_is_real_output_or_says_it_is_unverified():
    source = inspect.getsource(sandboxfail).splitlines()
    for pattern, _, fixture in sandboxfail.PATTERNS:
        if fixture:
            assert pattern in real(fixture), pattern
        else:
            at = next(i for i, text in enumerate(source) if f'("{pattern}"' in text)
            assert "unverified" in source[at - 1], pattern


def test_every_real_file_reads_as_a_sandbox_that_didnt_start_and_says_where_it_came_from():
    readme = (REAL / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in REAL.iterdir() if p.name != "README.md")
    assert len(files) == 7
    for name in files:
        assert f"`{name}`" in readme, name
        found = sandboxfail.read(real(name))
        assert found and found.line == line(name), name  # the line as printed, never the echo's "exit 1"


def test_another_tools_namespace_error_reads_as_nothing():
    for text in ['kubectl: namespaces "dev" not found', "error: namespace x", "bwrap: something failed", "", None]:
        assert sandboxfail.read(text) is None, text


def test_the_line_is_found_inside_a_longer_message():
    found = sandboxfail.read(f"something went wrong: {ENOSPC}\nmore")
    assert found == sandboxfail.Failure(ENOSPC, sandboxfail.NAMESPACE)


def test_one_next_action_naming_parallax_doctor():
    namespace = sandboxfail.read(ENOSPC)
    on_wsl, off_wsl = sandboxfail.hint(namespace, True), sandboxfail.hint(namespace, False)
    assert len(on_wsl) == len(off_wsl) == 1
    assert "docs/wsl.md" in on_wsl[0] and "docs/wsl.md" not in off_wsl[0]
    assert all("sysctl line in the README's setup step 1" in h and "parallax doctor" in h for h in on_wsl + off_wsl)
    tools = sandboxfail.hint(sandboxfail.read(real("srt-1.0.0-no-tools-on-path.stderr")), True)
    assert len(tools) == 1 and "install what it names" in tools[0] and "parallax doctor" in tools[0]
    assert sandboxfail.hint(None, True) == []
