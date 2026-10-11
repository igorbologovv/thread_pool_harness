use std::{
    fmt, io,
    num::NonZeroUsize,
    sync::{
        Arc, OnceLock,
        atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering, fence},
    },
    thread::{Builder, JoinHandle},
    time::{Duration, Instant},
};

use crossbeam_channel::{RecvError, SendError, TryRecvError, bounded};

type Job = Box<dyn FnOnce() + Send + 'static>;

static MONOTONIC_ORIGIN: OnceLock<Instant> = OnceLock::new();

// crossbeam_utils::Backoff 0.8.23 constants used by Agave wake-channel.
const AGAVE_BACKOFF_SPIN_LIMIT: u32 = 6;
const AGAVE_BACKOFF_YIELD_LIMIT: u32 = 10;

/// Monotonic timestamp used by the wake-characterization utility.
pub fn monotonic_now_ns() -> u64 {
    MONOTONIC_ORIGIN
        .get_or_init(Instant::now)
        .elapsed()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}

/// Strategy used by idle workers while waiting for more work.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaitStrategy {
    /// Use Crossbeam's native blocking receive path.
    Crossbeam,

    /// Stay hot for a fixed duration, then park until work actually arrives.
    ///
    /// While hot, the worker does nothing except poll the bounded Crossbeam
    /// queue and issue a CPU spin hint. Once the duration expires, it moves to
    /// the futex-backed cold path and does not start another spin period until
    /// it has received and executed a job.
    SpinThenPark(Duration),

    /// Use the fixed iteration-based backoff policy from Agave wake-channel.
    AgaveBackoff,
}

/// Snapshot of optional Threadance wait-path instrumentation.
///
/// Instrumentation is disabled by default and exists only for the small wake
/// characterization utility.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct WaitStatsSnapshot {
    pub empty_observations: u64,
    pub last_empty_ns: u64,
    pub sleeping_waiters: usize,
}

#[derive(Default)]
struct WaitStats {
    enabled: AtomicBool,
    empty_observations: AtomicU64,
    last_empty_ns: AtomicU64,
}

impl WaitStats {
    fn reset(&self) {
        self.empty_observations.store(0, Ordering::Relaxed);
        self.last_empty_ns.store(0, Ordering::Relaxed);
    }

    fn record_empty(&self) {
        if self.enabled.load(Ordering::Relaxed) {
            let now_ns = monotonic_now_ns();
            self.last_empty_ns.store(now_ns, Ordering::Relaxed);
            self.empty_observations.fetch_add(1, Ordering::Release);
        }
    }
}

/// Futex-backed event used only after a worker has left the hot spin path.
///
/// A receiver snapshots `cookie`, registers itself as a waiter, and executes a
/// second `try_recv()` before sleeping. The paired sequentially-consistent
/// fences guarantee that either the receiver observes a concurrently enqueued
/// job on that recheck or the sender observes the registered waiter and wakes
/// it. This avoids the lost-wakeup window without a mutex.
#[derive(Default)]
struct WakeEvent {
    cookie: AtomicU32,
    waiters: AtomicUsize,
    sleeping_waiters: AtomicUsize,
    stats: WaitStats,
}

impl WakeEvent {
    fn register_waiter(&self) -> WakeWaiter<'_> {
        let cookie = self.cookie.load(Ordering::Relaxed);
        self.waiters.fetch_add(1, Ordering::Relaxed);
        fence(Ordering::SeqCst);
        WakeWaiter {
            event: self,
            cookie,
        }
    }

    fn wake_one(&self) {
        fence(Ordering::SeqCst);

        if self.waiters.load(Ordering::Relaxed) != 0 {
            self.cookie.fetch_add(1, Ordering::Relaxed);
            atomic_wait::wake_one(&self.cookie);
        }
    }

    #[cold]
    #[inline(never)]
    fn wake_all(&self) {
        fence(Ordering::SeqCst);

        if self.waiters.load(Ordering::Relaxed) != 0 {
            self.cookie.fetch_add(1, Ordering::Relaxed);
            atomic_wait::wake_all(&self.cookie);
        }
    }

    fn snapshot(&self) -> WaitStatsSnapshot {
        WaitStatsSnapshot {
            empty_observations: self.stats.empty_observations.load(Ordering::Acquire),
            last_empty_ns: self.stats.last_empty_ns.load(Ordering::Relaxed),
            sleeping_waiters: self.sleeping_waiters.load(Ordering::Relaxed),
        }
    }
}

