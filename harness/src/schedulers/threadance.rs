use std::{
    io,
    num::NonZeroUsize,
    sync::{Arc, mpsc::sync_channel},
    time::Instant,
};

use ::threadance::ThreadPool;

use crate::{result::RunResult, schedulers::Scheduler, workload::Workload};

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

        // Large enough to hold one result for every submitted work unit.
        // Workers therefore never block waiting for the benchmark thread to
        // start collecting results.
        let (result_sender, result_receiver) = sync_channel(work_units.len());

        let start = Instant::now();

        for unit in work_units.iter().cloned() {
            let workload = workload.clone();
            let result_sender = result_sender.clone();

            self.pool
                .execute(move || {
                    let result = workload.execute(&unit);

                    result_sender
                        .send(result)
                        .expect("benchmark result receiver must be alive");
                })
                .expect("Threadance worker threads must be alive");
        }

        drop(result_sender);

        let mut checksum = 0u64;

        for _ in 0..work_units.len() {
            let value = result_receiver
                .recv()
                .expect("Threadance worker failed to return a result");

            checksum = checksum.wrapping_add(value);
        }

        let elapsed = start.elapsed();

        RunResult {
            completed_work_units,
            elapsed_ns: elapsed.as_nanos() as u64,
            work_units_per_second: completed_work_units as f64 / elapsed.as_secs_f64(),
            checksum,
        }
    }
}
