#[path = "../workload/mod.rs"]
mod workload;

use std::time::{Duration, Instant};

use clap::Parser;

use workload::{
    CommonWorkloadConfig, Workload,
    bls_aggregate_verify::{BlsAggregateVerifyConfig, BlsAggregateVerifyWorkload},
};

#[derive(Debug, Parser)]
#[command(about = "Measure pure single-thread workload service time")]
struct Cli {
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

    let config = BlsAggregateVerifyConfig {
        validators: cli.validators,
        signers_per_certificate: cli.signers_per_certificate,
        certificates_per_work_unit: cli.certificates_per_work_unit,
    };

    // Dataset generation is intentionally outside the measured interval.
    let workload = BlsAggregateVerifyWorkload::generate(&common, &config);

    for _ in 0..cli.warmup {
        run_once(&workload);
    }

    let mut samples_ns = Vec::with_capacity(cli.runs);

    for _ in 0..cli.runs {
        let elapsed = run_once(&workload);
        samples_ns.push(elapsed.as_nanos() as f64);
    }

    samples_ns.sort_by(f64::total_cmp);

    let mean_ns = samples_ns.iter().sum::<f64>() / samples_ns.len() as f64;

    let median_ns = if samples_ns.len() % 2 == 0 {
        let upper = samples_ns.len() / 2;
        (samples_ns[upper - 1] + samples_ns[upper]) / 2.0
    } else {
        samples_ns[samples_ns.len() / 2]
    };

    let mean_service_ns = mean_ns / cli.work_units as f64;
    let median_service_ns = median_ns / cli.work_units as f64;

    println!("Pure single-thread service-time calibration");
    println!("Work units              : {}", cli.work_units);
    println!("Measured runs           : {}", cli.runs);
    println!("Mean run elapsed        : {:.3} ms", mean_ns / 1_000_000.0);
    println!(
        "Median run elapsed      : {:.3} ms",
        median_ns / 1_000_000.0
    );
    println!(
        "Mean service time S     : {:.3} ms",
        mean_service_ns / 1_000_000.0
    );
    println!(
        "Median service time S   : {:.3} ms",
        median_service_ns / 1_000_000.0
    );
}
