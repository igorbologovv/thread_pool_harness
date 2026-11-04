//! CPU-pinned periodic cache/memory interference on Linux.
//! The campaign supplies its settings from `cache_interference.toml`.
//! NOTE: this produces CPU, cache, bandwidth and DVFS interference,
//! not pure eviction isolated from other mechanisms.
use clap::Parser;
use std::{
    collections::BTreeSet,
    error::Error,
    hint::black_box,
    mem,
    sync::{
        Arc, OnceLock,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};
const MIB: usize = 1024 * 1024;
const LINE: usize = 64;
static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn stop(_: libc::c_int) {
    STOP.store(true, Ordering::Relaxed);
}

#[derive(Clone, Parser)]
#[command(about = "Pinned workers that write to a large cache working set each 1 ms")]
struct Args {
    /// Logical CPU IDs (topology and availability are host dependent).
    #[arg(long, default_value = "0-7")]
    cpus: String,
    /// Per-thread memory size; must be a power of two.
    #[arg(long, default_value_t = 1)]
    working_set_mib: usize,
    #[arg(long, default_value_t = 1)]
    touch_mib: usize,
    #[arg(long, default_value_t = 1000)]
    period_us: u64,
    /// 0 = run until Ctrl+C.
    #[arg(long, default_value_t = 15)]
    seconds: u64,
}

fn cpus(s: &str) -> Result<Vec<usize>, String> {
    let mut ids = BTreeSet::new();
    for term in s.split(',') {
        let (a, b) = if let Some((a, b)) = term.split_once('-') {
            (
                a.parse::<usize>().map_err(|_| "invalid CPU")?,
                b.parse::<usize>().map_err(|_| "invalid CPU")?,
            )
        } else {
            let a = term.parse::<usize>().map_err(|_| "invalid CPU")?;
            (a, a)
        };
        if a > b || b >= libc::CPU_SETSIZE as usize {
            return Err(format!("bad CPU range: {term}"));
        }
        for id in a..=b {
            if !ids.insert(id) {
                return Err(format!("duplicate CPU {id}"));
            }
        }
    }
    if ids.is_empty() {
        return Err("no CPUs".into());
    }
    Ok(ids.into_iter().collect())
}

fn pin(cpu: usize) -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut set: libc::cpu_set_t = unsafe { mem::zeroed() };
    unsafe {
        libc::CPU_ZERO(&mut set);
        libc::CPU_SET(cpu, &mut set);
        if libc::sched_setaffinity(0, mem::size_of_val(&set), &set) != 0 {
            return Err(format!("CPU {cpu}: {}", std::io::Error::last_os_error()).into());
        }
    }
    Ok(())
}

#[derive(Default)]
struct Stats {
    cpu: usize,
    ticks: u64,
    skipped: u64,
    overruns: u64,
    deadline_misses: u64,
    late_sum_ns: u128,
    late_max_ns: u128,
    work_sum_ns: u128,
    work_max_ns: u128,
    checksum: u64,
}

fn next_tick(tick: u64, elapsed_ns: u128, period_ns: u128) -> u64 {
    // A sweep finishing exactly at the next deadline need not skip it.
    (elapsed_ns.div_ceil(period_ns) as u64).max(tick + 1)
}

