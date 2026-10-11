use std::{
    fmt, io,
    num::NonZeroUsize,
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    },
    thread::{Builder, JoinHandle},
    time::{Duration, Instant},
};

use crossbeam_channel::{RecvError, SendError, TryRecvError, bounded};

type Job = Box<dyn FnOnce() + Send + 'static>;

/// Strategy used by idle workers while waiting for more work.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaitStrategy {
    /// Use Crossbeam's native blocking receive path.
    ///
    /// This preserves the original Threadance behaviour and is useful as a
    /// baseline when comparing wake implementations.
    Block,

    /// Legacy strategy used by the first fixed-wait experiments.
    ///
    /// The worker actively polls for the configured duration and then falls
    /// back to Crossbeam's native blocking `recv()`. This intentionally keeps
    /// the old semantics so existing experiment results remain reproducible.
    SpinThenBlock(Duration),

    /// Use Threadance's wake channel.
    ///
    /// The worker actively polls for exactly the configured duration and then
    /// registers with the Threadance wake event, rechecks the queue, and parks
    /// if the queue is still empty. Crossbeam is used only as the bounded job
    /// queue; its blocking receive path is not used.
    SpinThenPark(Duration),
}

/// Snapshot of optional Threadance wake-path instrumentation.
///
/// Instrumentation is disabled by default. It is intended for the small
/// wake-characterization experiment, not for production benchmark runs.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct WaitStatsSnapshot {
    pub empty_observations: u64,
    pub parks: u64,
    pub wake_one_calls: u64,
    pub sleeping_waiters: usize,
}

#[derive(Default)]
struct WaitStats {
    enabled: AtomicBool,
    empty_observations: AtomicU64,
    parks: AtomicU64,
    wake_one_calls: AtomicU64,
}

impl WaitStats {
    fn reset(&self) {
        self.empty_observations.store(0, Ordering::Relaxed);
        self.parks.store(0, Ordering::Relaxed);
        self.wake_one_calls.store(0, Ordering::Relaxed);
    }

    fn record_empty(&self) {
        if self.enabled.load(Ordering::Relaxed) {
            self.empty_observations.fetch_add(1, Ordering::Relaxed);
        }
    }

    fn record_park(&self) {
        if self.enabled.load(Ordering::Relaxed) {
            self.parks.fetch_add(1, Ordering::Relaxed);
        }
    }

    fn record_wake_one(&self) {
        if self.enabled.load(Ordering::Relaxed) {
            self.wake_one_calls.fetch_add(1, Ordering::Relaxed);
        }
    }
}

/// Wake event used by the Threadance receive path.
///
/// `generation` changes whenever a sender publishes a wake. A waiter records
/// the current generation, the receiver rechecks the queue, and only then
/// waits for the generation to change. The mutex/condvar pair makes the
/// transition into sleep atomic with respect to a sender notification, so a
/// job cannot be stranded by a lost wakeup.
#[derive(Default)]
struct WakeEvent {
    generation: Mutex<u64>,
    condvar: Condvar,
    waiters: AtomicUsize,
    sleeping_waiters: AtomicUsize,
    stats: WaitStats,
}

impl WakeEvent {
    fn register_waiter(&self) -> WakeWaiter<'_> {
        let generation = *self.generation.lock().unwrap();
        self.waiters.fetch_add(1, Ordering::Relaxed);
        WakeWaiter {
            event: self,
            generation,
        }
    }

    fn wake_one(&self) {
        self.stats.record_wake_one();

        if self.waiters.load(Ordering::Relaxed) == 0 {
            return;
        }

        let mut generation = self.generation.lock().unwrap();
        *generation = generation.wrapping_add(1);
        self.condvar.notify_one();
    }

    fn wake_all(&self) {
        let mut generation = self.generation.lock().unwrap();
        *generation = generation.wrapping_add(1);
        self.condvar.notify_all();
    }

    fn snapshot(&self) -> WaitStatsSnapshot {
        WaitStatsSnapshot {
            empty_observations: self.stats.empty_observations.load(Ordering::Relaxed),
            parks: self.stats.parks.load(Ordering::Relaxed),
            wake_one_calls: self.stats.wake_one_calls.load(Ordering::Relaxed),
            sleeping_waiters: self.sleeping_waiters.load(Ordering::Relaxed),
        }
    }
}

struct WakeWaiter<'a> {
    event: &'a WakeEvent,
    generation: u64,
}

