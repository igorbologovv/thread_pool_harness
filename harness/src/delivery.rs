use std::{
    fmt,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    thread::{self, Thread},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use rand::{RngExt, SeedableRng, rngs::StdRng};

use crate::{result::RunResult, workload::Workload};

const STEADY_SHAPE: f64 = 4.0;
const VARIABLE_SHAPE: f64 = 1.0;
const BURSTY_SHAPE: f64 = 0.5;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryMode {
    AllAtOnce,
    FixedArrivals,
    SteadyArrivals,
    VariableArrivals,
    BurstyArrivals,
}

impl DeliveryMode {
    fn weibull_shape(self) -> Option<f64> {
        match self {
            Self::AllAtOnce | Self::FixedArrivals => None,
            Self::SteadyArrivals => Some(STEADY_SHAPE),
            Self::VariableArrivals => Some(VARIABLE_SHAPE),
            Self::BurstyArrivals => Some(BURSTY_SHAPE),
        }
    }
}

impl fmt::Display for DeliveryMode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::AllAtOnce => "all-at-once",
            Self::FixedArrivals => "fixed-arrivals",
            Self::SteadyArrivals => "steady-arrivals",
            Self::VariableArrivals => "variable-arrivals",
            Self::BurstyArrivals => "bursty-arrivals",
        })
    }
}

#[derive(Debug)]
pub struct DeliverySchedule {
    mode: DeliveryMode,
    offsets: Vec<Duration>,
    arrival_window: Duration,
    arrival_seed: u64,
}

impl DeliverySchedule {
    pub fn generate(
        mode: DeliveryMode,
        work_units: usize,
        arrival_window: Duration,
        arrival_seed: u64,
    ) -> Self {
        assert!(
            work_units > 0,
            "delivery schedule requires at least one work unit"
        );

        let offsets = match mode {
            DeliveryMode::AllAtOnce => vec![Duration::ZERO; work_units],

            _ if work_units == 1 => vec![Duration::ZERO],

            DeliveryMode::FixedArrivals => {
                assert!(
                    !arrival_window.is_zero(),
                    "scheduled delivery requires a non-zero arrival window"
                );

                generate_fixed_offsets(work_units, arrival_window)
            }

            DeliveryMode::SteadyArrivals
            | DeliveryMode::VariableArrivals
            | DeliveryMode::BurstyArrivals => {
                assert!(
                    !arrival_window.is_zero(),
                    "scheduled delivery requires a non-zero arrival window"
                );

                generate_scheduled_offsets(
                    work_units,
                    arrival_window,
                    arrival_seed,
                    mode.weibull_shape()
                        .expect("pseudo-random delivery mode must define a Weibull shape"),
                )
            }
        };

        Self {
            mode,
            offsets,
            arrival_window,
            arrival_seed,
        }
    }

    pub fn mode(&self) -> DeliveryMode {
        self.mode
    }

    pub fn offsets(&self) -> &[Duration] {
        &self.offsets
    }

    pub fn arrival_rate(&self) -> Option<f64> {
        if self.mode == DeliveryMode::AllAtOnce || self.offsets.len() <= 1 {
            return None;
        }

        Some((self.offsets.len() - 1) as f64 / self.arrival_window.as_secs_f64())
    }
}

impl fmt::Display for DeliverySchedule {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "Delivery schedule")?;
        writeln!(f, "  mode:           {}", self.mode)?;
        writeln!(f, "  work units:     {}", self.offsets.len())?;

        if self.mode != DeliveryMode::AllAtOnce {
            if let Some(arrival_rate) = self.arrival_rate() {
                writeln!(f, "  arrival rate:   {arrival_rate:.3} work units/s")?;

                writeln!(
                    f,
                    "  mean gap:       {:?}",
                    Duration::from_secs_f64(1.0 / arrival_rate)
                )?;
            }

            writeln!(f, "  arrival window: {:?}", self.arrival_window)?;

            match self.mode {
                DeliveryMode::FixedArrivals => {
                    write!(f, "  pattern:        deterministic periodic")
                }

                DeliveryMode::SteadyArrivals
                | DeliveryMode::VariableArrivals
                | DeliveryMode::BurstyArrivals => {
                    writeln!(f, "  arrival seed:   {}", self.arrival_seed)?;

                    let shape = self
                        .mode
                        .weibull_shape()
                        .expect("pseudo-random delivery mode must define a Weibull shape");

                    write!(f, "  Weibull shape:  {shape}")
                }

                DeliveryMode::AllAtOnce => unreachable!(),
            }
        } else {
            Ok(())
        }
    }
}

fn generate_fixed_offsets(work_units: usize, arrival_window: Duration) -> Vec<Duration> {
    debug_assert!(work_units > 1);

    let window_nanos = u64::try_from(arrival_window.as_nanos())
        .expect("arrival window must fit in u64 nanoseconds");

    let gap_count = (work_units - 1) as u128;

    (0..work_units)
        .map(|index| {
            // Spread N arrivals uniformly across [0, T].
            //
            // Integer nanosecond resolution means adjacent gaps can differ by
            // at most one nanosecond when T is not exactly divisible by N - 1.
            let nanos = (u128::from(window_nanos) * index as u128) / gap_count;

            Duration::from_nanos(
                u64::try_from(nanos).expect("fixed arrival offset must fit in u64 nanoseconds"),
            )
        })
        .collect()
}