fn worker(
    cpu: usize,
    a: Args,
    start: Arc<OnceLock<Instant>>,
    ready: mpsc::Sender<Result<(), String>>,
    active: mpsc::Sender<usize>,
) -> Result<Stats, Box<dyn Error + Send + Sync>> {
    let init = (|| -> Result<Vec<u64>, Box<dyn Error + Send + Sync>> {
        pin(cpu)?;
        let mut data = vec![0_u64; a.working_set_mib * MIB / 8];
        // Prefault and initialize every cache line before timing.
        for i in (0..data.len()).step_by(LINE / 8) {
            data[i] = (i as u64) ^ cpu as u64;
        }
        black_box(&data);
        Ok(data)
    })();
    let mut data = match init {
        Ok(data) => {
            let _ = ready.send(Ok(()));
            data
        }
        Err(error) => {
            let _ = ready.send(Err(error.to_string()));
            return Err(error);
        }
    };
    let mut stats = Stats {
        cpu,
        ..Stats::default()
    };
    let start_at = loop {
        if STOP.load(Ordering::Relaxed) {
            return Ok(stats);
        }
        if let Some(v) = start.get() {
            break *v;
        }
        thread::yield_now();
    };
    let period = Duration::from_micros(a.period_us);
    let period_ns = period.as_nanos();
    let lines = data.len() / (LINE / 8); // power of two
    let touch_lines = a.touch_mib * MIB / LINE;
    let mut tick = 0_u64;
    let mut cursor = 0_usize;
    // Odd stride traverses every line of the power-of-two working set.
    let stride = 8191_usize;
    while !STOP.load(Ordering::Relaxed) {
        let target = start_at + Duration::from_micros(tick.saturating_mul(a.period_us));
        loop {
            let now = Instant::now();
            if now >= target || STOP.load(Ordering::Relaxed) {
                break;
            }
            thread::sleep(target.duration_since(now).min(Duration::from_millis(2)));
        }
        if STOP.load(Ordering::Relaxed) {
            break;
        }
        let t0 = Instant::now();
        let late_ns = t0.saturating_duration_since(target).as_nanos();
        stats.late_sum_ns += late_ns;
        stats.late_max_ns = stats.late_max_ns.max(late_ns);
        let mut sum = 0_u64;
        for _ in 0..touch_lines {
            // Modify every byte of each selected line, not just one u64.
            // Volatile preserves the requested read/modify/write volume.
            for word in &mut data[cursor * (LINE / 8)..(cursor + 1) * (LINE / 8)] {
                unsafe {
                    let old = std::ptr::read_volatile(word);
                    let new = old.wrapping_add(1);
                    std::ptr::write_volatile(word, new);
                    sum = sum.wrapping_add(new);
                }
            }
            cursor = (cursor + stride) & (lines - 1);
        }
        stats.checksum ^= black_box(sum);
        let end = Instant::now();
        let elapsed_ns = end.duration_since(t0).as_nanos();
        stats.work_sum_ns += elapsed_ns;
        stats.work_max_ns = stats.work_max_ns.max(elapsed_ns);
        stats.ticks += 1;
        if elapsed_ns > period_ns {
            stats.overruns += 1;
        }
        if late_ns + elapsed_ns > period_ns {
            stats.deadline_misses += 1;
        }
        if stats.ticks == 1 {
            let _ = active.send(cpu);
        }
        let elapsed_from_start = end.saturating_duration_since(start_at).as_nanos();
        let next = next_tick(tick, elapsed_from_start, period_ns);
        stats.skipped += next.saturating_sub(tick + 1);
        tick = next;
    }
    black_box(data);
    Ok(stats)
}