struct WakeWaiter<'a> {
    event: &'a WakeEvent,
    cookie: u32,
}

impl WakeWaiter<'_> {
    #[cold]
    #[inline(never)]
    fn wait(self) {
        if self.event.cookie.load(Ordering::Relaxed) != self.cookie {
            return;
        }

        self.event.sleeping_waiters.fetch_add(1, Ordering::Relaxed);
        atomic_wait::wait(&self.event.cookie, self.cookie);
        self.event.sleeping_waiters.fetch_sub(1, Ordering::Relaxed);
    }
}

impl Drop for WakeWaiter<'_> {
    fn drop(&mut self) {
        self.event.waiters.fetch_sub(1, Ordering::Relaxed);
    }
}

struct Shared {
    wake_event: WakeEvent,
}

struct JobSender {
    inner: Option<crossbeam_channel::Sender<Job>>,
    shared: Arc<Shared>,
}

impl JobSender {
    fn send(&self, value: Job) -> Result<(), SendError<Job>> {
        self.inner
            .as_ref()
            .expect("job sender must be connected")
            .send(value)?;
        self.shared.wake_event.wake_one();
        Ok(())
    }
}

impl Drop for JobSender {
    fn drop(&mut self) {
        drop(self.inner.take());
        self.shared.wake_event.wake_all();
    }
}

struct JobReceiver {
    inner: crossbeam_channel::Receiver<Job>,
    shared: Arc<Shared>,
}

impl Clone for JobReceiver {
    fn clone(&self) -> Self {
        Self {
            inner: self.inner.clone(),
            shared: Arc::clone(&self.shared),
        }
    }
}

impl JobReceiver {
    fn recv_crossbeam(&self) -> Result<Job, RecvError> {
        self.inner.recv()
    }

    fn recv_spin_then_park(&self, spin_duration: Duration) -> Result<Job, RecvError> {
        let deadline = Instant::now()
            .checked_add(spin_duration)
            .expect("worker spin duration is too large");
        let mut recorded_empty = false;

        loop {
            match self.inner.try_recv() {
                Ok(job) => return Ok(job),
                Err(TryRecvError::Disconnected) => return Err(RecvError),
                Err(TryRecvError::Empty) => {
                    if !recorded_empty {
                        self.shared.wake_event.stats.record_empty();
                        recorded_empty = true;
                    }

                    if spin_duration.is_zero() || Instant::now() >= deadline {
                        return self.recv_parked();
                    }

                    std::hint::spin_loop();
                }
            }
        }
    }

    /// Mirrors Agave wake-channel's receive policy. Agave uses
    /// `crossbeam_utils::Backoff` 0.8.23: steps 0..=6 issue exponentially
    /// increasing spin hints, steps 7..=10 call `yield_now()`, and the next
    /// empty observation moves to the futex-backed waiter protocol.
    fn recv_agave_backoff(&self) -> Result<Job, RecvError> {
        loop {
            let mut step = 0u32;

            loop {
                match self.inner.try_recv() {
                    Ok(job) => return Ok(job),
                    Err(TryRecvError::Disconnected) => return Err(RecvError),
                    Err(TryRecvError::Empty) if step > AGAVE_BACKOFF_YIELD_LIMIT => break,
                    Err(TryRecvError::Empty) => {
                        if step <= AGAVE_BACKOFF_SPIN_LIMIT {
                            for _ in 0..(1u32 << step) {
                                std::hint::spin_loop();
                            }
                        } else {
                            std::thread::yield_now();
                        }

                        if step <= AGAVE_BACKOFF_YIELD_LIMIT {
                            step += 1;
                        }
                    }
                }
            }

            let waiter = self.shared.wake_event.register_waiter();
            match self.inner.try_recv() {
                Ok(job) => return Ok(job),
                Err(TryRecvError::Disconnected) => return Err(RecvError),
                Err(TryRecvError::Empty) => waiter.wait(),
            }
        }
    }

