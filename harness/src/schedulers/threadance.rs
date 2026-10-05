use std::{
    io,
    num::NonZeroUsize,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    thread::{self, Thread},
    time::Instant,
};

use ::threadance::ThreadPool;

use crate::{result::RunResult, schedulers::Scheduler, workload::Workload};

struct Completion {
    remaining: AtomicUsize,
    waiter: Thread,
}

impl Completion {
    fn new(count: usize) -> Self {
        debug_assert!(count > 0);

        Self {
            remaining: AtomicUsize::new(count),
            waiter: thread::current(),
        }
    }

    fn complete_one(&self) {
        if self.remaining.fetch_sub(1, Ordering::AcqRel) == 1 {
            self.waiter.unpark();
        }
    }

    fn wait(&self) {
        while self.remaining.load(Ordering::Acquire) != 0 {
            thread::park();
        }
    }
}

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
    fn run<W: Workload>(&self, workload: &Arc<W>) -> RunResult {
        let work_units = workload.work_units();
        let completed_work_units = work_units.len() as u64;

        let completion = Arc::new(Completion::new(work_units.len()));

        let start = Instant::now();

        for unit in work_units {
            let unit = unit.clone();
            let workload = workload.clone();
            let completion = completion.clone();

            self.pool
                .execute(move || {
                    workload.execute(&unit);
                    completion.complete_one();
                })
                .expect("Threadance worker threads must be alive");
        }

        completion.wait();

        let elapsed = start.elapsed();

        RunResult {
            completed_work_units,
            elapsed_ns: elapsed.as_nanos() as u64,
            work_units_per_second: completed_work_units as f64 / elapsed.as_secs_f64(),
        }
    }
}
