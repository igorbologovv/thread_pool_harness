use std::{num::NonZeroUsize, time::Instant};

use rayon::{ThreadPool, ThreadPoolBuildError, ThreadPoolBuilder, prelude::*};

use crate::{result::RunResult, workload::Workload};

pub struct RayonScheduler {
    pool: ThreadPool,
}

impl RayonScheduler {
    pub fn new(workers: NonZeroUsize) -> Result<Self, ThreadPoolBuildError> {
        let pool = ThreadPoolBuilder::new()
            .num_threads(workers.get())
            .build()?;

        Ok(Self { pool })
    }

    pub fn run<W: Workload>(&self, workload: &W) -> RunResult {
        let work_units = workload.work_units();

        let start = Instant::now();

        let checksum = self.pool.install(|| {
            work_units
                .par_iter()
                .map(|unit| workload.execute(unit))
                .reduce(|| 0u64, |acc, value| acc.wrapping_add(value))
        });

        let elapsed = start.elapsed();
        let completed_work_units = work_units.len() as u64;

        RunResult {
            completed_work_units,
            elapsed_ns: elapsed.as_nanos() as u64,
            work_units_per_second: completed_work_units as f64 / elapsed.as_secs_f64(),
            checksum,
        }
    }
}
