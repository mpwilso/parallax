Bottom line: Rewrite the WSL install steps in README.md so a reader following them in a fresh dedicated distro ends up with a working Parallax, and add a small test that pins the README's tool list and doctor sample to what `parallax doctor` actually checks.
Not looked at: whether NodeSource's `setup_22.x` script name or URL has changed (no network here), whether Ubuntu 24.04's apt Node is new enough today, and I did not run the steps on a fresh distro. I did not read anything under `docs/tasks/`.

## Steps

1. `README.md`, step 3 (line 50). Keep the `adduser` and `usermod` lines, and say plainly that `wsl -d parallax` drops you into a root shell and that every later "as your user" step means `su - <you>` first. The default user from `/etc/wsl.conf` only applies after step 6, so `su -` is the only way there until then.

2. `README.md`, step 4 (lines 51 to 56). Split the single apt line. Install `git bubblewrap socat ripgrep python3 curl ca-certificates` from apt, and do not install apt's `nodejs` and `npm`: Ubuntu 24.04 ships Node 18, and `@anthropic-ai/sandbox-runtime` needs Node 20 or newer. Add, as root, before the `npm install -g` line:
   - `apt remove -y nodejs npm` (harmless if they were never installed, and needed if a reader already ran the old step)
   - `curl -fsSL https://deb.nodesource.com/setup_22.x | bash -`
   - `apt install -y nodejs`
   Then `npm install -g @anthropic-ai/sandbox-runtime`. State that this is done while Windows drives are still mounted and the distro still has network, which it does in every step here.

3. `README.md`, step 4, user tools. Move uv and Claude Code into their own labelled block that starts with `su - <you>`. After uv's installer, add `source ~/.local/bin/env` on its own line, with one sentence saying uv lands in `~/.local/bin`, which a shell started before the install does not have on its PATH. Say that a new login shell works too. Do the same before the first use of `claude`.

4. `README.md`, step 5 (lines 57 to 61). Keep the clone from `/mnt/c`, and wrap it in the two things that actually make it work:
   - Before the clone: `git config --global --add safe.directory /mnt/c/<path to parallax>`. Without it git refuses the clone with "detected dubious ownership in repository", because the drvfs mount reports a different owner.
   - After the clone: `git config --global --unset safe.directory /mnt/c/<path to parallax>`, so the exception does not outlive the one clone that needed it.
   - After the clone: `git -C ~/code/parallax remote remove origin`, with one sentence saying why. Step 6 turns off automount, so an `origin` under `/mnt/c` is unreachable from then on and every `git fetch` fails. Add that you can `git remote add origin <your github url>` instead if you have one.
   Keep the `uv tool install --editable "$HOME/code/parallax[claude]"` line and the `[claude]` extra name as is. Put `source ~/.local/bin/env` before it if the reader is in the same shell as step 4.

5. `README.md`, step 7 and the `parallax doctor` block (lines 64 to 72). Say that after the `wsl --terminate` in step 6 you land in a fresh shell as your user, so `parallax` resolves without further work. Leave the sample output at lines 74 to 82 unchanged: `report()` still prints exactly those columns for a hardened WSL2 machine.

6. `README.md`, line 66. Rewrite the macOS and Linux paragraph so the tool list matches `check_sandbox` (parallax/doctor.py:87): on macOS install git, Node 20 or newer, `@anthropic-ai/sandbox-runtime`, uv and Claude Code, so `srt` is on PATH; on Linux add bubblewrap and socat. Drop the reading that socat is wanted on macOS.

7. `README.md`, `### Harden WSL` (lines 86 to 104). Leave the heading text and the wsl.conf block alone so the `README.md#harden-wsl` anchor in parallax/doctor.py:26 still resolves. Only add one sentence under the automount block: once drives are off, anything under `/mnt/c`, including a git remote, is gone.

