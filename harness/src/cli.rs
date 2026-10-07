use std::{fmt, num::NonZeroUsize, path::PathBuf, time::Duration};

use clap::{ArgGroup, Parser, ValueEnum};

use crate::delivery::DeliveryMode;

#[derive(Debug, Parser)]
#[command(about = "Thread-pool scheduling benchmark harness")]
#[command(group(
    ArgGroup::new("delivery")
        .required(true)
        .multiple(false)
        .args([
            "all_at_once",
            "fixed_arrivals",
            "steady_arrivals",
            "variable_arrivals",
            "bursty_arrivals",
        ])
))]
pub struct Cli {
    /// Workload to execute.
    #[arg(long, value_enum)]
    pub workload: WorkloadKind,

    /// Scheduler implementation to benchmark.
    #[arg(long, value_enum, default_value = "rayon")]
    pub scheduler: SchedulerKind,

    /// Number of worker threads.
    #[arg(long)]
    pub workers: NonZeroUsize,

    /// Capacity of the Threadance task queue.
    #[arg(long, default_value = "1024")]
    pub queue_capacity: NonZeroUsize,

    /// Time a Threadance worker actively polls an empty queue before blocking.
    ///
    /// Zero means immediate blocking.
    #[arg(long, default_value_t = 0)]
    pub threadance_spin_us: u64,

    /// Submit every work unit immediately at the start of the run.
    #[arg(long)]
    pub all_at_once: bool,

    /// Deliver work units at deterministic periodic intervals.
    #[arg(long)]
    pub fixed_arrivals: bool,

    /// Deliver work units using low-variability pseudo-random inter-arrival gaps.
    #[arg(long)]
    pub steady_arrivals: bool,

    /// Deliver work units using moderately variable pseudo-random inter-arrival gaps.
    #[arg(long)]
    pub variable_arrivals: bool,

    /// Deliver work units using highly variable pseudo-random inter-arrival gaps.
    #[arg(long)]
    pub bursty_arrivals: bool,

    /// Mean scheduled arrival rate in work units per second.
    ///
    /// Required for scheduled delivery modes and invalid with --all-at-once.
    #[arg(
        long,
        value_parser = parse_positive_f64,
        required_unless_present = "all_at_once",
        conflicts_with = "all_at_once"
    )]
    pub arrival_rate: Option<f64>,

    /// Seed used to generate pseudo-random arrival schedules.
    ///
    /// Ignored by --fixed-arrivals and --all-at-once.
    /// This is separate from --seed, which controls workload generation.
    #[arg(long, default_value_t = 1)]
    pub arrival_seed: u64,

    /// Number of independently schedulable work units.
    #[arg(long)]
    pub work_units: NonZeroUsize,

    /// Number of independent matrix operations performed by each compute-heavy
    /// work unit.
    #[arg(long)]
    pub operations_per_work_unit: Option<NonZeroUsize>,

    /// Total BLS validator-set size.
    #[arg(long, default_value = "2000")]
    pub validators: NonZeroUsize,

    /// Number of BLS signers included in each aggregate signature.
    #[arg(long, default_value = "1600")]
    pub signers_per_certificate: NonZeroUsize,

    /// Number of BLS certificate verifications grouped into one work unit.
    #[arg(long, default_value = "1")]
    pub certificates_per_work_unit: NonZeroUsize,

    /// Seed used for deterministic workload generation.
    #[arg(long, default_value_t = 1)]
    pub seed: u64,

    /// Number of warm-up runs discarded before measurement.
    #[arg(long, default_value_t = 5)]
    pub warmup: usize,

    /// Number of measured benchmark runs.
    #[arg(long, default_value = "50")]
    pub runs: NonZeroUsize,

    /// Profiling mode.
    ///
    /// `none` records only harness timing.
    /// `standard` records the primary perf counters used in experiments.
    /// `deep` records additional cache and TLB counters using separate diagnostic perf passes.
    #[arg(long, value_enum, default_value = "none")]
    pub profile: ProfileMode,

    /// SQLite database used to store benchmark metadata and measured runs.
    #[arg(long, default_value = "results/benchmarks.sqlite3")]
    pub database: PathBuf,
}

impl Cli {
    pub fn delivery_mode(&self) -> DeliveryMode {
        match (
            self.all_at_once,
            self.fixed_arrivals,
            self.steady_arrivals,
            self.variable_arrivals,
            self.bursty_arrivals,
        ) {
            (true, false, false, false, false) => DeliveryMode::AllAtOnce,
            (false, true, false, false, false) => DeliveryMode::FixedArrivals,
            (false, false, true, false, false) => DeliveryMode::SteadyArrivals,
            (false, false, false, true, false) => DeliveryMode::VariableArrivals,
            (false, false, false, false, true) => DeliveryMode::BurstyArrivals,
            _ => unreachable!("clap validates the delivery-mode argument group"),
        }
    }

    /// Arrival window implied by the requested mean arrival rate.
    ///
    /// For N arrivals there are N - 1 inter-arrival gaps:
    ///
    ///     T = (N - 1) / lambda
    pub fn arrival_window(&self) -> Option<Duration> {
        self.arrival_rate.map(|arrival_rate| {
            if self.work_units.get() <= 1 {
                Duration::ZERO
            } else {
                Duration::from_secs_f64((self.work_units.get() - 1) as f64 / arrival_rate)
            }
        })
    }
}

fn parse_positive_f64(value: &str) -> Result<f64, String> {
    let value: f64 = value
        .parse()
        .map_err(|_| "arrival rate must be a number".to_owned())?;

    if value.is_finite() && value > 0.0 {
        Ok(value)
    } else {
        Err("arrival rate must be finite and greater than zero".to_owned())
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum WorkloadKind {
    ComputeHeavy,
    BlsAggregateVerify,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum SchedulerKind {
    Rayon,
    Bevy,
    Threadance,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum ProfileMode {
    None,
    Standard,
    Deep,
}

impl fmt::Display for WorkloadKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let value = self
            .to_possible_value()
            .expect("workload variant must have a CLI representation");

        f.write_str(value.get_name())
    }
}
