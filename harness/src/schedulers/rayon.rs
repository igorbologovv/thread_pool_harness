use std::{num::NonZeroUsize, sync::Arc, time::Instant};

use rayon::{ThreadPool, ThreadPoolBuildError, ThreadPoolBuilder, prelude::*};

use crate::{result::RunResult, schedulers::Scheduler, workload::Workload};

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
}

impl Scheduler for RayonScheduler {
    fn run<W: Workload>(&self, workload: &Arc<W>) -> RunResult {
        let work_units = workload.work_units();
        let completed_work_units = work_units.len() as u64;

        let start = Instant::now();

        self.pool.install(|| {
            work_units
                .par_iter()
                .for_each(|unit| workload.execute(unit));
        });

        let elapsed = start.elapsed();

        RunResult {
            completed_work_units,
            elapsed_ns: elapsed.as_nanos() as u64,
            work_units_per_second: completed_work_units as f64 / elapsed.as_secs_f64(),
        }
    }
}
