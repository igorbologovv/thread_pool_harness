use std::{fmt, num::NonZeroUsize};

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

    /// Number of compute rounds performed by each work unit.
    #[arg(long)]
    pub rounds: NonZeroUsize,

    /// Seed used for deterministic workload generation.
    #[arg(long, default_value_t = 1)]
    pub seed: u64,

    /// Number of warm-up runs discarded before measurement.
    #[arg(long, default_value_t = 5)]
    pub warmup: usize,

    /// Number of measured benchmark runs.
    #[arg(long, default_value = "50")]
    pub runs: NonZeroUsize,
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
