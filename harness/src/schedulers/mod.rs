//! Scheduler implementations used by the benchmark harness.
//!
//! Schedulers define how workload items are distributed and executed across
//! worker threads. Scheduler implementations must remain independent from
//! workload-specific processing logic.

pub mod bevy;
pub mod rayon;
pub mod threadance;

use std::sync::Arc;

use crate::{delivery::DeliverySchedule, result::RunResult, workload::Workload};

/// Common interface implemented by every scheduler used by the harness.
pub trait Scheduler {
    fn run<W: Workload>(&self, workload: &Arc<W>, delivery: &DeliverySchedule) -> RunResult;
}

#[cfg(test)]
mod tests {
    use std::{
        num::NonZeroUsize,
        sync::{
            Arc,
            atomic::{AtomicUsize, Ordering},
        },
        time::Duration,
    };

    use super::{
        Scheduler, bevy::BevyScheduler, rayon::RayonScheduler, threadance::ThreadanceScheduler,
    };
    use crate::{
        delivery::{DeliveryMode, DeliverySchedule},
        workload::{CommonWorkloadConfig, Workload},
    };

    const WORK_UNITS: usize = 64;
    const WORKERS: usize = 4;

    struct CountingWorkload {
        units: Vec<usize>,
        executions: Vec<AtomicUsize>,
    }

    impl CountingWorkload {
        fn new(work_units: usize) -> Self {
            Self {
                units: (0..work_units).collect(),
                executions: (0..work_units).map(|_| AtomicUsize::new(0)).collect(),
            }
        }

        fn assert_exactly_once(&self) {
            for (index, count) in self.executions.iter().enumerate() {
                assert_eq!(
                    count.load(Ordering::SeqCst),
                    1,
                    "work unit {index} did not execute exactly once"
                );
            }
        }
    }

    impl Workload for CountingWorkload {
        type WorkUnit = usize;
        type Config = ();

        fn generate(common: &CommonWorkloadConfig, _config: &Self::Config) -> Self {
            Self::new(common.work_units)
        }

        fn work_units(&self) -> &[Self::WorkUnit] {
            &self.units
        }

        fn execute(&self, unit: &Self::WorkUnit) {
            self.executions[*unit].fetch_add(1, Ordering::SeqCst);
        }
    }

    fn schedule() -> DeliverySchedule {
        DeliverySchedule::generate(
            DeliveryMode::AllAtOnce,
            WORK_UNITS,
            Duration::from_millis(100),
            1,
        )
    }

    fn verify<S: Scheduler>(scheduler: &S) {
        let workload = Arc::new(CountingWorkload::new(WORK_UNITS));
        let delivery = schedule();

        let result = scheduler.run(&workload, &delivery);

        assert_eq!(result.completed_work_units, WORK_UNITS as u64);
        workload.assert_exactly_once();
    }

    #[test]
    fn rayon_executes_every_work_unit_exactly_once() {
        let scheduler = RayonScheduler::new(NonZeroUsize::new(WORKERS).unwrap()).unwrap();

        verify(&scheduler);
    }

    #[test]
    fn bevy_executes_every_work_unit_exactly_once() {
        let scheduler = BevyScheduler::new(NonZeroUsize::new(WORKERS).unwrap());

        verify(&scheduler);
    }

    #[test]
    fn threadance_executes_every_work_unit_exactly_once() {
        let scheduler = ThreadanceScheduler::new(
            NonZeroUsize::new(WORKERS).unwrap(),
            NonZeroUsize::new(WORK_UNITS).unwrap(),
        )
        .unwrap();

        verify(&scheduler);
    }
}
