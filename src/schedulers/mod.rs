//! Scheduler implementations used by the benchmark harness.
//!
//! Schedulers define how workload items are distributed and executed across
//! worker threads. Scheduler implementations must remain independent from
//! workload-specific processing logic.

pub mod rayon;
