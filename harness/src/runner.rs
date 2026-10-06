use std::num::NonZeroUsize;

use crate::{
    cli::ProfileMode,
    perf_control::{DEEP_PASSES, PerfCapture, STANDARD_PASS, measure_run},
    result::RunResult,
};

pub struct RepeatedRunOutput {
    /// One entry per physical measured execution.
    pub results: Vec<RunResult>,

    /// One-to-one correspondence with `results`.
    pub perf_captures: Vec<Option<PerfCapture>>,
}

pub fn run_repeated<F>(
    warmup_runs: usize,
    measured_runs: NonZeroUsize,
    profile: ProfileMode,
    mut run_once: F,
) -> RepeatedRunOutput
where
    F: FnMut() -> RunResult,
{
    match profile {
        ProfileMode::None => {
            run_warmups(warmup_runs, &mut run_once);

            let mut results = Vec::with_capacity(measured_runs.get());

            let mut perf_captures = Vec::with_capacity(measured_runs.get());

            for _ in 0..measured_runs.get() {
                results.push(run_once());
                perf_captures.push(None);
            }

            RepeatedRunOutput {
                results,
                perf_captures,
            }
        }

        ProfileMode::Standard => {
            run_warmups(warmup_runs, &mut run_once);

            let mut results = Vec::with_capacity(measured_runs.get());

            let mut perf_captures = Vec::with_capacity(measured_runs.get());

            for _ in 0..measured_runs.get() {
                let (result, capture) = measure_run(STANDARD_PASS, || run_once())
                    .expect("failed to profile standard run");

                results.push(result);
                perf_captures.push(Some(capture));
            }

            RepeatedRunOutput {
                results,
                perf_captures,
            }
        }

        ProfileMode::Deep => {
            let total = measured_runs
                .get()
                .checked_mul(DEEP_PASSES.len())
                .expect("deep run count overflow");

            let mut results = Vec::with_capacity(total);

            let mut perf_captures = Vec::with_capacity(total);

            for pass in DEEP_PASSES {
                // Each group gets equivalent warm-up.
                run_warmups(warmup_runs, &mut run_once);

                for _ in 0..measured_runs.get() {
                    let (result, capture) = measure_run(*pass, || run_once())
                        .unwrap_or_else(|error| panic!("failed deep pass {}: {error}", pass.name,));

                    results.push(result);
                    perf_captures.push(Some(capture));
                }
            }

            RepeatedRunOutput {
                results,
                perf_captures,
            }
        }
    }
}

fn run_warmups<F>(warmup_runs: usize, run_once: &mut F)
where
    F: FnMut() -> RunResult,
{
    for _ in 0..warmup_runs {
        run_once();
    }
}