8. New file `tests/test_readme.py`. Read `README.md` from the repo root and assert, against `parallax.doctor`:
   - the `### Harden WSL` heading exists, so `doctor.HARDENING`'s anchor resolves;
   - the fenced sample block matches `doctor.report(doctor.run(m))` for a hardened WSL2 machine, line for line, with the approval key line's path allowed to differ (the fake machine's key lives under `tmp_path`);
   - the macOS and Linux paragraph names every tool `check_sandbox` requires on each platform, and names no sandbox tool it does not require.
   Reuse the `machine` helper's shape from tests/test_m7.py:18 rather than importing it across test modules.

No network domain, outside read, binary, symlink or dependency is needed. The NodeSource URL and the `setup_22.x` name are text in the README, not something the build fetches, and the new test reads only `README.md` inside the worktree.

## Tests

New, because nothing today ties the README to `doctor`:

- `tests/test_readme.py::test_the_doctor_sample_matches_what_report_prints` builds a hardened WSL2 machine, runs `doctor.run` and `doctor.report`, and compares against the fenced block in the README. This is the guard on the outcome that the sample output still matches `report()`.
- `tests/test_readme.py::test_the_sandbox_tools_paragraph_matches_check_sandbox` asserts the macOS and Linux paragraph lists `srt` for macOS and bubblewrap, socat and srt for Linux, derived from `check_sandbox` on each platform rather than hardcoded.
- `tests/test_readme.py::test_the_hardening_anchor_resolves` asserts the heading `doctor.HARDENING` points at is present.

Existing, unchanged and expected to stay green:

- `tests/test_m7.py` in full, in particular `test_doctor_report_lines_up_and_the_cli_exit_code_follows_failures` and `test_windows_reach_is_a_warning_with_the_hardening_link`, which pin the output the README quotes. No `parallax/` file changes, so any failure here means the test file itself is wrong.
- `tests/test_m8.py` in full.

Also run `parallax lint docs/tasks/003876/intent.md` and `parallax lint docs/tasks/003876/plan.md`.

Not covered by any test, and checked by reading: that the rewritten steps actually run on a fresh distro. The NodeSource step, the `safe.directory` add and unset, and the `su -` step are prose, and no test here executes them.

## Risks

- The intent says the test suite stays unchanged. This plan adds `tests/test_readme.py` and edits no existing test. Without it nothing catches the README drifting from `check_sandbox` again, which is one of the four bugs being fixed. If you want the suite untouched, drop step 8 and the plan's only remaining proof is the lint run.
- The doctor sample comparison is brittle by design. Any future change to `report()`'s column width or to a check's detail string will fail `test_the_doctor_sample_matches_what_report_prints` until the README is updated. That is the point, but it makes the README a thing tests can break.
- `apt remove -y nodejs npm` on a distro that never had them prints a no-op; on a distro where a reader installed other npm globals it removes them too. The steps are written for a fresh dedicated distro, so this is stated, not defended against.
- Pinning `setup_22.x` dates the README. If NodeSource retires that URL the step breaks, and no test here will notice.
- Removing `origin` means `git pull` in `~/code/parallax` does nothing until the reader adds a remote. That is better than a remote that fails, but it is a behaviour change for anyone who followed the old steps.
- The `safe.directory` unset uses the exact path string that was added. A reader who types a different path spelling in the two commands leaves the exception in `~/.gitconfig`.
- Parsing the README by heading and fence in the test is sensitive to the section order, which the intent says to keep anyway.

```toml
files = ["README.md", "tests/test_readme.py"]
tests = ["tests/test_readme.py", "tests/test_m7.py", "tests/test_m8.py"]
lines_changed = 95
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = "No change under parallax/. The '### Harden WSL' heading text, the wsl.conf block, the doctor sample output block, the section order and the '[claude]' extra name must be byte-identical to the previous README, except where a step above names them."
estimated_cost_usd = 1.20
budget_cap_usd = 1.60
```
