//! Experimental hot-first scheduling on fixed-capacity per-worker SPSC rings.
//! One contiguous slot allocation, one producer mutex, one consumer per ring.
//! backlog = 0 means one in-flight task, and no queued task, per worker.

use crate::{
    delivery::{DeliverySchedule, run_with_delivery},
    result::RunResult,
    schedulers::Scheduler,
    workload::Workload,
};
use std::{
    cell::{Cell, UnsafeCell},
    io,
    mem::MaybeUninit,
    num::NonZeroUsize,
    sync::{
        Arc, Condvar, Mutex, OnceLock,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread::{self, Thread},
    time::{Duration, Instant},
};
use threadance::{
    QueuePop, QueueTopology, Task, TaskConsumer, TaskProducer, ThreadPool, WaitResult, WakePolicy,
};

const WORD_BITS: usize = usize::BITS as usize;

#[derive(Clone, Copy)]
pub enum WaitMode {
    SpinThenPark(Duration),
    AgaveBackoff,
}

#[repr(align(64))]
struct Worker {
    head: AtomicUsize,
    tail: AtomicUsize,
    outstanding: AtomicUsize,
    thread: OnceLock<Thread>,
}
impl Worker {
    fn new() -> Self {
        Self {
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
            outstanding: AtomicUsize::new(0),
            thread: OnceLock::new(),
        }
    }
}
struct Writer {
    tails: Vec<usize>,
}
struct Backpressure {
    writer: Mutex<Writer>,
    available: Condvar,
    blocked: AtomicUsize,
}
// SAFETY: a single producer at a time writes a slot; one consumer owns each
// ring; publishing tail (Release) makes its initialized slot visible (Acquire).
// A slot cannot be reused until the task executed there has completed.
struct Slots(Box<[UnsafeCell<MaybeUninit<Task>>]>);
unsafe impl Sync for Slots {}

struct Shared {
    width: usize,
    slots: Slots,
    workers: Vec<Arc<Worker>>,
    backpressure: Arc<Backpressure>,
    pending: Vec<AtomicUsize>,
    closed: AtomicBool,
    shutdown: AtomicBool,
}
impl Shared {
    fn slot(&self, worker: usize, seq: usize) -> &UnsafeCell<MaybeUninit<Task>> {
        &self.slots.0[worker * self.width + seq % self.width]
    }
    fn signal(&self, worker: usize) {
        self.pending[worker / WORD_BITS].fetch_or(1 << (worker % WORD_BITS), Ordering::Release);
    }
}
impl Drop for Shared {
    fn drop(&mut self) {
        // No consumer or producer exists now. Drain initialized, unclaimed
        // slots if a poisoned pool stopped early.
        for (id, worker) in self.workers.iter().enumerate() {
            let mut head = worker.head.load(Ordering::Relaxed);
            let tail = worker.tail.load(Ordering::Relaxed);
            while head != tail {
                // SAFETY: this task was published but not claimed, and no
                // concurrent access to the allocation remains.
                unsafe {
                    (*self.slot(id, head).get()).assume_init_drop();
                }
                head += 1;
            }
        }
    }
}
struct Completion {
    worker: Arc<Worker>,
    backpressure: Arc<Backpressure>,
}
impl Drop for Completion {
    fn drop(&mut self) {
        self.worker.outstanding.fetch_sub(1, Ordering::SeqCst);
        if self.backpressure.blocked.load(Ordering::SeqCst) != 0 {
            // Holding writer while notifying closes the condvar missed-wake
            // race with a producer entering wait().
            let _lock = self
                .backpressure
                .writer
                .lock()
                .unwrap_or_else(|err| err.into_inner());
            self.backpressure.available.notify_one();
        }
    }
}

pub struct HotProducer {
    shared: Arc<Shared>,
}
impl TaskProducer for HotProducer {
    fn push(&self, task: Task) -> Result<(), Task> {
        let mut task = Some(task);
        loop {
            let bp = &self.shared.backpressure;
            let mut writer = bp.writer.lock().unwrap_or_else(|err| err.into_inner());
            if let Some(id) = self
                .shared
                .workers
                .iter()
                .position(|w| w.outstanding.load(Ordering::SeqCst) < self.shared.width)
            {
                let worker = Arc::clone(&self.shared.workers[id]);
                let backpressure = Arc::clone(bp);
                let before = worker.outstanding.fetch_add(1, Ordering::SeqCst);
                debug_assert!(before < self.shared.width);

                let original = task.take().expect("submit exactly once");
                let guarded: Task = Box::new(move || {
                    let _completion = Completion {
                        worker,
                        backpressure,
                    };
                    original();
                });
                let seq = writer.tails[id];
                // SAFETY: producer mutex serializes writers. The outstanding
                // limit prevents overwriting an unconsumed or running task.
                unsafe {
                    (*self.shared.slot(id, seq).get()).write(guarded);
                }
                writer.tails[id] = seq + 1;
                self.shared.workers[id]
                    .tail
                    .store(seq + 1, Ordering::Release);
                if before == 0 {
                    self.shared.signal(id);
                }
                return Ok(());
            }
            // Every worker is at its in-flight limit. Tasks must not be
            // discarded. Wait without holding the producer mutex.
            bp.blocked.fetch_add(1, Ordering::SeqCst);
            let available = self
                .shared
                .workers
                .iter()
                .any(|w| w.outstanding.load(Ordering::SeqCst) < self.shared.width);
            if !available {
                writer = bp
                    .available
                    .wait(writer)
                    .unwrap_or_else(|err| err.into_inner());
            }
            bp.blocked.fetch_sub(1, Ordering::SeqCst);
            drop(writer);
        }
    }
}
impl Drop for HotProducer {
    fn drop(&mut self) {
        self.shared.closed.store(true, Ordering::Release);
    }
}
pub struct HotConsumer {
    shared: Arc<Shared>,
    id: usize,
    read: Cell<usize>,
}
impl TaskConsumer for HotConsumer {
    #[inline]
    fn try_pop(&self) -> QueuePop {
        let seq = self.read.get();
        let worker = &self.shared.workers[self.id];
        if seq == worker.tail.load(Ordering::Acquire) {
            return if self.shared.closed.load(Ordering::Acquire) {
                QueuePop::Disconnected
            } else {
                QueuePop::Empty
            };
        }
        // SAFETY: one consumer owns this ring; Acquire on tail makes the
        // published slot visible. Completion guards delay slot reuse.
        let task = unsafe { (*self.shared.slot(self.id, seq).get()).assume_init_read() };
        self.read.set(seq + 1);
        worker.head.store(seq + 1, Ordering::Release);
        QueuePop::Item(task)
    }
}
pub struct HotFirstQueue {
    state: Arc<OnceLock<Arc<Shared>>>,
    backlog: usize,
}
impl QueueTopology for HotFirstQueue {
    type Producer = HotProducer;
    type Consumer = HotConsumer;

    fn build(
        &self,
        workers: NonZeroUsize,
        capacity: NonZeroUsize,
    ) -> (Self::Producer, Vec<Self::Consumer>) {
        let width = self.backlog + 1;
        let total = width * workers.get();
        assert!(total <= capacity.get(), "hot-first exceeds global capacity");
        let slots = (0..total)
            .map(|_| UnsafeCell::new(MaybeUninit::uninit()))
            .collect::<Vec<_>>()
            .into_boxed_slice();
        let bp = Arc::new(Backpressure {
            writer: Mutex::new(Writer {
                tails: vec![0; workers.get()],
            }),
            available: Condvar::new(),
            blocked: AtomicUsize::new(0),
        });
        let shared = Arc::new(Shared {
            width,
            slots: Slots(slots),
            backpressure: bp,
            workers: (0..workers.get())
                .map(|_| Arc::new(Worker::new()))
                .collect(),
            pending: (0..workers.get().div_ceil(WORD_BITS))
                .map(|_| AtomicUsize::new(0))
                .collect(),
            closed: AtomicBool::new(false),
            shutdown: AtomicBool::new(false),
        });
        assert!(
            self.state.set(Arc::clone(&shared)).is_ok(),
            "a topology instance may only be built once"
        );
        let consumers = (0..workers.get())
            .map(|id| HotConsumer {
                shared: Arc::clone(&shared),
                id,
                read: Cell::new(0),
            })
            .collect();
        (HotProducer { shared }, consumers)
    }
}
pub struct HotFirstWait {
    state: Arc<OnceLock<Arc<Shared>>>,
    mode: WaitMode,
}
fn probe(consumer: &HotConsumer) -> Option<WaitResult> {
    match consumer.try_pop() {
        QueuePop::Item(task) => Some(WaitResult::Task(task)),
        QueuePop::Disconnected => Some(WaitResult::Disconnected),
        QueuePop::Empty => None,
    }
}
impl WakePolicy<HotConsumer> for HotFirstWait {
    fn wait(&self, consumer: &HotConsumer) -> WaitResult {
        let shared = self.state.get().expect("queue must be built");
        shared.workers[consumer.id]
            .thread
            .get_or_init(thread::current);
        if shared.shutdown.load(Ordering::Acquire) {
            return WaitResult::Interrupted;
        }
        if let Some(task) = probe(consumer) {
            return task;
        }
        match self.mode {
            WaitMode::SpinThenPark(duration) => {
                let start = Instant::now();
                while start.elapsed() < duration {
                    if shared.shutdown.load(Ordering::Acquire) {
                        return WaitResult::Interrupted;
                    }
                    std::hint::spin_loop();
                    if let Some(task) = probe(consumer) {
                        return task;
                    }
                }
            }
            WaitMode::AgaveBackoff => {
                for step in 0..=10u32 {
                    if shared.shutdown.load(Ordering::Acquire) {
                        return WaitResult::Interrupted;
                    }
                    if step <= 6 {
                        for _ in 0..(1u32 << step) {
                            std::hint::spin_loop();
                        }
                    } else {
                        thread::yield_now();
                    }
                    if let Some(task) = probe(consumer) {
                        return task;
                    }
                }
            }
        }
        loop {
            if shared.shutdown.load(Ordering::Acquire) {
                return WaitResult::Interrupted;
            }
            if let Some(task) = probe(consumer) {
                return task;
            }
            // unpark stores a token even if the worker has not parked yet.
            thread::park();
        }
    }
    fn wake_one(&self) {
        let shared = self.state.get().expect("queue must be built");
        for (word, pending) in shared.pending.iter().enumerate() {
            if pending.load(Ordering::Acquire) == 0 {
                continue;
            }
            let mut bits = pending.swap(0, Ordering::AcqRel);
            while bits != 0 {
                let id = word * WORD_BITS + bits.trailing_zeros() as usize;
                if let Some(worker) = shared.workers.get(id)
                    && let Some(handle) = worker.thread.get()
                {
                    handle.unpark();
                }
                bits &= bits - 1;
            }
        }
    }
    fn wake_all(&self) {
        let shared = self.state.get().expect("queue must be built");
        shared.shutdown.store(true, Ordering::Release);
        for worker in &shared.workers {
            if let Some(handle) = worker.thread.get() {
                handle.unpark();
            }
        }
    }
}

pub struct HotFirstScheduler {
    pool: ThreadPool<HotFirstQueue, HotFirstWait>,
}
impl HotFirstScheduler {
    pub fn new(
        workers: NonZeroUsize,
        capacity: NonZeroUsize,
        backlog: usize,
        mode: WaitMode,
    ) -> io::Result<Self> {
        let total = backlog
            .checked_add(1)
            .and_then(|width| width.checked_mul(workers.get()));
        if total.is_none_or(|total| total > capacity.get()) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "hot-first needs workers * (backlog + 1) <= queue_capacity",
            ));
        }
        let state = Arc::new(OnceLock::new());
        let queue = HotFirstQueue {
            state: Arc::clone(&state),
            backlog,
        };
        let wait = HotFirstWait { state, mode };
        let pool = ThreadPool::builder(workers, capacity)
            .queue(queue)
            .wake_policy(wait)
            .build()?;
        Ok(Self { pool })
    }
}
impl Scheduler for HotFirstScheduler {
    fn run<W: Workload>(&self, workload: &Arc<W>, delivery: &DeliverySchedule) -> RunResult {
        run_with_delivery(workload, delivery, |workload, unit, completion| {
            self.pool
                .execute(move || {
                    workload.execute(&unit);
                    completion.complete_one();
                })
                .expect("hot-first worker threads must be alive");
        })
    }
}

