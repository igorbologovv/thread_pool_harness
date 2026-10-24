# Study planner (first iteration)

This layer generates bounded, deterministic **campaign definitions**, not measurements.
Existing `experiments/common/campaign.py` remains the execution and analysis engine.

From the repository root, first inspect the planned matrix:

```bash
python3 -m experiments.common.study --spec experiments/studies/pilot.toml --check
```

Then generate campaign definitions and a JSON study manifest:

```bash
python3 -m experiments.common.study --spec experiments/studies/pilot.toml --write
```

The generated TOMLs live under `experiments/generated/queue-pilot/`. For each
generated file, execute the existing workflow (as a normal user for prepare):

```bash
python3 -m experiments.common.campaign prepare --experiment experiments/generated/queue-pilot/FILE.toml
python3 -m experiments.common.campaign plan --experiment experiments/generated/queue-pilot/FILE.toml
sudo python3 -m experiments.common.campaign collect --experiment experiments/generated/queue-pilot/FILE.toml
```

Use `analyze --campaign PATH` for statistical output after collection. The
planner does not invoke `sudo`, build binaries, or schedule background runs.

The TOML study config declares dimensions under `[axes]` and execution
budgets under `[study]`. Compatible combinations expand into one campaign
per worker count, work-unit count, granularity, and delivery mode, with queue
capacities and per-worker Hot-first backlog values expanded within each
campaign. Rayon/Bevy are not duplicated for irrelevant queue axes. Invalid
Hot-first capacity/backlog combinations are rejected and listed. A stable
unexpanded reference configuration such as Rayon is required.

`max_invocations` is a hard limit on invocations, **not** a bound on elapsed
time. The manifest estimates the number of invocations and physical measured
runs (including deep passes); actual benchmark time is workload-dependent.
The `settle_floor_seconds` field includes only fixed settle delays and is
not a reliable estimate of full campaign runtime.

Current limitations: a study uses one workload template; only the listed axes
are supported; common analysis compares within individual campaigns. Cross-
campaign reports, plots, aggregate quality gates, and autonomous selection
of subsequent experiments are deliberately deferred. For `rho` experiments,
the generated plan preview requires the existing service-time calibration.
