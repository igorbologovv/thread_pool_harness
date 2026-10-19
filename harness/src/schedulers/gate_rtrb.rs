//! Hot-first gate routing over independent, library-provided rtrb SPSC rings.
//!
//! This is an alternative to gate_ring.rs, not a replacement. Routing,
//! worker affinity, addressed wakes, and bounded producer backpressure are
//! deliberately kept equivalent so the queue implementations can be compared.
//!
//! Unlike the custom ring, rtrb owns its slot storage and cursor protocol.
//! A producer-side sequence counter retains the custom gate cadence.

use std::{
    cell::RefCell,
    io,
    num::NonZeroUsize,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU32, Ordering},
    },
};

use rtrb::{Consumer, PopError, Producer, PushError, RingBuffer};

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

/// The rtrb endpoints own their own ring storage. Only the slow-path
/// producer-capacity notification is shared across segments.
struct Backpressure {
    blocked: AtomicBool,
    cookie: AtomicU32,
}

pub struct RtrbGateProducer {
    writers: Vec<Producer<Task>>,
    /// Successful writes per ring; preserves the custom ring's gate boundary.
    next_sequences: Vec<usize>,
    width: usize,
    active: usize,
    at_gate: bool,
    backpressure: Arc<Backpressure>,
}

impl RtrbGateProducer {
    #[inline]
    fn select_at_gate(&self) -> Option<usize> {
        (0..self.writers.len()).find(|&id| !self.writers[id].is_full())
    }

    fn wait_for_capacity(&self) {
        let shared = &self.backpressure;
        // Every successful pop increments cookie, even when the producer is
        // not blocked. This independent epoch avoids relying on rtrb's
        // internal atomic ordering to prevent a lost capacity notification.
        let cookie = shared.cookie.load(Ordering::SeqCst);
        shared.blocked.store(true, Ordering::SeqCst);
        if self.select_at_gate().is_none() && shared.cookie.load(Ordering::SeqCst) == cookie {
            atomic_wait::wait(&shared.cookie, cookie);
        }
        shared.blocked.store(false, Ordering::SeqCst);
    }
}

