use std::num::NonZeroUsize;

use crate::{
    cli::ProfileMode,
    perf_control::{
        CustomPerfPass, DEEP_PASSES, PerfCapture, STANDARD_PASS, measure_run, measure_run_events,
    },
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
    custom_passes: Option<&[CustomPerfPass]>,
    reverse_passes: bool,
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
                let (result, capture) = measure_run(STANDARD_PASS, &mut run_once)
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
            // Legacy deep passes are unchanged unless custom passes are supplied.
            let mut passes: Vec<(String, Vec<String>)> = if let Some(custom) = custom_passes {
                custom
                    .iter()
                    .map(|pass| (pass.name.clone(), pass.events.clone()))
                    .collect()
            } else {
                DEEP_PASSES
                    .iter()
                    .map(|pass| {
                        (
                            pass.name.to_owned(),
                            pass.events
                                .iter()
                                .map(|event| (*event).to_owned())
                                .collect(),
                        )
                    })
                    .collect()
            };
            if reverse_passes {
                passes.reverse();
            }
            let total = measured_runs
                .get()
                .checked_mul(passes.len())
                .expect("deep run count overflow");

            let mut results = Vec::with_capacity(total);

            let mut perf_captures = Vec::with_capacity(total);

            // Custom multi-pass studies use the existing warm-up once per
            // configuration, not once per PMU event group.
            if custom_passes.is_some() {
                run_warmups(warmup_runs, &mut run_once);
            }

            for (pass_name, events) in &passes {
                // Preserve legacy deep profiling's original per-pass warm-up.
                if custom_passes.is_none() {
                    run_warmups(warmup_runs, &mut run_once);
                }

                for _ in 0..measured_runs.get() {
                    let (result, capture) = measure_run_events(pass_name, events, &mut run_once)
                        .unwrap_or_else(|error| panic!("failed deep pass {pass_name}: {error}"));
                    if custom_passes.is_some() {
                        validate_custom_capture(pass_name, events, &capture);
                    }

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

fn validate_custom_capture(pass_name: &str, events: &[String], capture: &PerfCapture) {
    assert_eq!(capture.perf_exit_code, 0, "perf failed in {pass_name}");
    for expected in events {
        let base = expected.split(':').next().unwrap_or(expected);
        let metric = capture
            .metrics
            .iter()
            .find(|metric| metric.event_name.split(':').next() == Some(base))
            .unwrap_or_else(|| panic!("missing perf event {expected} in {pass_name}"));
        assert!(
            metric.status == "ok" && metric.counter_value.is_some(),
            "unavailable perf event {expected} in {pass_name}: {}",
            metric.status,
        );
        // perf JSON encodes pcnt-running as percent, not a fraction.
        if let Some(running) = metric.percent_running {
            assert!(
                (99.5..=100.01).contains(&running),
                "multiplexed perf event {expected} in {pass_name}: {running:.2}%",
            );
        }
    }
}
