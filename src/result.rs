//! Results produced by a single benchmark run.
//!
//! This structure contains only metrics measured by the benchmark harness
//! itself. Hardware and operating-system counters are collected externally
//! with `perf`.

#[derive(Debug, Clone)]
pub struct RunResult {
    /// Number of work items processed during the run.
    pub completed_items: u64,

    /// Wall-clock time spent executing the workload, in nanoseconds.
    pub elapsed_ns: u64,

    /// Number of completed work items per second.
    pub items_per_second: f64,

    /// Deterministic result produced by the workload.
    ///
    /// Used to verify that every scheduler executed the same work and that
    /// the compiler did not eliminate the computation.
    pub checksum: u64,
}
