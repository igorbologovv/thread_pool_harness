mod cli;
mod result;
mod runner;
mod schedulers;
mod summary;
mod workload;

use clap::Parser;

use cli::{Cli, WorkloadKind};
use runner::run_repeated;
use schedulers::rayon::RayonScheduler;
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

    match cli.workload {
        WorkloadKind::ComputeHeavy => {
            let workload_config = ComputeHeavyConfig {
                rounds: cli.rounds.get(),
            };

            let workload = ComputeHeavyWorkload::generate(&common_config, &workload_config);

            let scheduler =
                RayonScheduler::new(cli.workers).expect("failed to create Rayon thread pool");

            let results = run_repeated(cli.warmup, cli.runs, || scheduler.run(&workload));

            let summary = RunSummary::from_results(&results);

            println!("{summary}");
        }
    }
}
