mod cli;
mod perf_control;
mod result;
mod runner;
mod schedulers;
mod summary;
mod workload;

use std::{num::NonZeroUsize, sync::Arc};

use clap::Parser;

use cli::{Cli, SchedulerKind, WorkloadKind};
use perf_control::PerfControl;
use runner::run_repeated;
use schedulers::{Scheduler, rayon::RayonScheduler, threadance::ThreadanceScheduler};
use summary::RunSummary;
use workload::{
    CommonWorkloadConfig, Workload,
    compute_heavy::{ComputeHeavyConfig, ComputeHeavyWorkload},
};

fn main() {
    let cli = Cli::parse();

    let common_config = CommonWorkloadConfig {
        work_units: cli.work_units.get(),
        seed: cli.seed,
    };

    let mut perf = match (&cli.perf_control, &cli.perf_ack) {
        (Some(control_path), Some(ack_path)) => Some(
            PerfControl::connect(control_path, ack_path)
                .expect("failed to connect to perf control FIFOs"),
        ),
        (None, None) => None,
        _ => panic!("--perf-control and --perf-ack must be provided together"),
    };

    match cli.workload {
        WorkloadKind::ComputeHeavy => {
            let workload_config = ComputeHeavyConfig {
                operations_per_work_unit: cli.operations_per_work_unit.get(),
            };

            let workload = Arc::new(ComputeHeavyWorkload::generate(
                &common_config,
                &workload_config,
            ));

            match cli.scheduler {
                SchedulerKind::Rayon => {
                    let scheduler = RayonScheduler::new(cli.workers)
                        .expect("failed to create Rayon thread pool");

                    run_benchmark(&scheduler, &workload, cli.warmup, cli.runs, perf.as_mut());
                }

                SchedulerKind::Threadance => {
                    let scheduler = ThreadanceScheduler::new(cli.workers, cli.queue_capacity)
                        .expect("failed to create Threadance thread pool");

                    run_benchmark(&scheduler, &workload, cli.warmup, cli.runs, perf.as_mut());
                }
            }
        }
    }
}

fn run_benchmark<S, W>(
    scheduler: &S,
    workload: &Arc<W>,
    warmup: usize,
    runs: NonZeroUsize,
    perf: Option<&mut PerfControl>,
) where
    S: Scheduler,
    W: Workload,
{
    let results = run_repeated(warmup, runs, perf, || scheduler.run(workload));

    let summary = RunSummary::from_results(&results);

    println!("{summary}");
}
