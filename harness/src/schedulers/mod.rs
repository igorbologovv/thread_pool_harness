//! Scheduler implementations used by the benchmark harness.
//!
//! Schedulers define how workload items are distributed and executed across
//! worker threads. Scheduler implementations must remain independent from
//! workload-specific processing logic.

pub mod rayon;

use crate::{result::RunResult, workload::Workload};

/// Common interface implemented by every scheduler used by the harness.
///
/// A scheduler is responsible for distributing workload units across its
/// worker threads and returning the result of one complete benchmark run.
pub trait Scheduler {
    fn run<W: Workload>(&self, workload: &W) -> RunResult;
}
