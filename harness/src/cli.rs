use std::{fmt, num::NonZeroUsize, path::PathBuf};

use clap::{Parser, ValueEnum};

#[derive(Debug, Parser)]
#[command(about = "Thread-pool scheduling benchmark harness")]
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

    /// Number of independently schedulable work units.
    ///
    /// For the BLS workload, one work unit is one aggregate certificate-like
    /// verification input.
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