fn generate_scheduled_offsets(
    work_units: usize,
    arrival_window: Duration,
    arrival_seed: u64,
    shape: f64,
) -> Vec<Duration> {
    debug_assert!(work_units > 1);
    debug_assert!(shape > 0.0);

    let mut rng = StdRng::seed_from_u64(arrival_seed);

    let raw_gaps: Vec<f64> = (0..work_units - 1)
        .map(|_| {
            // Inverse-transform sampling for a unit-scale Weibull variable.
            //
            // rand::<f64>() is in [0, 1), therefore 1-U is in (0, 1].
            let uniform = (1.0 - rng.random::<f64>()).max(f64::MIN_POSITIVE);

            (-uniform.ln()).powf(1.0 / shape)
        })
        .collect();

    let raw_total: f64 = raw_gaps.iter().sum();

    assert!(
        raw_total.is_finite() && raw_total > 0.0,
        "generated arrival gaps must have a finite positive sum"
    );

    let window_nanos = u64::try_from(arrival_window.as_nanos())
        .expect("arrival window must fit in u64 nanoseconds");

    let mut offsets = Vec::with_capacity(work_units);
    offsets.push(Duration::ZERO);

    let mut cumulative = 0.0;

    for (index, gap) in raw_gaps.iter().enumerate() {
        cumulative += gap;

        let nanos = if index + 1 == raw_gaps.len() {
            // Make the final arrival exactly equal to the configured window.
            window_nanos
        } else {
            ((window_nanos as f64) * (cumulative / raw_total)).round() as u64
        };

        offsets.push(Duration::from_nanos(nanos.min(window_nanos)));
    }

    debug_assert_eq!(offsets.len(), work_units);
    debug_assert_eq!(offsets[0], Duration::ZERO);
    debug_assert_eq!(
        offsets.last().copied(),
        Some(arrival_window),
        "last scheduled arrival must equal the configured window"
    );
    debug_assert!(offsets.windows(2).all(|pair| pair[0] <= pair[1]));

    offsets
}

pub(crate) struct Completion {
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

    pub(crate) fn complete_one(&self) {
        let previous = self.remaining.fetch_sub(1, Ordering::AcqRel);

        assert!(previous > 0, "work unit completed more than once");

        if previous == 1 {
            self.waiter.unpark();
        }
    }

    fn wait(&self) {
        while self.remaining.load(Ordering::Acquire) != 0 {
            thread::park();
        }
    }
}

pub(crate) fn run_with_delivery<W, Submit>(
    workload: &Arc<W>,
    schedule: &DeliverySchedule,
    mut submit: Submit,
) -> RunResult
where
    W: Workload,
    Submit: FnMut(Arc<W>, W::WorkUnit, Arc<Completion>),
{
    let work_units = workload.work_units();

    assert_eq!(
        work_units.len(),
        schedule.offsets().len(),
        "delivery schedule must contain one offset per work unit"
    );

    let completed_work_units = work_units.len() as u64;
    let completion = Arc::new(Completion::new(work_units.len()));

    let started_unix_ns: u64 = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before Unix epoch")
        .as_nanos()
        .try_into()
        .expect("run timestamp does not fit u64");

    let start = Instant::now();

    match schedule.mode() {
        DeliveryMode::AllAtOnce => {
            for unit in work_units {
                submit(Arc::clone(workload), unit.clone(), Arc::clone(&completion));
            }
        }

        _ => {
            for (unit, &emit_offset) in work_units.iter().zip(schedule.offsets()) {
                sleep_until(start + emit_offset);

                submit(Arc::clone(workload), unit.clone(), Arc::clone(&completion));
            }
        }
    }

    completion.wait();

    let elapsed = start.elapsed();

    RunResult {
        started_unix_ns,
        completed_work_units,
        elapsed_ns: elapsed.as_nanos() as u64,
        work_units_per_second: completed_work_units as f64 / elapsed.as_secs_f64(),
    }
}

