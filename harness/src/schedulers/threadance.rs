use std::{io, num::NonZeroUsize, sync::Arc, time::Duration};

use ::threadance::{ThreadPool, WaitStrategy};

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
    pub fn new(
        workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
        spin_us: u64,
    ) -> io::Result<Self> {
        let wait_strategy = WaitStrategy::SpinThenPark(Duration::from_micros(spin_us));
        let pool = ThreadPool::with_wait_strategy(workers, queue_capacity, wait_strategy)?;

        Ok(Self { pool })
    }

    pub fn new_agave(
        workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
    ) -> io::Result<Self> {
        let pool =
            ThreadPool::with_wait_strategy(workers, queue_capacity, WaitStrategy::AgaveBackoff)?;

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
