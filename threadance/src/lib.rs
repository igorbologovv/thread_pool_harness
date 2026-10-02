use std::{
    fmt, io,
    num::NonZeroUsize,
    thread::{Builder, JoinHandle},
};

use crossbeam_channel::{Sender, bounded};

type Job = Box<dyn FnOnce() + Send + 'static>;

/// Fixed-size thread pool backed by a bounded shared work queue.
pub struct ThreadPool {
    // The sender is optional so Drop can disconnect the channel before
    // joining worker threads.
    job_sender: Option<Sender<Job>>,
    worker_handles: Vec<JoinHandle<()>>,
}

impl ThreadPool {
    /// Creates a thread pool with a fixed number of worker threads and a
    /// bounded shared job queue.
    pub fn new(num_workers: NonZeroUsize, queue_capacity: NonZeroUsize) -> io::Result<Self> {
        let (job_sender, job_receiver) = bounded::<Job>(queue_capacity.get());

        let mut worker_handles: Vec<JoinHandle<()>> = Vec::with_capacity(num_workers.get());

        for index in 0..num_workers.get() {
            let job_receiver = job_receiver.clone();

            let handle =
                match Builder::new()
                    .name(format!("threadance-{index:02}"))
                    .spawn(move || {
                        while let Ok(job) = job_receiver.recv() {
                            job();
                        }
                    }) {
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
            worker_handles,
        })
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

impl Drop for ThreadPool {
    fn drop(&mut self) {
        // Disconnect the job channel first. Workers continue draining queued
        // jobs and exit once the queue is empty.
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
    };

    use super::ThreadPool;

    #[test]
    fn executes_submitted_jobs() {
        let pool = ThreadPool::new(
            NonZeroUsize::new(4).unwrap(),
            NonZeroUsize::new(16).unwrap(),
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
}
