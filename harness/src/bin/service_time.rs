#[allow(dead_code)]
#[path = "../workload/mod.rs"]
mod workload;

use std::{
    fmt,
    fs::File,
    io::Write,
    path::PathBuf,
    time::{Duration, Instant},
};

use clap::Parser;

use workload::{
    CommonWorkloadConfig, Workload,
    bls_aggregate_verify::{BlsAggregateVerifyConfig, BlsAggregateVerifyWorkload},
    compute_heavy::{ComputeHeavyConfig, ComputeHeavyWorkload},
};

#[derive(Debug, Parser)]
#[command(about = "Measure pure single-thread workload service time")]
struct Cli {
    #[arg(long, default_value = "bls-aggregate-verify")]
    workload: String,

    #[arg(long)]
    operations_per_work_unit: Option<usize>,

    #[arg(long, default_value_t = 100)]
    work_units: usize,

    #[arg(long, default_value_t = 2000)]
    validators: usize,

    #[arg(long, default_value_t = 1600)]
    signers_per_certificate: usize,

    #[arg(long, default_value_t = 1)]
    certificates_per_work_unit: usize,

    #[arg(long, default_value_t = 1)]
    seed: u64,

    #[arg(long, default_value_t = 5)]
    warmup: usize,

    #[arg(long, default_value_t = 20)]
    runs: usize,

    /// Optional CSV file receiving every measured calibration run.
    #[arg(long)]
    samples_csv: Option<PathBuf>,

    /// Directory containing reusable workload datasets.
    #[arg(long, default_value = "results/datasets")]
    dataset_cache_dir: PathBuf,
}

struct ServiceTimeSummary {
    work_units: usize,
    runs: usize,
    mean_run_ns: f64,
    median_run_ns: f64,
    mean_service_ns: f64,
    median_service_ns: f64,
}

impl fmt::Display for ServiceTimeSummary {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "Pure single-thread service-time calibration")?;
        writeln!(f, "Work units              : {}", self.work_units)?;
        writeln!(f, "Measured runs           : {}", self.runs)?;
        writeln!(
            f,
            "Mean run elapsed        : {:.3} ms",
            self.mean_run_ns / 1_000_000.0
        )?;
        writeln!(
            f,
            "Median run elapsed      : {:.3} ms",
            self.median_run_ns / 1_000_000.0
        )?;
        writeln!(
            f,
            "Mean service time S     : {:.3} ms",
            self.mean_service_ns / 1_000_000.0
        )?;
        write!(
            f,
            "Median service time S   : {:.3} ms",
            self.median_service_ns / 1_000_000.0
        )
    }
}

fn run_once<W: Workload>(workload: &W) -> Duration {
    let start = Instant::now();

    for unit in workload.work_units() {
        workload.execute(unit);
    }

    start.elapsed()
}

fn main() {
    let cli = Cli::parse();

    assert!(cli.work_units > 0);
    assert!(cli.runs > 0);

    let common = CommonWorkloadConfig {
        work_units: cli.work_units,
        seed: cli.seed,
    };

    let samples_ns = match cli.workload.as_str() {
        "bls-aggregate-verify" => {
            let config = BlsAggregateVerifyConfig {
                validators: cli.validators,
                signers_per_certificate: cli.signers_per_certificate,
                certificates_per_work_unit: cli.certificates_per_work_unit,
            };
            let workload = BlsAggregateVerifyWorkload::load_or_generate_cached(
                &common,
                &config,
                &cli.dataset_cache_dir,
            );
            measure(&workload, cli.warmup, cli.runs)
        }
        "compute-heavy" => {
            let ops = cli
                .operations_per_work_unit
                .expect("--operations-per-work-unit is required for compute-heavy");
            assert!(ops > 0);
            let workload = ComputeHeavyWorkload::generate(
                &common,
                &ComputeHeavyConfig {
                    operations_per_work_unit: ops,
                },
            );
            measure(&workload, cli.warmup, cli.runs)
        }
        _ => panic!("unsupported workload: {}", cli.workload),
    };

    // Preserve measurement order in samples_ns so temporal drift can
    // be inspected later from the raw calibration data.
    let mut ordered_samples_ns = samples_ns.clone();
    ordered_samples_ns.sort_by(f64::total_cmp);

    let mean_ns = samples_ns.iter().sum::<f64>() / samples_ns.len() as f64;

    let median_ns = if ordered_samples_ns.len() % 2 == 0 {
        let upper = ordered_samples_ns.len() / 2;
        (ordered_samples_ns[upper - 1] + ordered_samples_ns[upper]) / 2.0
    } else {
        ordered_samples_ns[ordered_samples_ns.len() / 2]
    };

    let mean_service_ns = mean_ns / cli.work_units as f64;
    let median_service_ns = median_ns / cli.work_units as f64;

    if let Some(path) = &cli.samples_csv {
        let mut file = File::create(path).expect("failed to create calibration sample CSV");

        writeln!(file, "run_index,elapsed_ns,service_time_ns")
            .expect("failed to write calibration CSV header");

        for (run_index, elapsed_ns) in samples_ns.iter().enumerate() {
            let service_time_ns = elapsed_ns / cli.work_units as f64;

            writeln!(file, "{run_index},{elapsed_ns:.0},{service_time_ns:.6}")
                .expect("failed to write calibration sample");
        }
    }

    let summary = ServiceTimeSummary {
        work_units: cli.work_units,
        runs: cli.runs,
        mean_run_ns: mean_ns,
        median_run_ns: median_ns,
        mean_service_ns,
        median_service_ns,
    };

    println!("{summary}");
}

fn measure<W: Workload>(workload: &W, warmup: usize, runs: usize) -> Vec<f64> {
    for _ in 0..warmup {
        run_once(workload);
    }
    (0..runs)
        .map(|_| run_once(workload).as_nanos() as f64)
        .collect()
}
