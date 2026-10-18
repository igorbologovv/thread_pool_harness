//! Hot-first gate ring: one allocation split into worker-affine SPSC segments.
//!
//! One producer writes sequentially within its selected segment. On crossing
//! the segment boundary (or finding its next slot occupied), it enters a gate:
//! probe W0 first, then W1, and so on. It does not scan workers for every task.
//! Each worker reads only its own segment and releases the slot on *pop*, not
//! when its task finishes. No producer mutex, per-task completion guard, or
//! wake bitmap is needed.
//!
//! B=0 is intentionally unsupported: zero queued tasks requires a genuine
//! direct-handoff protocol and must not be silently simulated with one slot.

use std::{
    cell::UnsafeCell,
    io,
    mem::MaybeUninit,
    num::NonZeroUsize,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering},
    },
};

use ::threadance::{
    QueuePop, SegmentedIdle, SegmentedProducer, SegmentedThreadPool, SegmentedTopology,
    SegmentedWake, Task, TaskConsumer,
};

use crate::{
    delivery::{DeliverySchedule, run_with_delivery},
    result::RunResult,
    schedulers::Scheduler,
    workload::Workload,
};

/// The task slots are one physical, contiguous allocation.
///
/// SAFETY: there is exactly one producer with mutable access to its endpoint;
/// each consumer owns a disjoint segment. Only the producer writes the next
/// unpublished slot, and only its owning worker reads it. Release on tail
/// publishes writes; Acquire on tail observes them. The producer reads head
/// before reusing a slot. Moving a task out on pop makes that slot free.
struct Slots(Box<[UnsafeCell<MaybeUninit<Task>>]>);

// SAFETY: all slot access follows the single-writer/single-reader protocol
// above. UnsafeCell never authorizes unsynchronized aliasing by itself.
unsafe impl Sync for Slots {}

#[repr(align(64))]
struct Cursor(AtomicUsize);

struct Segment {
    head: Cursor,
    tail: Cursor,
}

impl Segment {
    fn new() -> Self {
        Self {
            head: Cursor(AtomicUsize::new(0)),
            tail: Cursor(AtomicUsize::new(0)),
        }
    }
}

struct Shared {
    width: usize,
    slots: Slots,
    segments: Box<[Segment]>,
    closed: AtomicBool,
    // Slow-path producer backpressure. No lock or syscall on a successful push.
    producer_blocked: AtomicBool,
    producer_cookie: AtomicU32,
}

impl Shared {
    #[inline]
    fn slot(&self, worker: usize, sequence: usize) -> &UnsafeCell<MaybeUninit<Task>> {
        &self.slots.0[worker * self.width + sequence % self.width]
    }

    #[inline]
    fn has_room(&self, worker: usize, ordering: Ordering) -> bool {
        let segment = &self.segments[worker];
        let tail = segment.tail.0.load(Ordering::Relaxed);
        let head = segment.head.0.load(ordering);
        tail.wrapping_sub(head) < self.width
    }
}

impl Drop for Shared {
    fn drop(&mut self) {
        // This executes only after producer and all consumers have gone away.
        // Unclaimed jobs can remain after panic/poison; drop them exactly once.
        for (worker, segment) in self.segments.iter().enumerate() {
            let mut head = segment.head.0.load(Ordering::Relaxed);
            let tail = segment.tail.0.load(Ordering::Relaxed);
            while head != tail {
                // SAFETY: the task at this published position has not been
                // removed, and no producer/consumer survives Shared::drop.
                unsafe {
                    (*self.slot(worker, head).get()).assume_init_drop();
                }
                head = head.wrapping_add(1);
            }
        }
    }
}

pub struct GateProducer {
    shared: Arc<Shared>,
    active: usize,
    at_gate: bool,
}

impl GateProducer {
    #[inline]
    fn select_at_gate(&self, ordering: Ordering) -> Option<usize> {
        // Hot-first priority is evaluated ONLY at gates, not on every push.
        (0..self.shared.segments.len()).find(|&id| self.shared.has_room(id, ordering))
    }

    fn wait_for_capacity(&self) {
        let shared = &self.shared;
        // Take the snapshot first, before advertising intent to sleep.
        let cookie = shared.producer_cookie.load(Ordering::Relaxed);
        shared.producer_blocked.store(true, Ordering::SeqCst);

        // Recheck after registration. Paired SeqCst stores/loads on head and
        // producer_blocked rule out missing the last slot release.
        if self.select_at_gate(Ordering::SeqCst).is_none() {
            atomic_wait::wait(&shared.producer_cookie, cookie);
        }
        shared.producer_blocked.store(false, Ordering::SeqCst);
    }
}

