use std::fmt;

/// One observed paced work-unit submission.
///
/// `actual_submit_offset_ns` is sampled immediately before invoking the
/// scheduler's submit path. It therefore measures pacer wake-up lateness
/// without including scheduler submission work itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ArrivalSample {
    /// Zero-based work-unit position in the delivery schedule.
    pub work_unit_index: u64,

    /// Planned offset from the run start.
    pub scheduled_offset_ns: u64,

    /// Observed offset from the run start immediately before submission.
    pub actual_submit_offset_ns: u64,

    /// Difference between actual and scheduled submission offsets.
    pub lateness_ns: u64,
}

/// Results produced by a single benchmark run.
#[derive(Debug)]
pub struct RunResult {
    /// Wall-clock start of the measured run, in nanoseconds
    /// since the Unix epoch.
    pub started_unix_ns: u64,

    /// Number of work units completed during the run.
    pub completed_work_units: u64,

    /// Wall-clock time spent executing the workload, in nanoseconds.
    pub elapsed_ns: u64,

    /// Number of completed work units per second.
    pub work_units_per_second: f64,

    /// Per-work-unit timing observations for paced delivery modes.
    ///
    /// Empty for all-at-once delivery.
    pub arrival_samples: Vec<ArrivalSample>,
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
