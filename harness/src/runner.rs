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

    let mut results = Vec::with_capacity(measured_runs.get());

    for _ in 0..measured_runs.get() {
        if let Some(control) = perf.as_mut() {
            control.enable().expect("failed to enable perf counters");
        }

        let result = run_once();

        if let Some(control) = perf.as_mut() {
            control.disable().expect("failed to disable perf counters");
        }

        results.push(result);
    }

    results
}
