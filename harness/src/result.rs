use std::fmt;

/// Results produced by a single benchmark run.
#[derive(Debug)]
pub struct RunResult {
    /// Number of work units completed during the run.
    pub completed_work_units: u64,

    /// Wall-clock time spent executing the workload, in nanoseconds.
    pub elapsed_ns: u64,

    /// Number of completed work units per second.
    pub work_units_per_second: f64,
}

impl fmt::Display for RunResult {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "Completed work units : {}", self.completed_work_units)?;
        writeln!(
            f,
            "Elapsed              : {:.3} ms",
            self.elapsed_ns as f64 / 1_000_000.0
        )?;
        write!(
            f,
            "Throughput           : {:.2} work units/s",
            self.work_units_per_second
        )
    }
}
