mod cli;
mod perf_control;
mod result;
mod runner;
mod schedulers;
mod summary;
mod workload;

use clap::Parser;

use cli::{Cli, WorkloadKind};
use perf_control::PerfControl;
use runner::run_repeated;
use schedulers::rayon::RayonScheduler;
use summary::RunSummary;
use workload::{
    compute_heavy::{ComputeHeavyConfig, ComputeHeavyWorkload},
    CommonWorkloadConfig, Workload,
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
                rounds: cli.rounds.get(),
            };

            let workload =
                ComputeHeavyWorkload::generate(&common_config, &workload_config);

            let scheduler =
                RayonScheduler::new(cli.workers).expect("failed to create Rayon thread pool");

            let results = run_repeated(
                cli.warmup,
                cli.runs,
                perf.as_mut(),
                || scheduler.run(&workload),
            );

            let summary = RunSummary::from_results(&results);

            println!("{summary}");
        }
    }
}