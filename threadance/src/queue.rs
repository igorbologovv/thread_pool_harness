use std::num::NonZeroUsize;

use crossbeam_channel::{RecvError, TryRecvError, bounded};

use crate::Task;

/// Result of a non-blocking task-queue probe.
pub enum QueuePop {
    Item(Task),
    Empty,
    Disconnected,
}

/// Producer endpoint created by a [`QueueTopology`].
pub trait TaskProducer: Send + Sync + 'static {
    fn push(&self, task: Task) -> Result<(), Task>;
}

/// Worker-side endpoint created by a [`QueueTopology`].
pub trait TaskConsumer: Send + 'static {
    fn try_pop(&self) -> QueuePop;
}

/// Defines how submitted tasks are routed to workers.
///
/// A topology owns the number and kind of channels. It may create one global
/// MPMC queue, per-worker queues, a dispatcher, or another routing structure
/// without changing the thread-pool or wake-policy code.
pub trait QueueTopology: Send + Sync + 'static {
    type Producer: TaskProducer;
    type Consumer: TaskConsumer;

    fn build(
        &self,
        workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
    ) -> (Self::Producer, Vec<Self::Consumer>);
}

/// One bounded Crossbeam MPMC queue shared by all producers and workers.
#[derive(Debug, Clone, Copy, Default)]
pub struct CrossbeamMpmc;

#[doc(hidden)]
pub struct CrossbeamProducer {
    inner: crossbeam_channel::Sender<Task>,
}

#[doc(hidden)]
pub struct CrossbeamConsumer {
    inner: crossbeam_channel::Receiver<Task>,
}

impl CrossbeamConsumer {
    pub(crate) fn recv_blocking(&self) -> Result<Task, RecvError> {
        self.inner.recv()
    }
}

impl TaskProducer for CrossbeamProducer {
    fn push(&self, task: Task) -> Result<(), Task> {
        self.inner.send(task).map_err(|error| error.0)
    }
}

impl TaskConsumer for CrossbeamConsumer {
    #[inline]
    fn try_pop(&self) -> QueuePop {
        match self.inner.try_recv() {
            Ok(task) => QueuePop::Item(task),
            Err(TryRecvError::Empty) => QueuePop::Empty,
            Err(TryRecvError::Disconnected) => QueuePop::Disconnected,
        }
    }
}

impl QueueTopology for CrossbeamMpmc {
    type Producer = CrossbeamProducer;
    type Consumer = CrossbeamConsumer;

    fn build(
        &self,
        workers: NonZeroUsize,
        queue_capacity: NonZeroUsize,
    ) -> (Self::Producer, Vec<Self::Consumer>) {
        let (sender, receiver) = bounded(queue_capacity.get());
        let consumers = (0..workers.get())
            .map(|_| CrossbeamConsumer {
                inner: receiver.clone(),
            })
            .collect();

        (CrossbeamProducer { inner: sender }, consumers)
    }
}
