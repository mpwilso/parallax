# Evals

`cases.toml` is the case set: real, already-merged fixes to small open-source projects, in the SWE-bench instance shape, with the maintainers' tests hidden from the agents and used to score.

`parallax eval --budget 20` runs cases through the same pipeline as `parallax do`, in scratch clones, and stops before the budget could run out. `parallax eval check` runs no model: it confirms each case's hidden tests fail at the base and pass at the fix. The design is in [docs/plan.md](../docs/plan.md#evals-2026-09-30-the-harness-on-the-current-pipeline).

A run is capped at 4 GB of memory, and each run of a repo's tests inside it at 3 GB (`parallax/memcap.py`), so a test that never ends is stopped with a plain message instead of taking the machine down.

`results/` holds one folder per run: a small JSON file per case, `run.json` with the fingerprint of what steered the agents, and `summary.md`. The two `2026-09-28-*` files came from an earlier harness that ran on the first design (a maker working straight from a goal) and was removed with it. Their numbers describe that path, not the current one.

Three runs that no doc links, kept as published:

- [2026-09-30-7dd776](results/2026-09-30-7dd776/summary.md): 6 real fixes with Reticle on, on parallax 42e435b. All 6 reached Ready, 5 passed the hidden tests; humanize-174 got past Second Eye and Reticle. $6.53.
- [2026-09-30-221c3e-seeded](results/2026-09-30-221c3e-seeded/summary.md): Second Eye alone on the 23 broken versions and 11 real fixes of the 9ba608 and 48a3a5 seeded runs, outcomes without their asked or inferred marks. It caught 23 of 23, with 7 false alarms on the real fixes. $0.69.
- [2026-09-30-f5ff69-seeded](results/2026-09-30-f5ff69-seeded/summary.md): the same, with outcomes marked asked or inferred and findings told to rest on asked ones. It caught 20 of 23, with 4 false alarms. $0.75. [docs/evals.md](../docs/evals.md) compares the two.
