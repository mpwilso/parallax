"""Every tracked Markdown file renders the way it reads: no angle-bracket placeholder sits outside
code (GitHub would swallow it as an HTML tag), and every relative link and anchor resolves."""
import re
import subprocess
from pathlib import Path

from parallax.markdown import placeholders, prose  # the one rule, shared with the draft check (lint.py)

ROOT = Path(__file__).resolve().parent.parent

SKIP = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}


def markdown_files() -> list[Path]:
    """The repo's Markdown files, wherever it was cloned or copied. git's list when this folder is its
    own repository; otherwise every .md file in it, since a copy has no .git (or sits inside someone
    else's repository, whose ls-files would list none of these and pass on nothing)."""
    top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode == 0 and Path(top.stdout.strip()).resolve() == ROOT:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "*.md"], capture_output=True, text=True, check=True)
        return [ROOT / p for p in out.stdout.split("\0") if p]
    return sorted(p for p in ROOT.rglob("*.md") if not SKIP & set(p.relative_to(ROOT).parts[:-1])
                  and not any(part.endswith(".egg-info") for part in p.relative_to(ROOT).parts))


def test_the_markdown_files_are_found_with_or_without_git():
    names = {p.relative_to(ROOT).as_posix() for p in markdown_files()}
    assert {"README.md", "CLAUDE.md", "REVIEW.md", "docs/PRODUCT.md"} <= names


def slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    out = set()
    for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*$", prose(md.read_text()), re.M):
        out.add(slug(re.sub(r"`", "", m.group(1))))
    for m in re.finditer(r'\b(?:id|name)="([^"]+)"', md.read_text()):
        out.add(m.group(1))
    return out


def links(text: str) -> list[str]:
    text = prose(text)
    found = [m.group(1) for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text)]
    found += [m.group(1) for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)\)", text)]
    found += [m.group(2) for m in re.finditer(r'\b(href|src|srcset)="([^"]+)"', text)]
    return found


def test_no_placeholder_outside_code_in_any_markdown_file():
    bad = {}
    for md in markdown_files():
        if md.relative_to(ROOT).parts[:2] == ("docs", "tasks"):
            continue  # task records are hashed in the ledger, so they're kept as written
        found = placeholders(md.read_text())
        if found:
            bad[str(md.relative_to(ROOT))] = found
    assert not bad, f"angle-bracket placeholders outside code (GitHub renders them as tags): {bad}"


def test_the_check_sees_a_placeholder_and_not_code_or_html():
    assert placeholders("run `parallax show <task>`\n\n```\nsu - <you>\n```\n<p align=\"center\"><img src=\"x\"></p> <!-- <x> -->") == []
    assert placeholders("run parallax show <task> then su - <you>") == ["<task>", "<you>"]


def test_every_relative_link_and_anchor_resolves():
    bad = []
    for md in markdown_files():
        for target in links(md.read_text()):
            if re.match(r"(https?:|mailto:)", target):
                continue
            path, _, anchor = target.partition("#")
            dest = (md.parent / path).resolve() if path else md
            if not dest.exists():
                bad.append(f"{md.relative_to(ROOT)}: {target} (missing {path})")
            elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                bad.append(f"{md.relative_to(ROOT)}: {target} (no heading #{anchor})")
    assert not bad, "\n".join(bad)


def test_parallax_reviews_itself_with_the_general_template_plus_one_pass_for_its_own_rules():
    """The template ships with Parallax and is what `parallax init` and evals use. Parallax's own
    REVIEW.md is that template plus pass 6, without the housekeeping note, and nothing else."""
    from parallax import review
    own = (ROOT / "REVIEW.md").read_text().splitlines(keepends=True)
    extra = [line for line in own if line.startswith("6. Parallax's own rules:")]
    # the template's housekeeping note (2026-09-30) isn't in Parallax's own file, which keeps its rules
    note = "Housekeeping, such as a changelog entry, docs or a version number left out or not updated, is a note:\n" \
           "minor at most, never blocking, unless a pass above asks for it. It isn't behavior.\n\n"
    assert note in review.TEMPLATE and "Housekeeping" not in "".join(own)
    assert len(extra) == 1 and "".join(line for line in own if line not in extra) == review.TEMPLATE.replace(note, "")
    assert "Parallax" not in review.TEMPLATE.split("## Passes", 1)[1]  # no Parallax rule in the general passes


def test_the_readme_and_product_doc_describe_what_the_last_batch_built():
    """Merging and how long it takes, the over-limit budget question, the overlap heads-up, the conflict
    commands, a send-back changing only what you name, and a merged card without its worktree."""
    for name in ("README.md", "docs/PRODUCT.md"):
        text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        for said in ("Merging", "usually takes", "the median of the last five", "Allow $8 and launch",
                     "use the limit", "heads-up", "git merge --ff-only", "changes only what", "worktree"):
            assert said.lower() in text.lower(), f"{name} doesn't say {said!r}"
