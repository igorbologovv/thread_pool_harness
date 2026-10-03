use std::{num::NonZeroUsize, sync::Arc, time::Instant};

use bevy_tasks::{TaskPool, TaskPoolBuilder};

use crate::{result::RunResult, schedulers::Scheduler, workload::Workload};

pub struct BevyScheduler {
    pool: TaskPool,
}

impl BevyScheduler {
    pub fn new(workers: NonZeroUsize) -> Self {
        let pool = TaskPoolBuilder::new()
            .num_threads(workers.get())
            .thread_name("bevy-compute".to_string())
            .build();

        debug_assert_eq!(pool.thread_num(), workers.get());

        Self { pool }
    }
}

impl Scheduler for BevyScheduler {
    fn run<W: Workload>(&self, workload: &Arc<W>) -> RunResult {
        let work_units = workload.work_units();
        let completed_work_units = work_units.len() as u64;

        let start = Instant::now();

        let results = self.pool.scope_with_executor(false, None, |scope| {
            for unit in work_units {
                scope.spawn(async move { workload.execute(unit) });
            }
        });

        let checksum = results
            .into_iter()
            .fold(0u64, |acc, value| acc.wrapping_add(value));

        let elapsed = start.elapsed();

        RunResult {
            completed_work_units,
            elapsed_ns: elapsed.as_nanos() as u64,
            work_units_per_second: completed_work_units as f64 / elapsed.as_secs_f64(),
            checksum,
        }
    }
}
