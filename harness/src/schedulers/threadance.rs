use std::{io, num::NonZeroUsize, sync::Arc, time::Duration};

use ::threadance::{AgaveBackoff, CrossbeamMpmc, CrossbeamWait, ExecuteError, SpinThenPark, ThreadPool};

use crate::{
    delivery::{DeliverySchedule, run_with_delivery},
    result::RunResult,
    schedulers::Scheduler,
    workload::Workload,
};

type SpinPool = ThreadPool<CrossbeamMpmc, SpinThenPark>;
type AgavePool = ThreadPool<CrossbeamMpmc, AgaveBackoff>;
type NativePool = ThreadPool<CrossbeamMpmc, CrossbeamWait>;

enum ThreadancePool {
    Spin(SpinPool),
    Agave(AgavePool),
    Native(NativePool),
}

impl ThreadancePool {
    fn execute<F>(&self, task: F) -> Result<(), ExecuteError>
    where
        F: FnOnce() + Send + 'static,
    {
        match self {
            Self::Spin(pool) => pool.execute(task),
            Self::Agave(pool) => pool.execute(task),
            Self::Native(pool) => pool.execute(task),
        }
    }
}

pub struct ThreadanceScheduler {
    pool: ThreadancePool,
}

impl ThreadanceScheduler {
    pub fn new(
        workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
        spin_us: u64,
    ) -> io::Result<Self> {
        let pool = ThreadPool::builder(workers, queue_capacity)
            .wake_policy(SpinThenPark::new(Duration::from_micros(spin_us)))
            .build()?;

        Ok(Self {
            pool: ThreadancePool::Spin(pool),
        })
    }

    pub fn new_crossbeam_wait(
        workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
    ) -> io::Result<Self> {
        let pool = ThreadPool::builder(workers, queue_capacity)
            .wake_policy(CrossbeamWait::default())
            .build()?;

        Ok(Self {
            pool: ThreadancePool::Native(pool),
        })
    }

    pub fn new_agave_backoff(
        workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
    ) -> io::Result<Self> {
        let pool = ThreadPool::builder(workers, queue_capacity)
            .wake_policy(AgaveBackoff::default())
            .build()?;

        Ok(Self {
            pool: ThreadancePool::Agave(pool),
        })
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
