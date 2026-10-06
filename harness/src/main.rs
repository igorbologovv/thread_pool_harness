mod cli;
mod delivery;
mod perf_control;
mod result;
mod runner;
mod schedulers;
mod storage;
mod summary;
mod workload;

use std::{
    num::NonZeroUsize,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use clap::Parser;

use cli::{Cli, SchedulerKind, WorkloadKind};
use delivery::DeliverySchedule;
use perf_control::PerfControl;
use result::RunResult;
use runner::run_repeated;
use schedulers::{
    Scheduler, bevy::BevyScheduler, rayon::RayonScheduler, threadance::ThreadanceScheduler,
};
use storage::sqlite::BenchmarkDb;
use summary::RunSummary;
use workload::{
    CommonWorkloadConfig, Workload,
    bls_aggregate_verify::{BlsAggregateVerifyConfig, BlsAggregateVerifyWorkload},
    compute_heavy::{ComputeHeavyConfig, ComputeHeavyWorkload},
};

fn main() {
    let cli = Cli::parse();

    let benchmark_started_unix_seconds: i64 = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before Unix epoch")
        .as_secs()
        .try_into()
        .expect("benchmark timestamp does not fit into i64");

    let common_config = CommonWorkloadConfig {
        work_units: cli.work_units.get(),
        seed: cli.seed,
    };

    let delivery = DeliverySchedule::generate(
        cli.delivery_mode(),
        cli.work_units.get(),
        Duration::from_millis(cli.arrival_window_ms.get()),
        cli.arrival_seed,
    );

    println!("{delivery}\n");

    let mut perf = match (&cli.perf_control, &cli.perf_ack) {
        (Some(control_path), Some(ack_path)) => Some(
            PerfControl::connect(control_path, ack_path)
                .expect("failed to connect to perf control FIFOs"),
        ),
        (None, None) => None,
        _ => panic!("--perf-control and --perf-ack must be provided together"),
    };

    let results = match cli.workload {
        WorkloadKind::ComputeHeavy => {
            let operations_per_work_unit = cli
                .operations_per_work_unit
                .expect("--operations-per-work-unit is required for compute-heavy");

            let workload_config = ComputeHeavyConfig {
                operations_per_work_unit: operations_per_work_unit.get(),
            };

            let workload = Arc::new(ComputeHeavyWorkload::generate(
                &common_config,
                &workload_config,
            ));

            run_selected_scheduler(&cli, &workload, &delivery, perf.as_mut())
        }

        WorkloadKind::BlsAggregateVerify => {
            let workload_config = BlsAggregateVerifyConfig {
                validators: cli.validators.get(),
                signers_per_certificate: cli.signers_per_certificate.get(),
                certificates_per_work_unit: cli.certificates_per_work_unit.get(),
            };

            let workload = Arc::new(BlsAggregateVerifyWorkload::generate(
                &common_config,
                &workload_config,
            ));

            run_selected_scheduler(&cli, &workload, &delivery, perf.as_mut())
        }
    };

    // Storage begins only after all measured benchmark runs have completed.
    let mut database = BenchmarkDb::open(&cli.database, benchmark_started_unix_seconds)
        .expect("failed to open benchmark database");

    let stored = database
        .store_experiment(&cli, &results)
        .expect("failed to store benchmark results");

    println!(
        "\nStored benchmark: session={} experiment={} database={}",
        stored.session_id,
        stored.experiment_id,
        cli.database.display(),
    );
}

fn run_selected_scheduler<W>(
    cli: &Cli,
    workload: &Arc<W>,
    delivery: &DeliverySchedule,
    perf: Option<&mut PerfControl>,
) -> Vec<RunResult>
where
    W: Workload,
{
    match cli.scheduler {
        SchedulerKind::Rayon => {
            let scheduler =
                RayonScheduler::new(cli.workers).expect("failed to create Rayon thread pool");

            run_benchmark(&scheduler, workload, delivery, cli.warmup, cli.runs, perf)
        }

        SchedulerKind::Bevy => {
            let scheduler = BevyScheduler::new(cli.workers);

            run_benchmark(&scheduler, workload, delivery, cli.warmup, cli.runs, perf)
        }

        SchedulerKind::Threadance => {
            let scheduler = ThreadanceScheduler::new(cli.workers, cli.queue_capacity)
                .expect("failed to create Threadance thread pool");

            run_benchmark(&scheduler, workload, delivery, cli.warmup, cli.runs, perf)
        }
    }
}

fn run_benchmark<S, W>(
    scheduler: &S,
    workload: &Arc<W>,
    delivery: &DeliverySchedule,
    warmup: usize,
    runs: NonZeroUsize,
    perf: Option<&mut PerfControl>,
) -> Vec<RunResult>
where
    S: Scheduler,
    W: Workload,
{
    let results = run_repeated(warmup, runs, perf, || scheduler.run(workload, delivery));

    let summary = RunSummary::from_results(&results);

    println!("{summary}");

    results
}
