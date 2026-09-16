use std::{fmt, num::NonZeroUsize, path::PathBuf};

use clap::{Parser, ValueEnum};

#[derive(Debug, Parser)]
#[command(about = "Thread-pool scheduling benchmark harness")]
pub struct Cli {
    /// Workload to execute.
    #[arg(long, value_enum)]
    pub workload: WorkloadKind,

    /// Number of worker threads.
    #[arg(long)]
    pub workers: NonZeroUsize,

    /// Number of independently schedulable work units.
    #[arg(long)]
    pub work_units: NonZeroUsize,

    /// Number of independent matrix operations performed by each work unit.
    #[arg(long)]
    pub operations_per_work_unit: NonZeroUsize,

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
}

impl fmt::Display for WorkloadKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let value = self
            .to_possible_value()
            .expect("workload variant must have a CLI representation");

        f.write_str(value.get_name())
    }
}
