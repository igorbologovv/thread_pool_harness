use std::{num::NonZeroUsize, sync::Arc};

use bevy_tasks::{TaskPool, TaskPoolBuilder};

use crate::{
    delivery::{DeliverySchedule, run_with_delivery},
    result::RunResult,
    schedulers::Scheduler,
    workload::Workload,
};

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
    fn run<W: Workload>(&self, workload: &Arc<W>, delivery: &DeliverySchedule) -> RunResult {
        run_with_delivery(workload, delivery, |workload, unit, completion| {
            self.pool
                .spawn(async move {
                    workload.execute(&unit);
                    completion.complete_one();
                })
                .detach();
        })
    }
}