fn sleep_until(deadline: Instant) {
    loop {
        let now = Instant::now();

        if now >= deadline {
            return;
        }

        thread::sleep(deadline - now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_at_once_offsets_are_zero() {
        let schedule =
            DeliverySchedule::generate(DeliveryMode::AllAtOnce, 8, Duration::from_millis(100), 777);

        assert_eq!(schedule.offsets(), &[Duration::ZERO; 8]);
    }

    #[test]
    fn same_seed_produces_same_schedule() {
        let first = DeliverySchedule::generate(
            DeliveryMode::VariableArrivals,
            64,
            Duration::from_millis(100),
            777,
        );

        let second = DeliverySchedule::generate(
            DeliveryMode::VariableArrivals,
            64,
            Duration::from_millis(100),
            777,
        );

        assert_eq!(first.offsets(), second.offsets());
    }

    #[test]
    fn different_seed_changes_schedule() {
        let first = DeliverySchedule::generate(
            DeliveryMode::VariableArrivals,
            64,
            Duration::from_millis(100),
            1,
        );

        let second = DeliverySchedule::generate(
            DeliveryMode::VariableArrivals,
            64,
            Duration::from_millis(100),
            2,
        );

        assert_ne!(first.offsets(), second.offsets());
    }

    #[test]
    fn scheduled_offsets_are_ordered_and_span_exact_window() {
        let window = Duration::from_millis(100);

        for mode in [
            DeliveryMode::FixedArrivals,
            DeliveryMode::SteadyArrivals,
            DeliveryMode::VariableArrivals,
            DeliveryMode::BurstyArrivals,
        ] {
            let schedule = DeliverySchedule::generate(mode, 64, window, 777);

            assert_eq!(schedule.offsets()[0], Duration::ZERO);
            assert_eq!(schedule.offsets().last().copied(), Some(window));

            assert!(schedule.offsets().windows(2).all(|pair| pair[0] <= pair[1]));
        }
    }

    #[test]
    fn fixed_arrivals_are_periodic_and_ignore_seed() {
        let units = 11;
        let window = Duration::from_millis(100);

        let first = DeliverySchedule::generate(DeliveryMode::FixedArrivals, units, window, 1);

        let second = DeliverySchedule::generate(DeliveryMode::FixedArrivals, units, window, 999);

        // Fixed arrivals are deterministic: the seed must have no effect.
        assert_eq!(first.offsets(), second.offsets());

        let gaps: Vec<Duration> = first
            .offsets()
            .windows(2)
            .map(|pair| pair[1] - pair[0])
            .collect();

        assert_eq!(gaps.len(), units - 1);
        assert!(gaps.iter().all(|gap| *gap == Duration::from_millis(10)));
    }

    #[test]
    fn delivery_modes_have_increasing_gap_variability() {
        let units = 10_000;
        let window = Duration::from_secs(10);
        let seed = 777;

        let fixed = DeliverySchedule::generate(DeliveryMode::FixedArrivals, units, window, seed);

        let steady = DeliverySchedule::generate(DeliveryMode::SteadyArrivals, units, window, seed);

        let variable =
            DeliverySchedule::generate(DeliveryMode::VariableArrivals, units, window, seed);

        let bursty = DeliverySchedule::generate(DeliveryMode::BurstyArrivals, units, window, seed);

        let fixed_cv = gap_coefficient_of_variation(fixed.offsets());
        let steady_cv = gap_coefficient_of_variation(steady.offsets());
        let variable_cv = gap_coefficient_of_variation(variable.offsets());
        let bursty_cv = gap_coefficient_of_variation(bursty.offsets());

        assert!(fixed_cv < steady_cv);
        assert!(steady_cv < variable_cv);
        assert!(variable_cv < bursty_cv);
    }

    fn gap_coefficient_of_variation(offsets: &[Duration]) -> f64 {
        let gaps: Vec<f64> = offsets
            .windows(2)
            .map(|pair| (pair[1] - pair[0]).as_nanos() as f64)
            .collect();

        let mean = gaps.iter().sum::<f64>() / gaps.len() as f64;

        let variance = gaps
            .iter()
            .map(|gap| {
                let difference = gap - mean;
                difference * difference
            })
            .sum::<f64>()
            / gaps.len() as f64;

        variance.sqrt() / mean
    }

    #[test]
    #[ignore = "manual timing diagnostic; depends on host scheduler timing"]
    fn diagnose_pacer_lateness() {
        const WORK_UNITS: usize = 64;
        const WINDOW: Duration = Duration::from_millis(100);
        const SEED: u64 = 777;

        for mode in [
            DeliveryMode::FixedArrivals,
            DeliveryMode::SteadyArrivals,
            DeliveryMode::VariableArrivals,
            DeliveryMode::BurstyArrivals,
        ] {
            let schedule = DeliverySchedule::generate(mode, WORK_UNITS, WINDOW, SEED);

            let start = Instant::now();
            let mut lateness = Vec::with_capacity(WORK_UNITS);

            for &offset in schedule.offsets() {
                sleep_until(start + offset);

                let actual = start.elapsed();
                lateness.push(actual.saturating_sub(offset));
            }

            let mut lateness_ns: Vec<u128> = lateness.iter().map(Duration::as_nanos).collect();

            lateness_ns.sort_unstable();

            let sum: u128 = lateness_ns.iter().sum();
            let mean_ns = sum / lateness_ns.len() as u128;
            let p95_index = ((lateness_ns.len() - 1) * 95) / 100;

            let mean = Duration::from_nanos(mean_ns as u64);
            let p95 = Duration::from_nanos(lateness_ns[p95_index] as u64);
            let max = Duration::from_nanos(*lateness_ns.last().unwrap() as u64);

            println!("{mode}: mean lateness={mean:?}, p95={p95:?}, max={max:?}");
        }
    }
}
