# Normalized-load BLS study

This study is **not** an exhaustive factorial sweep. It compares five scheduler
anchors (Rayon; MPMC SP0 and AgaveBackoff; RTRB SP0 with backlog 1/2), at
2/4/8/16 workers, under periodic and bursty submission.

## Load selection

Six offered-load levels: 25%, 50%, 75%, 93.75%, 110%, 200%. The first four
sample low, medium, high and near-saturation regimes, with closer attention near
the heavy-traffic boundary (queueing delay often grows nonlinearly with
rho/(1-rho)). The last two are **finite-duration overload stress tests** and
must not be interpreted as steady-state queueing regimes. 100% and 105% are
not separate levels, to keep the runtime manageable.

The load fraction is **offered arrival rate / reference saturated throughput**,
not measured CPU utilization. The same per-worker-count reference capacity is
used for every scheduler configuration, ensuring matched absolute arrival rates.
Initial capacities (WU/s) are 2:1294, 4:2583, 8:4792, 16:5224, taken from the
archived all-at-once Rayon results. Prefer supplying new, validated capacity
measurements from the same binary/host using `--reference-json` before final
collection; retain the JSON and generated plan for provenance. This approach
does not claim identical actual CPU occupancy at any specified load fraction.

## Dataset, schedule and measurements

One deterministic cache of **2500 distinct BLS work units** is shared across
campaigns. Each campaign executes the first N work units without wrapping, with

    arrival_rate = load_fraction * reference_capacity[workers]
    N = ceil(arrival_rate * 0.200 seconds) + 1

The schedule spans **at least 200 ms**, measured from the first to the last
*scheduled* arrival. Actual delivery may run later if bounded-queue backpressure
blocks the producer. The harness records scheduled-vs-actual arrival lateness;
inspect this metric particularly in the 110% and 200% overload experiments.
Elapsed time includes the final drain after submission. No independent
per-task latency is recorded yet. Datasets are loaded/generated outside measured
intervals. All scheduler conditions within a given worker/load/mode campaign
run the same N tasks and the same arrival schedule within randomized blocks.
Changing N changes the prefix of the fixed dataset, so avoid pooling unmatched
rows and check duration effects in pilot runs.

Study size: 4 worker levels × 6 loads × 2 delivery modes × 5 scheduler
configurations × 20 randomized blocks = **4800 invocations**, with two measured
and one warm-up runs each (**9600 measured, 4800 warmups**). Standard perf,
q64, and zero settling are held fixed. This is substantially smaller than a
full policy/backlog/spin factorial sweep. Total wall clock also includes builds,
dataset preparation and analysis; no timing estimate is promised.

## Generate (does not benchmark)

    python3 -m experiments.studies.bls_normalized_load --check
    python3 -m experiments.studies.bls_normalized_load --write

To replace provisional reference capacities, create a JSON object such as
`{"2":1294,"4":2583,"8":4792,"16":5224}` and pass
`--reference-json /path/to/references.json` for both commands.

This generates 48 TOML campaign definitions and `plan.json` under
`experiments/generated/bls-normalized-load/`.

## Execute

Run per-campaign using the existing prepare/collect/analyze pipeline. For example:

    python3 -m experiments.common.campaign prepare --experiment experiments/generated/bls-normalized-load/w2-load02500-fixed-arrivals.toml
    python3 -m experiments.common.campaign collect --experiment experiments/generated/bls-normalized-load/w2-load02500-fixed-arrivals.toml

Repeat for campaign files, or use an explicit serial loop after a pilot. Do not
execute production campaigns from a dirty tracked working tree. Existing campaign
resume checks still apply. Benchmark execution requires a compatible Rust build,
Linux perf permissions, and a stable host.

**Important:** this is an independently designed new acquisition series.
Do not pool its confidence intervals with the historical 1000-WU campaigns.
Use block-paired analysis within each campaign, inspect distribution/drift
and arrival lateness, then analyze cross-worker comparisons separately.
