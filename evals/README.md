# Evals

`cases.toml` is the case set: real, already-merged fixes to small open-source projects, in the SWE-bench instance shape, with the maintainers' tests hidden from the agents and used to score.

`parallax eval --budget 20` runs cases through the same pipeline as `parallax do`, in scratch clones, and stops before the budget could run out. `parallax eval check` runs no model: it confirms each case's hidden tests fail at the base and pass at the fix. The design is in [docs/plan.md](../docs/plan.md#evals-2026-09-30-the-harness-on-the-current-pipeline).

`results/` holds one folder per run: a small JSON file per case, `run.json` with the fingerprint of what steered the agents, and `summary.md`. The two `2026-09-28-*` files came from an earlier harness that ran on the first design (a maker working straight from a goal) and was removed with it. Their numbers describe that path, not the current one.
