use std::num::NonZeroUsize;

use crate::{perf_control::PerfControl, result::RunResult};

/// Executes warm-up runs followed by measured benchmark runs.
///
/// Warm-up results are discarded. Every measured run is retained so that
/// summary statistics can be calculated later.
pub fn run_repeated<F>(
    warmup_runs: usize,
    measured_runs: NonZeroUsize,
    mut perf: Option<&mut PerfControl>,
    mut run_once: F,
) -> Vec<RunResult>
where
    F: FnMut() -> RunResult,
{
    for _ in 0..warmup_runs {
        run_once();
    }

    if let Some(control) = perf.as_deref_mut() {
        control.enable().expect("failed to enable perf counters");
    }

    let mut results = Vec::with_capacity(measured_runs.get());

    let first = run_once();
    let expected_checksum = first.checksum;

    results.push(first);

    for _ in 1..measured_runs.get() {
        let result = run_once();

        assert_eq!(
            result.checksum, expected_checksum,
            "checksum mismatch between benchmark runs"
        );

        results.push(result);
    }

    if let Some(control) = perf.as_deref_mut() {
        control.disable().expect("failed to disable perf counters");
    }

    results
}
