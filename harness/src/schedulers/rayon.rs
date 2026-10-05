use std::{num::NonZeroUsize, sync::Arc};

use rayon::{ThreadPool, ThreadPoolBuildError, ThreadPoolBuilder};

use crate::{
    delivery::{DeliverySchedule, run_with_delivery},
    result::RunResult,
    schedulers::Scheduler,
    workload::Workload,
};

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
    fn run<W: Workload>(&self, workload: &Arc<W>, delivery: &DeliverySchedule) -> RunResult {
        run_with_delivery(workload, delivery, |workload, unit, completion| {
            self.pool.spawn(move || {
                workload.execute(&unit);
                completion.complete_one();
            });
        })
    }
}
