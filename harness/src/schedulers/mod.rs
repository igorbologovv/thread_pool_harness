//! Scheduler implementations used by the benchmark harness.
//!
//! Schedulers define how workload items are distributed and executed across
//! worker threads. Scheduler implementations must remain independent from
//! workload-specific processing logic.

pub mod rayon;
pub mod threadance;

use std::sync::Arc;

use crate::{result::RunResult, workload::Workload};

/// Common interface implemented by every scheduler used by the harness.
pub trait Scheduler {
    fn run<W: Workload>(&self, workload: &Arc<W>) -> RunResult;
}