impl WakeWaiter<'_> {
    fn wait(self) {
        self.event.stats.record_park();

        let mut generation = self.event.generation.lock().unwrap();
        while *generation == self.generation {
            // Increment while holding the same mutex used by wake_one(). Once
            // Condvar::wait releases the mutex, a sender can acquire it and
            // notify us without a lost-wakeup window.
            self.event.sleeping_waiters.fetch_add(1, Ordering::Relaxed);
            generation = self.event.condvar.wait(generation).unwrap();
            self.event.sleeping_waiters.fetch_sub(1, Ordering::Relaxed);
        }
    }
}

impl Drop for WakeWaiter<'_> {
    fn drop(&mut self) {
        self.event.waiters.fetch_sub(1, Ordering::Relaxed);
    }
}

struct Shared {
    wake_event: WakeEvent,
    num_senders: AtomicUsize,
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

impl Clone for JobSender {
    fn clone(&self) -> Self {
        self.shared.num_senders.fetch_add(1, Ordering::Relaxed);
        Self {
            inner: self.inner.clone(),
            shared: Arc::clone(&self.shared),
        }
    }
}

impl Drop for JobSender {
    fn drop(&mut self) {
        drop(self.inner.take());
        if self.shared.num_senders.fetch_sub(1, Ordering::AcqRel) == 1 {
            self.shared.wake_event.wake_all();
        }
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

    fn recv_spin_then_block(&self, spin_duration: Duration) -> Result<Job, RecvError> {
        if spin_duration.is_zero() {
            return self.inner.recv();
        }

        let deadline = Instant::now()
            .checked_add(spin_duration)
            .expect("worker spin duration is too large");

        loop {
            match self.inner.try_recv() {
                Ok(job) => return Ok(job),
                Err(TryRecvError::Disconnected) => return Err(RecvError),
                Err(TryRecvError::Empty) => {
                    if Instant::now() >= deadline {
                        return self.inner.recv();
                    }
                    std::hint::spin_loop();
                }
            }
        }
    }

    fn recv_spin_then_park(&self, spin_duration: Duration) -> Result<Job, RecvError> {
        loop {
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
                            break;
                        }

                        std::hint::spin_loop();
                    }
                }
            }

            // A sender may publish a job after the last Empty above but before
            // we register as a waiter. Register first, then recheck the queue.
            // If the job arrived in that race window, this second try_recv()
            // observes it. Otherwise the sender observes a registered waiter
            // and wakes us after publishing the job.
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
        num_senders: AtomicUsize::new(1),
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
    // The sender is optional so Drop can disconnect the channel before
    // joining worker threads.
    job_sender: Option<JobSender>,
    shared: Arc<Shared>,
    worker_handles: Vec<JoinHandle<()>>,
}

impl ThreadPool {
    /// Creates a thread pool whose workers use Crossbeam's native blocking
    /// receive path when the queue is empty.
    pub fn new(num_workers: NonZeroUsize, queue_capacity: NonZeroUsize) -> io::Result<Self> {
        Self::with_wait_strategy(num_workers, queue_capacity, WaitStrategy::Block)
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

    /// Enables or disables lightweight wake-path instrumentation.
    ///
    /// Leave this disabled for normal benchmark runs. The characterization
    /// utility enables it to observe when workers first see an empty queue and
    /// when they actually enter the sleep path.
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
        WaitStrategy::Block => receiver.recv_crossbeam().ok(),
        WaitStrategy::SpinThenBlock(spin_duration) => {
            receiver.recv_spin_then_block(spin_duration).ok()
        }
        WaitStrategy::SpinThenPark(spin_duration) => {
            receiver.recv_spin_then_park(spin_duration).ok()
        }
    }
}

impl Drop for ThreadPool {
    fn drop(&mut self) {
        // Disconnect the job channel first. The custom sender wakes all parked
        // workers when the final sender is dropped, so they can observe the
        // disconnected queue and exit before we join them.
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

    use super::{ThreadPool, WaitStrategy};

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
    fn executes_submitted_jobs_with_crossbeam_blocking() {
        assert_executes(WaitStrategy::Block);
    }

    #[test]
    fn legacy_spin_then_block_executes_submitted_jobs() {
        assert_executes(WaitStrategy::SpinThenBlock(Duration::from_micros(50)));
    }

    #[test]
    fn spin_then_park_executes_submitted_jobs() {
        assert_executes(WaitStrategy::SpinThenPark(Duration::from_micros(50)));
    }

    #[test]
    fn immediate_park_executes_submitted_jobs() {
        assert_executes(WaitStrategy::SpinThenPark(Duration::ZERO));
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