impl SegmentedProducer for GateProducer {
    #[inline]
    fn push(&mut self, task: Task) -> Result<usize, Task> {
        loop {
            // Inside a segment we keep the pointer moving, without rescanning.
            // At the boundary, always give W0 first refusal.
            if self.at_gate || !self.shared.has_room(self.active, Ordering::Acquire) {
                match self.select_at_gate(Ordering::Acquire) {
                    Some(worker) => {
                        self.active = worker;
                        self.at_gate = false;
                    }
                    None => {
                        self.at_gate = true;
                        self.wait_for_capacity();
                        continue;
                    }
                }
            }

            let segment = &self.shared.segments[self.active];
            let tail = segment.tail.0.load(Ordering::Relaxed);

            // SAFETY: &mut self guarantees a unique active writer; has_room
            // proved this slot was released. No reader can observe it until
            // the Release store of tail below.
            unsafe {
                (*self.shared.slot(self.active, tail).get()).write(task);
            }
            let next = tail.wrapping_add(1);
            segment.tail.0.store(next, Ordering::Release);
            self.at_gate = next.is_multiple_of(self.shared.width);
            return Ok(self.active);
        }
    }
}

impl Drop for GateProducer {
    fn drop(&mut self) {
        self.shared.closed.store(true, Ordering::Release);
    }
}

pub struct GateConsumer {
    shared: Arc<Shared>,
    id: usize,
}

impl TaskConsumer for GateConsumer {
    #[inline]
    fn try_pop(&self) -> QueuePop {
        let segment = &self.shared.segments[self.id];
        let head = segment.head.0.load(Ordering::Relaxed);
        if head == segment.tail.0.load(Ordering::Acquire) {
            if !self.shared.closed.load(Ordering::Acquire) {
                return QueuePop::Empty;
            }
            // The Acquire of closed happens after the first tail load;
            // a second Acquire ensures all published work is visible.
            return if head == segment.tail.0.load(Ordering::Acquire) {
                QueuePop::Disconnected
            } else {
                self.try_pop()
            };
        }

        // SAFETY: only this consumer accesses this segment's head. Acquire
        // tail means the Task was initialized before it was published.
        let task = unsafe { (*self.shared.slot(self.id, head).get()).assume_init_read() };
        segment.head.0.store(head.wrapping_add(1), Ordering::SeqCst);

        // Wake a backpressured producer only on this slow path. Head's
        // SeqCst store and producer_blocked's SeqCst load pair with the
        // producer's store/recheck protocol (no lost full->available wake).
        if self.shared.producer_blocked.load(Ordering::SeqCst) {
            self.shared.producer_cookie.fetch_add(1, Ordering::Release);
            atomic_wait::wake_one(&self.shared.producer_cookie);
        }
        QueuePop::Item(task)
    }
}

pub struct GateTopology {
    backlog: NonZeroUsize,
}

impl GateTopology {
    fn new(backlog: NonZeroUsize) -> Self {
        Self { backlog }
    }
}

impl SegmentedTopology for GateTopology {
    type Producer = GateProducer;
    type Consumer = GateConsumer;

    fn build(
        self,
        workers: NonZeroUsize,
        _queue_capacity: NonZeroUsize,
    ) -> (Self::Producer, Vec<Self::Consumer>) {
        let width = self.backlog.get();
        let total = workers
            .get()
            .checked_mul(width)
            .expect("ring size overflow");
        let slots = (0..total)
            .map(|_| UnsafeCell::new(MaybeUninit::uninit()))
            .collect::<Vec<_>>()
            .into_boxed_slice();
        let shared = Arc::new(Shared {
            width,
            slots: Slots(slots),
            segments: (0..workers.get()).map(|_| Segment::new()).collect(),
            closed: AtomicBool::new(false),
            producer_blocked: AtomicBool::new(false),
            producer_cookie: AtomicU32::new(0),
        });
        let consumers = (0..workers.get())
            .map(|id| GateConsumer {
                shared: Arc::clone(&shared),
                id,
            })
            .collect();
        (
            GateProducer {
                shared,
                active: 0,
                at_gate: true,
            },
            consumers,
        )
    }
}

/// Holds one mutable producer for the duration of each benchmark run.
/// The RefCell is borrowed once per run, not once per task.
pub struct GateScheduler {
    pool: std::cell::RefCell<SegmentedThreadPool<GateTopology, SegmentedWake>>,
}

impl GateScheduler {
    pub fn new(
        workers: NonZeroUsize,
        capacity: NonZeroUsize,
        backlog: usize,
        idle: SegmentedIdle,
    ) -> io::Result<Self> {
        let width = NonZeroUsize::new(backlog).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "backlog=0 needs direct handoff; a queued SPSC ring requires backlog >= 1",
            )
        })?;
        let total = workers
            .get()
            .checked_mul(width.get())
            .filter(|&total| total <= capacity.get())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "hot-first needs workers * backlog <= queue_capacity",
                )
            })?;
        debug_assert!(total != 0);

        let pool = SegmentedThreadPool::new(
            workers,
            capacity,
            GateTopology::new(width),
            SegmentedWake::new(workers, idle),
        )?;
        Ok(Self {
            pool: std::cell::RefCell::new(pool),
        })
    }
}

