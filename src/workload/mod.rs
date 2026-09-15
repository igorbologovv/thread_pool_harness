//! Workload definitions used by the benchmark harness.
//!
//! A workload owns its input dataset and defines the processing performed on
//! each independently schedulable unit of work.
//!
//! Workloads are independent from the scheduler used to execute them.

pub mod compute_heavy;

/// Configuration shared by all workload implementations.
#[derive(Debug, Clone)]
pub struct CommonWorkloadConfig {
    /// Number of independently schedulable work units.
    pub work_units: usize,

    /// Seed used for deterministic dataset generation.
    pub seed: u64,
}

/// Common interface implemented by every benchmark workload.
///
/// A workload is responsible for:
/// - generating its input dataset;
/// - exposing independently schedulable work units;
/// - defining the processing performed on one work unit.
///
/// Scheduling and thread management are deliberately kept outside this trait.
pub trait Workload: Sync {
    /// One independently schedulable unit of work.
    type WorkUnit: Sync;

    /// Configuration specific to this workload.
    type Config;

    /// Generates the complete workload dataset.
    ///
    /// `common` contains parameters shared by all workloads, while `config`
    /// contains parameters specific to this workload.
    ///
    /// Dataset generation happens before the measured execution interval.
    fn generate(common: &CommonWorkloadConfig, config: &Self::Config) -> Self
    where
        Self: Sized;

    /// Returns all independently schedulable work units.
    fn work_units(&self) -> &[Self::WorkUnit];

    /// Executes one work unit and returns a deterministic control value.
    ///
    /// The scheduler aggregates these values into the checksum for the
    /// complete benchmark run.
    fn execute(&self, unit: &Self::WorkUnit) -> u64;
}