impl SegmentedProducer for RtrbGateProducer {
    #[inline]
    fn push(&mut self, mut task: Task) -> Result<usize, Task> {
        loop {
            if self.at_gate || self.writers[self.active].is_full() {
                match self.select_at_gate() {
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

            match self.writers[self.active].push(task) {
                Ok(()) => {
                    let next = self.next_sequences[self.active].wrapping_add(1);
                    self.next_sequences[self.active] = next;
                    self.at_gate = next.is_multiple_of(self.width);
                    return Ok(self.active);
                }
                Err(PushError::Full(returned)) => {
                    // Defensive retry: with one producer this should be
                    // unreachable after a successful capacity probe.
                    task = returned;
                    self.at_gate = true;
                }
            }
        }
    }
}

/// One consumer endpoint is moved to its owning worker. RefCell bridges
/// threadance's &self TaskConsumer API to rtrb's &mut self pop API.
/// Each consumer is Send, never Sync, so no mutex is necessary.
pub struct RtrbGateConsumer {
    reader: RefCell<Consumer<Task>>,
    backpressure: Arc<Backpressure>,
}

impl TaskConsumer for RtrbGateConsumer {
    #[inline]
    fn try_pop(&self) -> QueuePop {
        let mut reader = self.reader.borrow_mut();
        match reader.pop() {
            Ok(task) => {
                // Release of the ring slot precedes this SeqCst epoch update.
                // A sleeping producer either observes the available slot or
                // notices that the epoch changed before going to sleep.
                self.backpressure.cookie.fetch_add(1, Ordering::SeqCst);
                if self.backpressure.blocked.load(Ordering::SeqCst) {
                    atomic_wait::wake_one(&self.backpressure.cookie);
                }
                QueuePop::Item(task)
            }
            Err(PopError::Empty) => {
                if reader.is_abandoned() {
                    // Recheck after observing producer destruction: previously
                    // published tasks must be drained before disconnecting.
                    match reader.pop() {
                        Ok(task) => {
                            self.backpressure.cookie.fetch_add(1, Ordering::SeqCst);
                            if self.backpressure.blocked.load(Ordering::SeqCst) {
                                atomic_wait::wake_one(&self.backpressure.cookie);
                            }
                            QueuePop::Item(task)
                        }
                        Err(PopError::Empty) => QueuePop::Disconnected,
                    }
                } else {
                    QueuePop::Empty
                }
            }
        }
    }
}

pub struct RtrbGateTopology {
    backlog: NonZeroUsize,
}

impl RtrbGateTopology {
    fn new(backlog: NonZeroUsize) -> Self {
        Self { backlog }
    }
}

impl SegmentedTopology for RtrbGateTopology {
    type Producer = RtrbGateProducer;
    type Consumer = RtrbGateConsumer;

    fn build(
        self,
        workers: NonZeroUsize,
        _queue_capacity: NonZeroUsize,
    ) -> (Self::Producer, Vec<Self::Consumer>) {
        let width = self.backlog.get();
        let backpressure = Arc::new(Backpressure {
            blocked: AtomicBool::new(false),
            cookie: AtomicU32::new(0),
        });
        let (writers, consumers): (Vec<_>, Vec<_>) = (0..workers.get())
            .map(|_| {
                let (writer, reader) = RingBuffer::<Task>::new(width);
                (
                    writer,
                    RtrbGateConsumer {
                        reader: RefCell::new(reader),
                        backpressure: Arc::clone(&backpressure),
                    },
                )
            })
            .unzip();

        (
            RtrbGateProducer {
                writers,
                next_sequences: vec![0; workers.get()],
                width,
                active: 0,
                at_gate: true,
                backpressure,
            },
            consumers,
        )
    }
}

pub struct RtrbGateScheduler {
    pool: RefCell<SegmentedThreadPool<RtrbGateTopology, SegmentedWake>>,
}

impl RtrbGateScheduler {
    pub fn new(
        workers: NonZeroUsize,
        capacity: NonZeroUsize,
        backlog: usize,
        idle: SegmentedIdle,
    ) -> io::Result<Self> {
        let width = NonZeroUsize::new(backlog).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "backlog=0 needs direct handoff; rtrb requires backlog >= 1",
            )
        })?;
        workers
            .get()
            .checked_mul(width.get())
            .filter(|&total| total <= capacity.get())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "hot-first rtrb needs workers * backlog <= queue_capacity",
                )
            })?;

        let pool = SegmentedThreadPool::new(
            workers,
            capacity,
            RtrbGateTopology::new(width),
            SegmentedWake::new(workers, idle),
        )?;
        Ok(Self {
            pool: RefCell::new(pool),
        })
    }
}

impl Scheduler for RtrbGateScheduler {
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

    fn build(workers: usize, width: usize) -> (RtrbGateProducer, Vec<RtrbGateConsumer>) {
        RtrbGateTopology::new(NonZeroUsize::new(width).unwrap()).build(
            NonZeroUsize::new(workers).unwrap(),
            NonZeroUsize::new(workers * width).unwrap(),
        )
    }

    fn job() -> Task {
        Box::new(|| {})
    }

    fn consume(consumer: &RtrbGateConsumer) {
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
        assert_eq!(writer.push(job()).ok(), Some(1));
        consume(&readers[0]);
        assert_eq!(writer.push(job()).ok(), Some(1));
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
            RtrbGateTopology::new(NonZeroUsize::new(2).unwrap()),
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
    fn disconnect_drains_accepted_tasks() {
        let (mut writer, readers) = build(1, 2);
        writer.push(job()).unwrap_or_else(|_| panic!("push failed"));
        drop(writer);
        consume(&readers[0]);
        assert!(matches!(readers[0].try_pop(), QueuePop::Disconnected));
    }

    #[test]
    fn invalid_configuration_is_rejected() {
        let workers = NonZeroUsize::new(4).unwrap();
        let capacity = NonZeroUsize::new(8).unwrap();
        assert!(
            RtrbGateScheduler::new(
                workers,
                capacity,
                0,
                SegmentedIdle::SpinThenPark(Duration::ZERO)
            )
            .is_err()
        );
        assert!(RtrbGateScheduler::new(workers, capacity, 3, SegmentedIdle::AgaveBackoff).is_err());
    }
}