impl Scheduler for GateScheduler {
    fn run<W: Workload>(&self, workload: &Arc<W>, delivery: &DeliverySchedule) -> RunResult {
        let mut pool = self.pool.borrow_mut();
        run_with_delivery(workload, delivery, |workload, unit, completion| {
            pool.execute(move || {
                workload.execute(&unit);
                completion.complete_one();
            })
            .expect("segmented workers must be alive");
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::{
            atomic::{AtomicUsize, Ordering},
            mpsc,
        },
        thread,
        time::Duration,
    };

    fn build(workers: usize, width: usize) -> (GateProducer, Vec<GateConsumer>) {
        GateTopology::new(NonZeroUsize::new(width).unwrap()).build(
            NonZeroUsize::new(workers).unwrap(),
            NonZeroUsize::new(workers * width).unwrap(),
        )
    }

    fn job() -> Task {
        Box::new(|| {})
    }

    fn consume(consumer: &GateConsumer) {
        match consumer.try_pop() {
            QueuePop::Item(task) => task(),
            _ => panic!("expected task"),
        }
    }

    #[test]
    fn gate_returns_to_hot_segment_when_slots_free() {
        let (mut writer, readers) = build(3, 2);
        assert_eq!(writer.push(job()).ok(), Some(0));
        assert_eq!(writer.push(job()).ok(), Some(0));
        // At the W0 gate: W0 full, go to W1.
        assert_eq!(writer.push(job()).ok(), Some(1));
        // Free W0 during the W1 segment; the pointer must stay on W1
        // until its gate, rather than rescan on the next push.
        consume(&readers[0]);
        assert_eq!(writer.push(job()).ok(), Some(1));
        // W1 gate: W0 has capacity again.
        assert_eq!(writer.push(job()).ok(), Some(0));
    }

    #[test]
    fn gate_skips_full_segments_and_reuses_wrapped_slots() {
        let (mut writer, readers) = build(3, 1);
        for i in 0..3 {
            assert_eq!(writer.push(job()).ok(), Some(i));
        }
        consume(&readers[1]);
        assert_eq!(writer.push(job()).ok(), Some(1));
        consume(&readers[0]);
        assert_eq!(writer.push(job()).ok(), Some(0));
        for consumer in readers {
            loop {
                match consumer.try_pop() {
                    QueuePop::Item(task) => task(),
                    QueuePop::Empty => break,
                    QueuePop::Disconnected => break,
                }
            }
        }
    }

    #[test]
    fn backpressure_wakes_when_a_slot_is_consumed() {
        let (mut writer, readers) = build(1, 1);
        writer.push(job()).unwrap_or_else(|_| panic!("push failed"));
        let (started_tx, started_rx) = mpsc::channel();
        let (done_tx, done_rx) = mpsc::channel();
        let submitter = thread::spawn(move || {
            started_tx.send(()).unwrap();
            writer.push(job()).unwrap_or_else(|_| panic!("push failed"));
            done_tx.send(()).unwrap();
        });
        started_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(done_rx.recv_timeout(Duration::from_millis(20)).is_err());
        consume(&readers[0]);
        done_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        submitter.join().unwrap();
        consume(&readers[0]);
    }

    #[test]
    fn two_worker_pool_executes_each_job_once() {
        let workers = NonZeroUsize::new(2).unwrap();
        let mut pool = SegmentedThreadPool::new(
            workers,
            NonZeroUsize::new(8).unwrap(),
            GateTopology::new(NonZeroUsize::new(2).unwrap()),
            SegmentedWake::new(workers, SegmentedIdle::SpinThenPark(Duration::ZERO)),
        )
        .unwrap();
        let executions = Arc::new(AtomicUsize::new(0));
        for _ in 0..128 {
            let executions = Arc::clone(&executions);
            pool.execute(move || {
                executions.fetch_add(1, Ordering::Relaxed);
            })
            .unwrap();
        }
        drop(pool);
        assert_eq!(executions.load(Ordering::Relaxed), 128);
    }

    #[test]
    fn invalid_configuration_is_rejected() {
        let workers = NonZeroUsize::new(4).unwrap();
        let capacity = NonZeroUsize::new(8).unwrap();
        assert!(
            GateScheduler::new(
                workers,
                capacity,
                0,
                SegmentedIdle::SpinThenPark(Duration::ZERO),
            )
            .is_err()
        );
        assert!(GateScheduler::new(workers, capacity, 3, SegmentedIdle::AgaveBackoff,).is_err());
    }
}
