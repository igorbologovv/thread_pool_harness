# Synchronized per-thread BLS PMU diagnostic

This is a **measurement-methodology change**; do not interpret any results
before building, running unit tests, verifying the hardware perf JSON format
and passing the raw-data audit on the actual benchmark host.

## Design

- 2 workers, 256 BLS verifications, fixed arrivals, 647 WU/s (50%).
- MPMC Spin(0) and RTRB backlog 2; 20 randomized blocks.
- Four discarded warmups per process, 2 measured runs in each of 2 separate
  perf passes = **160 measured executions**.
- The harness creates the worker threads *before* the measured runs.
- Opt-in `THREADANCE_PER_THREAD_PERF=1` selects the three existing TIDs
  (pacer/main and 2 workers) from `/proc/self/task`. Unexpected thread
  counts abort collection instead of silently losing data.
- `perf stat --per-thread --no-inherit -t TID1,TID2,TID3` is launched with
  disabled counters; each run is bracketed by acknowledged `enable` and
  `disable` commands. The same interval is used for all three threads.
- Perf output must contain all expected events for all three selected TIDs,
  with valid counter values and acceptable PMU running percentages.
- Original per-thread perf JSON is stored for every physical run in SQLite
  `perf_capture.raw_json`. Summed event values preserve the legacy
  `perf_metric` schema; the audit exports `per_thread_perf.csv`.
- Do not combine counter passes as one physical observation. Pacer means the
  main thread, including both delivery and coordination.
- Counting cycles in the same interval is not equivalent to measuring
  energy. Thread-level counters alone do not isolate BLS compute from
  synchronization overhead.

## Run checks before collection

```bash
git fetch origin
git switch --track origin/experiment/bls-w2-thread-perf-diagnostic
cargo fmt --all -- --check
cargo test -p threadance-harness
python3 -m unittest tests.test_bls_thread_perf_diagnostic
python3 -m experiments.studies.run_bls_thread_perf_diagnostic --check
```

**Run a one-block smoke first, not the full campaign:**

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
python3 -m experiments.studies.run_bls_thread_perf_diagnostic --smoke
```

The production runner
is strict: unsupported per-TID perf JSON or missing events aborts. Inspect
`perf_capture.raw_json` for `thread_map` and the three distinct TIDs with
complete events before attempting 20 blocks.

## Full study (only after smoke succeeds)

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
set -o pipefail
systemd-inhibit --what=sleep:idle --why="BLS per-TID study" \
  python3 -m experiments.studies.run_bls_thread_perf_diagnostic --run \
  2>&1 | tee bls-thread-perf.log
```

The resulting campaign is in
`results/studies/bls-w2-thread-perf-diagnostic/campaigns/`.
Package only the completed campaign and associated logs.

**Caution:** Exact per-run bracketing does not by itself guarantee that perf
`--per-thread` output provides correct live-TID attribution on every kernel.
Check counter units, complete thread maps, PMU timing, and compare aggregate
counts against a separately acquired process-wide control run before treating
the results as publication-quality.