    /// Cold path: once a worker gives up spinning, keep it cold until it
    /// actually acquires a job. Spurious or stolen wakes re-park immediately
    /// instead of starting a fresh spin interval.
    #[cold]
    #[inline(never)]
    fn recv_parked(&self) -> Result<Job, RecvError> {
        loop {
            let waiter = self.shared.wake_event.register_waiter();
            match self.inner.try_recv() {
                Ok(job) => return Ok(job),
                Err(TryRecvError::Disconnected) => return Err(RecvError),
                Err(TryRecvError::Empty) => waiter.wait(),
            }
        }
    }
}

fn job_channel(capacity: usize) -> (JobSender, JobReceiver) {
    let (sender, receiver) = bounded(capacity);
    let shared = Arc::new(Shared {
        wake_event: WakeEvent::default(),
    });

    (
        JobSender {
            inner: Some(sender),
            shared: Arc::clone(&shared),
        },
        JobReceiver {
            inner: receiver,
            shared,
        },
    )
}

/// Fixed-size thread pool backed by a bounded shared work queue.
pub struct ThreadPool {
    job_sender: Option<JobSender>,
    shared: Arc<Shared>,
    worker_handles: Vec<JoinHandle<()>>,
}

impl ThreadPool {
    /// Creates a thread pool whose workers use Crossbeam's native blocking
    /// receive path when the queue is empty.
    pub fn new(num_workers: NonZeroUsize, queue_capacity: NonZeroUsize) -> io::Result<Self> {
        Self::with_wait_strategy(num_workers, queue_capacity, WaitStrategy::Crossbeam)
    }

    /// Creates a thread pool with an explicitly configured worker wait
    /// strategy.
    pub fn with_wait_strategy(
        num_workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
        wait_strategy: WaitStrategy,
    ) -> io::Result<Self> {
        let (job_sender, job_receiver) = job_channel(queue_capacity.get());
        let shared = Arc::clone(&job_sender.shared);

        let mut worker_handles: Vec<JoinHandle<()>> = Vec::with_capacity(num_workers.get());

        for index in 0..num_workers.get() {
            let job_receiver = job_receiver.clone();

            let handle = match Builder::new()
                .name(format!("threadance-{index:02}"))
                .spawn(move || worker_loop(job_receiver, wait_strategy))
            {
                Ok(handle) => handle,
                Err(error) => {
                    drop(job_sender);

                    for handle in worker_handles {
                        let _ = handle.join();
                    }

                    return Err(error);
                }
            };

            worker_handles.push(handle);
        }

        Ok(Self {
            job_sender: Some(job_sender),
            shared,
            worker_handles,
        })
    }

    /// Enables or disables lightweight wait-path instrumentation.
    pub fn set_wait_instrumentation(&self, enabled: bool) {
        self.shared
            .wake_event
            .stats
            .enabled
            .store(enabled, Ordering::Relaxed);
    }

    pub fn reset_wait_stats(&self) {
        self.shared.wake_event.stats.reset();
    }

    pub fn wait_stats(&self) -> WaitStatsSnapshot {
        self.shared.wake_event.snapshot()
    }

    /// Submits one job to the pool.
    ///
    /// This call blocks when the bounded job queue is full until space becomes
    /// available.
    pub fn execute<F>(&self, job: F) -> Result<(), ExecuteError>
    where
        F: FnOnce() + Send + 'static,
    {
        let sender = self.job_sender.as_ref().ok_or(ExecuteError::Disconnected)?;

        sender
            .send(Box::new(job))
            .map_err(|_| ExecuteError::Disconnected)
    }
}