#[cfg(test)]
mod tests {
    use super::{HotFirstScheduler, WaitMode};
    use std::{
        num::NonZeroUsize,
        sync::{
            Arc,
            atomic::{AtomicUsize, Ordering},
            mpsc,
        },
        thread,
        time::Duration,
    };

    fn pool(backlog: usize, workers: usize) -> HotFirstScheduler {
        HotFirstScheduler::new(
            NonZeroUsize::new(workers).unwrap(),
            NonZeroUsize::new(workers * (backlog + 1)).unwrap(),
            backlog,
            WaitMode::SpinThenPark(Duration::ZERO),
        )
        .unwrap()
    }

    #[test]
    fn zero_backlog_routes_to_next_worker_while_hot_worker_is_busy() {
        let pool = pool(0, 2);
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        pool.pool
            .execute(move || {
                started_tx.send(()).unwrap();
                release_rx.recv().unwrap();
            })
            .unwrap();
        started_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let (done_tx, done_rx) = mpsc::channel();
        pool.pool
            .execute(move || {
                done_tx
                    .send(thread::current().name().unwrap().to_owned())
                    .unwrap();
            })
            .unwrap();
        assert_eq!(
            done_rx.recv_timeout(Duration::from_secs(2)).unwrap(),
            "threadance-01"
        );
        release_tx.send(()).unwrap();
    }

