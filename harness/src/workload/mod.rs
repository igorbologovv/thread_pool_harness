//! Workload definitions used by the benchmark harness.
//!
//! A workload owns its input dataset and defines the processing performed on
//! each independently processable unit of work.
//!
//! Workload generation happens outside the measured execution interval.
//! Scheduling and thread management are deliberately kept outside this module.

pub mod bls_aggregate_verify;
pub mod compute_heavy;

/// Configuration shared by all workload implementations.
#[derive(Debug, Clone)]
pub struct CommonWorkloadConfig {
    /// Number of independently processable work units exposed by the workload.
    pub work_units: usize,

    /// Seed used for deterministic dataset generation.
    pub seed: u64,
}

/// Common interface implemented by every benchmark workload.
pub trait Workload: Send + Sync + 'static {
    /// One independently processable workload unit.
    ///
    /// Work units must be independently transferable to persistent worker
    /// threads.
    type WorkUnit: Clone + Send + Sync + 'static;

    /// Configuration specific to this workload.
    type Config;

    /// Generates the complete workload dataset.
    ///
    /// Dataset generation happens before the measured execution interval.
    fn generate(common: &CommonWorkloadConfig, config: &Self::Config) -> Self
    where
        Self: Sized;

    /// Returns all independently processable work units.
    fn work_units(&self) -> &[Self::WorkUnit];

    /// Executes one work unit.
    fn execute(&self, unit: &Self::WorkUnit);
}
