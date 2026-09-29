Bottom line: Fix the five broken steps in the README's WSL2 install walkthrough, and add a test that pins the README's `parallax doctor` sample block to what `parallax doctor` actually prints.
Not looked at: I did not run the install steps on a fresh WSL2 distro, so the fixes are read from the text and from what the code expects. I did not check the Node version Ubuntu 24.04's apt currently ships, or the minimum version `@anthropic-ai/sandbox-runtime` states. I did not check how `git config --global --unset` treats a path with dots in it as a value pattern.

## Steps

1. `README.md`, step 4 (the tool install, currently `README.md:51`): stop taking Node from apt. Drop `nodejs npm` from the `apt install` line and add `ca-certificates` (NodeSource's script needs it). Then, still as root, in order: `apt remove -y nodejs npm`, `curl -fsSL https://deb.nodesource.com/setup_22.x | bash -`, `apt install -y nodejs`, and only then `npm install -g @anthropic-ai/sandbox-runtime`. The removal comes before the repository is added so apt's Node 18 cannot win. These commands run on the reader's machine, not in our build, so `domains` stays empty.

2. `README.md`: split the "Then as your user" sentence at `README.md:56` into its own numbered step 5, and start it with the switch that is missing: `su - <you>`. Then the two installer commands, uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and Claude Code (`curl -fsSL https://claude.ai/install.sh | bash`), then `source ~/.local/bin/env`, then `claude` to log in. The `source` line goes after both installers and before anything that calls `uv` or `claude`, because each installer writes `~/.local/bin/env` and leaves the running shell's PATH alone.

3. `README.md`: rewrite the clone step (now step 6, currently `README.md:57`) as one block, run as your user while Windows drives are still mounted:
   - `git config --global --add safe.directory /mnt/c/<path to parallax>` and the same for `/mnt/c/<path to parallax>/.git`, **before** the clone. Git refuses the `/mnt/c` source during the clone, and it trips on the repo path and the `.git` path separately, so both entries are needed. The new copy in `~/code/parallax` is owned by you and never needs an entry.
   - `git clone /mnt/c/<path to parallax> ~/code/parallax`.
   - `git config --global --unset safe.directory <each of the two paths>`, right after the clone. The exception was only for the clone, so it does not outlive it.
   - `git -C ~/code/parallax remote remove origin`, because `origin` points into `/mnt/c` and step 7 turns drives off, after which every `fetch` and `pull` there fails. One line says a GitHub remote can be set instead if the reader has one.
   - `uv tool install --editable "$HOME/code/parallax[claude]"`, then the existing optional SSH signing key line.

4. `README.md`: renumber the following steps. Harden becomes 7, "start Claude Code" becomes 8. The hardening step keeps its link to `#harden-wsl`, and the `### Harden WSL` heading and its text are not touched, so the anchor `parallax/doctor.py:26` points at still resolves.

5. `README.md:66`, the macOS and Linux paragraph: update "step 5" to the clone step's new number, and say the `/mnt/c` parts (the `safe.directory` pair and the `origin` removal) are Windows only. Nothing else in that paragraph changes.

6. `tests/test_m7.py`: add `test_the_readme_doctor_sample_matches_what_doctor_prints`, in the doctor section, after `test_doctor_report_lines_up_and_the_cli_exit_code_follows_failures` at line 105. It:
   - reads `Path(__file__).resolve().parents[1] / "README.md"` and pulls out the fenced block whose first line starts with `platform `, so the block is found by content and not by line number;
   - makes a home under `tmp_path` with an existing key at `.config/parallax/key`, mode 0600 in a 0700 parent, and `monkeypatch.setenv("HOME", ...)` so `_home()` renders the detail as `~/.config/parallax/key`. An existing key is what gives the sample's wording; a missing one would give `created ...`. The sample is not edited to match;
   - builds `m = machine(tmp_path)`, the hardened WSL2 machine from `tests/test_m7.py:18`, and points it at that key with `m.key = key` (`Machine` is a plain dataclass, so the helper's signature stays as it is);
   - asserts no check has status `doctor.FAIL`, so `ready.` is the honest last line;
   - asserts the block's lines equal `doctor.report(doctor.run(m)) + ["ready."]`, the exact list, which is what `_doctor()` in `parallax/cli.py:346` prints.

7. No change to `parallax/doctor.py` or `parallax/cli.py`. No new dependency, fixture file, or network call: the test reads the repo's own `README.md` off disk, so `dependencies`, `domains`, `outside_reads`, `binaries` and `symlinks` all stay empty.

## Tests

- New: `tests/test_m7.py::test_the_readme_doctor_sample_matches_what_doctor_prints`. Nothing today reads the README, so this is the test that proves the second half of the outcome. It fails if a check name, a detail string, the column padding computed in `report()`, the line order, or the `ready.` line changes without the README changing, and it fails if the README sample is edited away from the real output.
- Existing: `tests/test_m7.py::test_doctor_report_lines_up_and_the_cli_exit_code_follows_failures` and `test_doctor_on_a_hardened_wsl2_machine_is_ready`, to show the new test did not need `report()` or the checks to move.
- The full suite, to show the README edits and the new test break nothing else.

## Risks

- Renumbering is easy to get half right. The macOS and Linux paragraph's "step 5" reference and the hardening step's link are the two places that go stale.
- `git config --global --unset safe.directory <path>` treats the path as a value pattern, where `.` matches any character. For these paths that still matches the entry it should, but an unusual `<path to parallax>` could match more than intended.
- The test depends on `Path.home()` following `HOME`. That holds on Linux and macOS, which is where the suite runs.
- Fence extraction could grab the wrong block if another fenced block in the README ever starts with `platform `. Matching on that prefix keeps it narrow, but it is a content match, not a structural one.
- `setup_22.x` pins a major version. If the sandbox runtime later needs newer than 22, the README goes stale again, quietly, since no test covers the install steps.
- The build touches a file the test reads. A stray edit elsewhere in the sample block turns into a test failure rather than a silent doc drift, which is the point, but it makes the two edits one unit.

```toml
files = ["README.md", "tests/test_m7.py"]
tests = ["tests/test_m7.py"]
lines_changed = 55
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = "The parallax doctor sample block in README.md must not be edited. If it disagrees with the real output, the test setup is wrong, not the sample."
estimated_cost_usd = 2.60
budget_cap_usd = 4.00
```