    #[test]
    fn backlog_one_keeps_next_job_at_hot_worker() {
        let pool = pool(1, 2);
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let (done_tx, done_rx) = mpsc::channel();
        pool.pool
            .execute(move || {
                started_tx.send(()).unwrap();
                release_rx.recv().unwrap();
            })
            .unwrap();
        started_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let tx = done_tx.clone();
        pool.pool
            .execute(move || {
                tx.send(thread::current().name().unwrap().to_owned())
                    .unwrap();
            })
            .unwrap();
        pool.pool
            .execute(move || {
                done_tx
                    .send(thread::current().name().unwrap().to_owned())
                    .unwrap();
            })
            .unwrap();
        assert_eq!(
            done_rx.recv_timeout(Duration::from_secs(2)).unwrap(),
            "threadance-01"
        );
        release_tx.send(()).unwrap();
        assert_eq!(
            done_rx.recv_timeout(Duration::from_secs(2)).unwrap(),
            "threadance-00"
        );
    }

    #[test]
    fn concurrent_producers_wrap_and_run_exactly_once() {
        let pool = Arc::new(pool(2, 4));
        let counts = Arc::new((0..2048).map(|_| AtomicUsize::new(0)).collect::<Vec<_>>());
        let mut producers = Vec::new();
        for p in 0..8 {
            let pool = Arc::clone(&pool);
            let counts = Arc::clone(&counts);
            producers.push(thread::spawn(move || {
                for i in (p..2048).step_by(8) {
                    let counts = Arc::clone(&counts);
                    pool.pool
                        .execute(move || {
                            counts[i].fetch_add(1, Ordering::SeqCst);
                        })
                        .unwrap();
                }
            }));
        }
        for producer in producers {
            producer.join().unwrap();
        }
        drop(pool);
        assert!(counts.iter().all(|x| x.load(Ordering::SeqCst) == 1));
    }

    #[test]
    fn repeated_park_and_unpark_has_no_lost_wake() {
        let pool = pool(0, 2);
        let (tx, rx) = mpsc::channel();
        for index in 0..256 {
            let tx = tx.clone();
            pool.pool
                .execute(move || {
                    tx.send(index).unwrap();
                })
                .unwrap();
            assert_eq!(rx.recv_timeout(Duration::from_secs(2)).unwrap(), index);
            thread::yield_now();
        }
    }

    #[test]
    fn rejects_insufficient_capacity() {
        assert!(
            HotFirstScheduler::new(
                NonZeroUsize::new(4).unwrap(),
                NonZeroUsize::new(7).unwrap(),
                1,
                WaitMode::AgaveBackoff,
            )
            .is_err()
        );
    }
}
