import pytest

from parallax.core import POLICY_FILE, Project


def test_non_string_ruling_is_a_clean_error(repo):
    (repo / POLICY_FILE).write_text('[actions]\n"shell.run" = { a = 1 }\n')
    with pytest.raises(Exception, match="bad policy"):
        Project.init(repo)