fn main() -> Result<(), Box<dyn Error>> {
    // A worker panic must terminate the process: otherwise the supervisor
    // could keep collecting with fewer interference threads than requested.
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        default_hook(info);
        std::process::abort();
    }));
    let args = Args::parse();
    let cpu_ids = cpus(&args.cpus)?;
    if !args.working_set_mib.is_power_of_two()
        || args.working_set_mib > 1024
        || args.touch_mib == 0
        || args.touch_mib > args.working_set_mib
        || args.period_us == 0
    {
        return Err("working-set must be a power of two <=1024 MiB; touch must be 1..working-set; period positive".into());
    }
    unsafe {
        libc::signal(libc::SIGINT, stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGTERM, stop as *const () as libc::sighandler_t);
    }
    let total_mib = args.working_set_mib * cpu_ids.len();
    println!(
        "Cache stress: {cpu_ids:?}, {total_mib} MiB total, {} MiB/tick/core, {} us period",
        args.touch_mib, args.period_us
    );
    let start = Arc::new(OnceLock::new());
    let (tx, rx) = mpsc::channel();
    let (active_tx, active_rx) = mpsc::channel();
    let mut joins = Vec::new();
    for cpu in &cpu_ids {
        let (a, start, tx) = (args.clone(), Arc::clone(&start), tx.clone());
        let id = *cpu;
        let active = active_tx.clone();
        let spawned = thread::Builder::new()
            .name(format!("cache-stress-{id}"))
            .spawn(move || worker(id, a, start, tx, active));
        match spawned {
            Ok(join) => joins.push(join),
            Err(error) => {
                STOP.store(true, Ordering::Relaxed);
                for join in joins {
                    let _ = join.join();
                }
                return Err(error.into());
            }
        }
    }
    drop(tx);
    drop(active_tx);
    let mut error = None;
    let mut initialized = 0;
    while initialized < cpu_ids.len() {
        if STOP.load(Ordering::Relaxed) {
            error = Some("Interrupted before all workers initialized".to_owned());
            break;
        }
        match rx.recv_timeout(Duration::from_millis(20)) {
            Ok(Ok(())) => initialized += 1,
            Ok(Err(e)) => {
                error = Some(e);
                break;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(e) => {
                error = Some(e.to_string());
                break;
            }
        }
    }
    if error.is_some() {
        STOP.store(true, Ordering::Relaxed);
    }
    let start_at = Instant::now();
    let _ = start.set(start_at);
    if let Some(e) = error {
        for j in joins {
            let _ = j.join();
        }
        return Err(e.into());
    }
    // READY requires every pinned, prefaulted worker to finish its first
    // sweep, so even a very short benchmark starts with active interference.
    let mut active_cpus = BTreeSet::new();
    while active_cpus.len() < cpu_ids.len() && !STOP.load(Ordering::Relaxed) {
        match active_rx.recv_timeout(Duration::from_millis(20)) {
            Ok(cpu) => {
                active_cpus.insert(cpu);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(error) => return Err(error.into()),
        }
    }
    if !STOP.load(Ordering::Relaxed) {
        println!(
            "READY threads={} cpus={}",
            cpu_ids.len(),
            cpu_ids
                .iter()
                .map(usize::to_string)
                .collect::<Vec<_>>()
                .join(",")
        );
    }
    use std::io::Write;
    std::io::stdout().flush()?;
    while !STOP.load(Ordering::Relaxed) {
        if args.seconds > 0 && Instant::now() >= start_at + Duration::from_secs(args.seconds) {
            STOP.store(true, Ordering::Relaxed);
            break;
        }
        thread::sleep(Duration::from_millis(100));
    }
    println!(
        "CPU  ticks  missed  overruns  avg_late_us  max_late_us  avg_work_us  max_work_us  checksum"
    );
    let mut all_stats = Vec::new();
    for j in joins {
        let s = j
            .join()
            .map_err(|_| "worker panicked")?
            .map_err(std::io::Error::other)?;
        let n = u128::from(s.ticks.max(1));
        println!(
            "{:>3} {:>6} {:>7} {:>9} {:>12.1} {:>12.1} {:>12.1} {:>12.1} {:016x}",
            s.cpu,
            s.ticks,
            s.skipped,
            s.overruns,
            s.late_sum_ns as f64 / n as f64 / 1000.0,
            s.late_max_ns as f64 / 1000.0,
            s.work_sum_ns as f64 / n as f64 / 1000.0,
            s.work_max_ns as f64 / 1000.0,
            s.checksum
        );
        all_stats.push(serde_json::json!({
            "cpu": s.cpu,
            "iterations": s.ticks,
            "completed_within_period": s.ticks.saturating_sub(s.overruns),
            "overruns": s.overruns,
            "deadline_misses": s.deadline_misses,
            "skipped_intervals": s.skipped,
            "average_lateness_ns": s.late_sum_ns as f64 / n as f64,
            "maximum_lateness_ns": s.late_max_ns as f64,
            "average_work_ns": s.work_sum_ns as f64 / n as f64,
            "maximum_work_ns": s.work_max_ns as f64,
            "checksum": format!("{:016x}", s.checksum),
        }));
    }
    println!(
        "CACHE_INTERFERER_STATS {}",
        serde_json::json!({
            "period_us": args.period_us,
            "touch_bytes_per_iteration_per_thread": args.touch_mib * MIB,
            "total_requested_bytes_per_period": args.touch_mib * MIB * cpu_ids.len(),
            "all_iterations_within_period": all_stats.iter().all(|s| s["overruns"] == 0),
            "all_deadlines_met": all_stats.iter().all(|s| s["deadline_misses"] == 0),
            "threads": all_stats,
        })
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{cpus, next_tick};
    #[test]
    fn skips_only_elapsed_intervals() {
        assert_eq!(next_tick(0, 900, 1000), 1);
        assert_eq!(next_tick(0, 1000, 1000), 1);
        assert_eq!(next_tick(0, 1001, 1000), 2);
        assert_eq!(next_tick(2, 5500, 1000), 6);
    }
    #[test]
    fn parses_cpu_ranges() {
        assert_eq!(cpus("0-2,8").unwrap(), vec![0, 1, 2, 8]);
        assert!(cpus("0,0").is_err());
        for value in ["", "2-1", "1024", "-1", "0-2,2", "no"] {
            assert!(cpus(value).is_err(), "{value}");
        }
    }
}
