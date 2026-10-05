use std::{
    fmt,
    num::{NonZeroU64, NonZeroUsize},
    path::PathBuf,
};

use clap::{ArgGroup, Parser, ValueEnum};

use crate::delivery::DeliveryMode;

#[derive(Debug, Parser)]
#[command(about = "Thread-pool scheduling benchmark harness")]
#[command(group(
    ArgGroup::new("delivery")
        .required(true)
        .multiple(false)
        .args([
            "all_at_once",
            "steady_arrivals",
            "variable_arrivals",
            "bursty_arrivals",
        ])
))]
pub struct Cli {
    /// Workload to execute.
    #[arg(long, value_enum)]
    pub workload: WorkloadKind,

    /// Scheduler implementation to benchmark.
    #[arg(long, value_enum, default_value = "rayon")]
    pub scheduler: SchedulerKind,

    /// Number of worker threads.
    #[arg(long)]
    pub workers: NonZeroUsize,

    /// Capacity of the Threadance task queue.
    #[arg(long, default_value = "1024")]
    pub queue_capacity: NonZeroUsize,

    /// Submit every work unit immediately at the start of the run.
    #[arg(long)]
    pub all_at_once: bool,

    /// Deliver work units using low-variability pseudo-random inter-arrival gaps.
    #[arg(long)]
    pub steady_arrivals: bool,

    /// Deliver work units using moderately variable pseudo-random inter-arrival gaps.
    #[arg(long)]
    pub variable_arrivals: bool,

    /// Deliver work units using highly variable pseudo-random inter-arrival gaps.
    #[arg(long)]
    pub bursty_arrivals: bool,

    /// Time between the first and final scheduled work-unit arrival.
    ///
    /// Ignored by --all-at-once.
    #[arg(long, default_value = "100")]
    pub arrival_window_ms: NonZeroU64,

    /// Seed used to generate the deterministic arrival schedule.
    ///
    /// This is separate from --seed, which controls workload generation.
    #[arg(long, default_value_t = 1)]
    pub arrival_seed: u64,

    /// Number of independently schedulable work units.
    #[arg(long)]
    pub work_units: NonZeroUsize,

    /// Number of independent matrix operations performed by each compute-heavy
    /// work unit.
    #[arg(long)]
    pub operations_per_work_unit: Option<NonZeroUsize>,

    /// Total BLS validator-set size.
    #[arg(long, default_value = "2000")]
    pub validators: NonZeroUsize,

    /// Number of BLS signers included in each aggregate signature.
    #[arg(long, default_value = "1600")]
    pub signers_per_certificate: NonZeroUsize,

    /// Number of BLS certificate verifications grouped into one work unit.
    #[arg(long, default_value = "1")]
    pub certificates_per_work_unit: NonZeroUsize,

    /// Seed used for deterministic workload generation.
    #[arg(long, default_value_t = 1)]
    pub seed: u64,

    /// Number of warm-up runs discarded before measurement.
    #[arg(long, default_value_t = 5)]
    pub warmup: usize,

    /// Number of measured benchmark runs.
    #[arg(long, default_value = "50")]
    pub runs: NonZeroUsize,

    /// FIFO used to send measurement-control commands to perf.
    #[arg(long)]
    pub perf_control: Option<PathBuf>,

    /// FIFO used to receive acknowledgements from perf.
    #[arg(long)]
    pub perf_ack: Option<PathBuf>,
}

impl Cli {
    pub fn delivery_mode(&self) -> DeliveryMode {
        match (
            self.all_at_once,
            self.steady_arrivals,
            self.variable_arrivals,
            self.bursty_arrivals,
        ) {
            (true, false, false, false) => DeliveryMode::AllAtOnce,
            (false, true, false, false) => DeliveryMode::SteadyArrivals,
            (false, false, true, false) => DeliveryMode::VariableArrivals,
            (false, false, false, true) => DeliveryMode::BurstyArrivals,
            _ => unreachable!("clap validates the delivery-mode argument group"),
        }
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum WorkloadKind {
    ComputeHeavy,
    BlsAggregateVerify,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum SchedulerKind {
    Rayon,
    Bevy,
    Threadance,
}

impl fmt::Display for WorkloadKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let value = self
            .to_possible_value()
            .expect("workload variant must have a CLI representation");

        f.write_str(value.get_name())
    }
}
