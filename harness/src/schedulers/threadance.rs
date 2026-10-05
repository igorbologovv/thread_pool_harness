use std::{io, num::NonZeroUsize, sync::Arc};

use ::threadance::ThreadPool;

use crate::{
    delivery::{DeliverySchedule, run_with_delivery},
    result::RunResult,
    schedulers::Scheduler,
    workload::Workload,
};

pub struct ThreadanceScheduler {
    pool: ThreadPool,
}

impl ThreadanceScheduler {
    pub fn new(workers: NonZeroUsize, queue_capacity: NonZeroUsize) -> io::Result<Self> {
        let pool = ThreadPool::new(workers, queue_capacity)?;

        Ok(Self { pool })
    }
}

impl Scheduler for ThreadanceScheduler {
    fn run<W: Workload>(&self, workload: &Arc<W>, delivery: &DeliverySchedule) -> RunResult {
        run_with_delivery(workload, delivery, |workload, unit, completion| {
            self.pool
                .execute(move || {
                    workload.execute(&unit);
                    completion.complete_one();
                })
                .expect("Threadance worker threads must be alive");
        })
    }
}