fn worker_loop(receiver: JobReceiver, wait_strategy: WaitStrategy) {
    while let Some(job) = receive_job(&receiver, wait_strategy) {
        job();
    }
}

fn receive_job(receiver: &JobReceiver, wait_strategy: WaitStrategy) -> Option<Job> {
    match wait_strategy {
        WaitStrategy::Crossbeam => receiver.recv_crossbeam().ok(),
        WaitStrategy::SpinThenPark(spin_duration) => {
            receiver.recv_spin_then_park(spin_duration).ok()
        }
        WaitStrategy::AgaveBackoff => receiver.recv_agave_backoff().ok(),
    }
}

impl Drop for ThreadPool {
    fn drop(&mut self) {
        // Disconnect the job channel first, then wake every parked worker so
        // each receiver can observe the disconnected queue before we join it.
        drop(self.job_sender.take());

        for handle in self.worker_handles.drain(..) {
            let _ = handle.join();
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecuteError {
    Disconnected,
}

impl fmt::Display for ExecuteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Disconnected => f.write_str("thread pool is disconnected"),
        }
    }
}

impl std::error::Error for ExecuteError {}

#[cfg(test)]
mod tests {
    use std::{
        num::NonZeroUsize,
        sync::{
            Arc,
            atomic::{AtomicUsize, Ordering},
        },
        thread,
        time::{Duration, Instant},
    };

    use super::{ThreadPool, WaitStrategy, WakeEvent};

    fn assert_executes(wait_strategy: WaitStrategy) {
        let pool = ThreadPool::with_wait_strategy(
            NonZeroUsize::new(4).unwrap(),
            NonZeroUsize::new(16).unwrap(),
            wait_strategy,
        )
        .unwrap();

        let counter = Arc::new(AtomicUsize::new(0));

        for _ in 0..100 {
            let counter = counter.clone();

            pool.execute(move || {
                counter.fetch_add(1, Ordering::Relaxed);
            })
            .unwrap();
        }

        drop(pool);

        assert_eq!(counter.load(Ordering::Relaxed), 100);
    }

    #[test]
    fn executes_submitted_jobs_with_crossbeam_waker() {
        assert_executes(WaitStrategy::Crossbeam);
    }

    #[test]
    fn spin_then_park_executes_submitted_jobs() {
        assert_executes(WaitStrategy::SpinThenPark(Duration::from_micros(50)));
    }

    #[test]
    fn agave_backoff_executes_submitted_jobs() {
        assert_executes(WaitStrategy::AgaveBackoff);
    }

    #[test]
    fn immediate_park_executes_submitted_jobs() {
        assert_executes(WaitStrategy::SpinThenPark(Duration::ZERO));
    }

    #[test]
    fn wake_before_wait_does_not_block() {
        let event = WakeEvent::default();
        let waiter = event.register_waiter();
        event.wake_one();
        waiter.wait();
    }

    #[test]
    fn parked_worker_is_woken_for_new_work() {
        let pool = ThreadPool::with_wait_strategy(
            NonZeroUsize::new(1).unwrap(),
            NonZeroUsize::new(4).unwrap(),
            WaitStrategy::SpinThenPark(Duration::ZERO),
        )
        .unwrap();
        pool.set_wait_instrumentation(true);
        pool.reset_wait_stats();

        let deadline = Instant::now() + Duration::from_secs(1);
        while pool.wait_stats().sleeping_waiters == 0 {
            assert!(Instant::now() < deadline, "worker never entered sleep path");
            thread::yield_now();
        }

        let counter = Arc::new(AtomicUsize::new(0));
        let counter_clone = Arc::clone(&counter);
        pool.execute(move || {
            counter_clone.fetch_add(1, Ordering::Relaxed);
        })
        .unwrap();

        let deadline = Instant::now() + Duration::from_secs(1);
        while counter.load(Ordering::Relaxed) == 0 {
            assert!(Instant::now() < deadline, "sleeping worker was not woken");
            thread::yield_now();
        }
    }
}
