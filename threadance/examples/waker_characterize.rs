use std::{
    env,
    num::NonZeroUsize,
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

use threadance::{ThreadPool, WaitStrategy};

const DEFAULT_GAPS_US: &[u64] = &[5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 75, 100];
const DEFAULT_ITERATIONS: usize = 200;

fn main() {
    let mut args = env::args().skip(1);
    let spin_us = args
        .next()
        .map(|value| value.parse::<u64>().expect("spin_us must be an integer"))
        .unwrap_or(40);
    let iterations = args
        .next()
        .map(|value| {
            value
                .parse::<usize>()
                .expect("iterations must be an integer")
        })
        .unwrap_or(DEFAULT_ITERATIONS);

    let gaps = args
        .next()
        .map(|value| parse_gaps(&value))
        .unwrap_or_else(|| DEFAULT_GAPS_US.to_vec());

    eprintln!("Threadance waker characterization");
    eprintln!("spin threshold : {spin_us} us");
    eprintln!("iterations     : {iterations} per gap");
    eprintln!("gaps           : {gaps:?} us");
    eprintln!();

    println!(
        "spin_us,gap_us,iterations,slept_count,slept_pct,mean_start_latency_us,median_start_latency_us,p95_start_latency_us"
    );

    for gap_us in gaps {
        let result = characterize(spin_us, gap_us, iterations);
        println!(
            "{spin_us},{gap_us},{iterations},{},{:.3},{:.3},{:.3},{:.3}",
            result.slept_count,
            100.0 * result.slept_count as f64 / iterations as f64,
            result.mean_latency_us,
            result.median_latency_us,
            result.p95_latency_us,
        );
    }
}

struct ResultRow {
    slept_count: usize,
    mean_latency_us: f64,
    median_latency_us: f64,
    p95_latency_us: f64,
}

fn characterize(spin_us: u64, gap_us: u64, iterations: usize) -> ResultRow {
    assert!(iterations > 0);

    let pool = ThreadPool::with_wait_strategy(
        NonZeroUsize::new(1).unwrap(),
        NonZeroUsize::new(4).unwrap(),
        WaitStrategy::SpinThenPark(Duration::from_micros(spin_us)),
    )
    .unwrap();

    pool.set_wait_instrumentation(true);
    pool.reset_wait_stats();

    // Kick the worker once so the measurement loop starts from a known
    // post-job idle transition rather than from pool construction timing.
    let (warmup_tx, warmup_rx) = mpsc::sync_channel(1);
    pool.execute(move || warmup_tx.send(()).unwrap()).unwrap();
    warmup_rx.recv().unwrap();

    wait_for_next_empty(&pool, 0);

    let mut slept_count = 0usize;
    let mut latencies_ns = Vec::with_capacity(iterations);
    let mut last_empty = pool.wait_stats().empty_observations;

    for _ in 0..iterations {
        // The worker has just observed an empty queue. Delay the producer by
        // the requested gap, then sample whether the worker is actually asleep
        // immediately before the next job is published.
        busy_wait(Duration::from_micros(gap_us));

        if pool.wait_stats().sleeping_waiters != 0 {
            slept_count += 1;
        }

        let submitted_at = Instant::now();
        let (done_tx, done_rx) = mpsc::sync_channel(1);
        pool.execute(move || {
            let start_latency = submitted_at.elapsed();
            done_tx.send(start_latency).unwrap();
        })
        .unwrap();

        latencies_ns.push(done_rx.recv().unwrap().as_nanos() as u64);

        wait_for_next_empty(&pool, last_empty);
        last_empty = pool.wait_stats().empty_observations;
    }

    latencies_ns.sort_unstable();
    let mean_ns = latencies_ns.iter().copied().sum::<u64>() as f64 / latencies_ns.len() as f64;
    let median_ns = percentile(&latencies_ns, 0.50) as f64;
    let p95_ns = percentile(&latencies_ns, 0.95) as f64;

    ResultRow {
        slept_count,
        mean_latency_us: mean_ns / 1_000.0,
        median_latency_us: median_ns / 1_000.0,
        p95_latency_us: p95_ns / 1_000.0,
    }
}

fn wait_for_next_empty(pool: &ThreadPool, previous: u64) {
    let deadline = Instant::now() + Duration::from_secs(1);
    while pool.wait_stats().empty_observations <= previous {
        assert!(
            Instant::now() < deadline,
            "worker did not observe an empty queue within one second"
        );
        thread::yield_now();
    }
}

fn busy_wait(duration: Duration) {
    let deadline = Instant::now() + duration;
    while Instant::now() < deadline {
        std::hint::spin_loop();
    }
}

fn percentile(sorted: &[u64], quantile: f64) -> u64 {
    assert!(!sorted.is_empty());
    let index = ((sorted.len() - 1) as f64 * quantile).round() as usize;
    sorted[index]
}

fn parse_gaps(value: &str) -> Vec<u64> {
    let gaps = value
        .split(',')
        .map(|part| {
            part.trim()
                .parse::<u64>()
                .expect("gap list must contain comma-separated integers")
        })
        .collect::<Vec<_>>();

    assert!(!gaps.is_empty(), "gap list must not be empty");
    gaps
}
