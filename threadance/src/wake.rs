use std::{
    sync::atomic::{AtomicU32, AtomicUsize, Ordering, fence},
    time::{Duration, Instant},
};

use crate::{
    Task,
    queue::{CrossbeamConsumer, QueuePop, TaskConsumer},
};

// crossbeam_utils::Backoff 0.8.23 constants used by Agave wake-channel.
const AGAVE_BACKOFF_SPIN_LIMIT: u32 = 6;
const AGAVE_BACKOFF_YIELD_LIMIT: u32 = 10;

/// Result of a wake policy waiting through one complete idle episode.
pub enum WaitResult {
    Task(Task),
    Disconnected,
}

/// Complete idle/wake algorithm for a worker pool.
///
/// A policy owns both sides of the protocol: what a worker does after observing
/// an empty queue and how task submission notifies workers that may be blocked.
/// The queue topology is separate; policies interact with it only through the
/// worker-side consumer supplied by the pool.
pub trait WakePolicy<C: TaskConsumer>: Send + Sync + 'static {
    /// Called after the worker has already observed its queue empty. This method
    /// owns the whole idle episode and returns only when a task is available or
    /// the queue has disconnected.
    fn wait(&self, consumer: &C) -> WaitResult;

    /// Called after a task has been pushed successfully.
    fn wake_one(&self);

    /// Called after the producer side is disconnected during pool shutdown.
    fn wake_all(&self);

    /// Number of workers currently sleeping in the policy's blocking path.
    fn sleeping_waiters(&self) -> usize {
        0
    }
}

/// Delegate the complete wait/wake protocol to Crossbeam's channel.
#[derive(Debug, Clone, Copy, Default)]
pub struct CrossbeamWait;

impl WakePolicy<CrossbeamConsumer> for CrossbeamWait {
    #[inline]
    fn wait(&self, consumer: &CrossbeamConsumer) -> WaitResult {
        match consumer.recv_blocking() {
            Ok(task) => WaitResult::Task(task),
            Err(_) => WaitResult::Disconnected,
        }
    }

    #[inline]
    fn wake_one(&self) {}

    #[inline]
    fn wake_all(&self) {}
}

/// Spin for a fixed duration, then park on a shared atomic wake event.
pub struct SpinThenPark {
    spin_duration: Duration,
    event: WakeEvent,
}

impl SpinThenPark {
    pub fn new(spin_duration: Duration) -> Self {
        Self {
            spin_duration,
            event: WakeEvent::default(),
        }
    }

    pub fn spin_duration(&self) -> Duration {
        self.spin_duration
    }

    #[cold]
    #[inline(never)]
    fn wait_parked<C: TaskConsumer>(&self, consumer: &C) -> WaitResult {
        loop {
            let waiter = self.event.register_waiter();
            match consumer.try_pop() {
                QueuePop::Item(task) => return WaitResult::Task(task),
                QueuePop::Disconnected => return WaitResult::Disconnected,
                QueuePop::Empty => waiter.wait(),
            }
        }
    }
}

impl<C: TaskConsumer> WakePolicy<C> for SpinThenPark {
    fn wait(&self, consumer: &C) -> WaitResult {
        if !self.spin_duration.is_zero() {
            let deadline = Instant::now()
                .checked_add(self.spin_duration)
                .expect("worker spin duration is too large");

            loop {
                std::hint::spin_loop();

                match consumer.try_pop() {
                    QueuePop::Item(task) => return WaitResult::Task(task),
                    QueuePop::Disconnected => return WaitResult::Disconnected,
                    QueuePop::Empty if Instant::now() >= deadline => break,
                    QueuePop::Empty => {}
                }
            }
        }

        self.wait_parked(consumer)
    }

    #[inline]
    fn wake_one(&self) {
        self.event.wake_one();
    }

    fn wake_all(&self) {
        self.event.wake_all();
    }

    fn sleeping_waiters(&self) -> usize {
        self.event.sleeping_waiters()
    }
}

/// Agave-style fixed spin/yield backoff followed by atomic blocking wake-up.
#[derive(Default)]
pub struct AgaveBackoff {
    event: WakeEvent,
}

impl<C: TaskConsumer> WakePolicy<C> for AgaveBackoff {
    fn wait(&self, consumer: &C) -> WaitResult {
        for step in 0..=AGAVE_BACKOFF_YIELD_LIMIT {
            if step <= AGAVE_BACKOFF_SPIN_LIMIT {
                for _ in 0..(1u32 << step) {
                    std::hint::spin_loop();
                }
            } else {
                std::thread::yield_now();
            }

            match consumer.try_pop() {
                QueuePop::Item(task) => return WaitResult::Task(task),
                QueuePop::Disconnected => return WaitResult::Disconnected,
                QueuePop::Empty => {}
            }
        }

        loop {
            let waiter = self.event.register_waiter();
            match consumer.try_pop() {
                QueuePop::Item(task) => return WaitResult::Task(task),
                QueuePop::Disconnected => return WaitResult::Disconnected,
                QueuePop::Empty => waiter.wait(),
            }
        }
    }

    #[inline]
    fn wake_one(&self) {
        self.event.wake_one();
    }

    fn wake_all(&self) {
        self.event.wake_all();
    }

    fn sleeping_waiters(&self) -> usize {
        self.event.sleeping_waiters()
    }
}

#[derive(Default)]
struct WakeEvent {
    cookie: AtomicU32,
    waiters: AtomicUsize,
    sleeping_waiters: AtomicUsize,
}

impl WakeEvent {
    #[inline]
    fn register_waiter(&self) -> WakeWaiter<'_> {
        let cookie = self.cookie.load(Ordering::Relaxed);
        self.waiters.fetch_add(1, Ordering::Relaxed);
        fence(Ordering::SeqCst);
        WakeWaiter {
            event: self,
            cookie,
        }
    }

    #[inline]
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

    fn sleeping_waiters(&self) -> usize {
        self.sleeping_waiters.load(Ordering::Relaxed)
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
