use std::fmt;

use clap::{Parser, ValueEnum};

#[derive(Debug, Parser)]
pub struct Cli {
    /// Workload to execute.
    #[arg(long, value_enum)]
    pub workload: WorkloadKind,
}

/// Workloads available through the benchmark CLI.
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
