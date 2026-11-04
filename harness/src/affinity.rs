//! Linux per-TID CPU affinity, applied before the first benchmark warmup.
use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io, mem, thread,
    time::{Duration, Instant},
};

#[derive(Clone, Copy)]
pub enum PoolKind {
    Rayon,
    Bevy,
    Threadance,
}

impl PoolKind {
    fn index(self, name: &str) -> Option<usize> {
        match self {
            Self::Rayon => name.strip_prefix("rayon-")?.parse().ok(),
            Self::Bevy => name.strip_prefix("bevy (")?.strip_suffix(')')?.parse().ok(),
            Self::Threadance => name.strip_prefix("threadance-")?.parse().ok(),
        }
    }
    fn label(self) -> &'static str {
        match self {
            Self::Rayon => "rayon",
            Self::Bevy => "bevy",
            Self::Threadance => "threadance",
        }
    }
}

fn bad(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

/// The list is ordered: worker i is pinned to entry i; accepts "0-3,8,10-11".
fn parse_cpus(spec: &str) -> io::Result<Vec<usize>> {
    if spec.is_empty() {
        return Err(bad("empty worker CPU list"));
    }
    let mut result = Vec::new();
    let mut seen = BTreeSet::new();
    for token in spec.split(',') {
        let (first, last) = if let Some((a, b)) = token.split_once('-') {
            (
                a.parse::<usize>().map_err(|_| bad("invalid CPU ID"))?,
                b.parse::<usize>().map_err(|_| bad("invalid CPU range"))?,
            )
        } else {
            let cpu = token.parse::<usize>().map_err(|_| bad("invalid CPU ID"))?;
            (cpu, cpu)
        };
        if first > last || last >= libc::CPU_SETSIZE as usize {
            return Err(bad(format!("invalid CPU range: {token}")));
        }
        for cpu in first..=last {
            if !seen.insert(cpu) {
                return Err(bad(format!("duplicate CPU: {cpu}")));
            }
            result.push(cpu);
        }
    }
    Ok(result)
}

fn mask(tid: libc::pid_t) -> io::Result<Vec<usize>> {
    let mut set: libc::cpu_set_t = unsafe { mem::zeroed() };
    if unsafe { libc::sched_getaffinity(tid, mem::size_of_val(&set), &mut set) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok((0..libc::CPU_SETSIZE as usize)
        .filter(|&cpu| unsafe { libc::CPU_ISSET(cpu, &set) })
        .collect())
}

fn pin(tid: libc::pid_t, cpu: usize) -> io::Result<()> {
    let mut set: libc::cpu_set_t = unsafe { mem::zeroed() };
    unsafe {
        libc::CPU_ZERO(&mut set);
        libc::CPU_SET(cpu, &mut set);
    }
    if unsafe { libc::sched_setaffinity(tid, mem::size_of_val(&set), &set) } != 0 {
        return Err(io::Error::last_os_error());
    }
    if mask(tid)? != [cpu] {
        return Err(io::Error::other(format!(
            "TID {tid} did not pin to CPU {cpu}"
        )));
    }
    Ok(())
}

pub struct AffinityPlan {
    workers: Option<Vec<usize>>,
    producer: Option<usize>,
}

impl AffinityPlan {
    pub fn new(count: usize, spec: Option<&str>, producer: Option<usize>) -> io::Result<Self> {
        let workers = spec
            .map(parse_cpus)
            .transpose()?
            .map(|cpus| {
                if cpus.len() < count {
                    Err(bad(format!(
                        "need {count} worker CPUs, received {}",
                        cpus.len()
                    )))
                } else {
                    Ok(cpus.into_iter().take(count).collect::<Vec<_>>())
                }
            })
            .transpose()?;
        if workers.is_some() || producer.is_some() {
            let permitted = mask(0)?;
            for cpu in workers.iter().flatten().copied().chain(producer) {
                if !permitted.contains(&cpu) {
                    return Err(bad(format!(
                        "CPU {cpu} unavailable; permitted={permitted:?}"
                    )));
                }
            }
        }
        Ok(Self { workers, producer })
    }

    /// The main harness thread is the producer/pacer for all delivery modes.
    pub fn pin_producer(&self) -> io::Result<()> {
        if let Some(cpu) = self.producer {
            pin(0, cpu)?;
            println!("CPU_AFFINITY producer cpu={cpu}");
            if self
                .workers
                .as_ref()
                .is_some_and(|cpus| cpus.contains(&cpu))
            {
                eprintln!("WARNING: producer shares CPU {cpu} with a worker");
            }
        }
        Ok(())
    }

    /// Find each named pool worker by its index and pin its Linux TID.
    pub fn pin_workers(&self, kind: PoolKind) -> io::Result<()> {
        let Some(cpus) = &self.workers else {
            return Ok(());
        };
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let mut found = BTreeMap::<usize, libc::pid_t>::new();
            for entry in fs::read_dir("/proc/self/task")? {
                let entry = entry?;
                let Some(tid) = entry
                    .file_name()
                    .to_str()
                    .and_then(|s| s.parse::<i32>().ok())
                else {
                    continue;
                };
                let name = match fs::read_to_string(entry.path().join("comm")) {
                    Ok(name) => name,
                    Err(err) if err.kind() == io::ErrorKind::NotFound => continue,
                    Err(err) => return Err(err),
                };
                let Some(index) = kind.index(name.trim()) else {
                    continue;
                };
                if index >= cpus.len() || found.insert(index, tid).is_some() {
                    return Err(io::Error::other(format!(
                        "unexpected {} worker {index}",
                        kind.label()
                    )));
                }
            }
            if found.len() == cpus.len() {
                for (index, &cpu) in cpus.iter().enumerate() {
                    let tid = found[&index];
                    pin(tid, cpu)?;
                    println!(
                        "CPU_AFFINITY pool={} worker={} tid={} cpu={}",
                        kind.label(),
                        index,
                        tid,
                        cpu
                    );
                }
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(io::Error::other(format!(
                    "missing {} workers: {found:?}",
                    kind.label()
                )));
            }
            thread::sleep(Duration::from_millis(5));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{mask, parse_cpus, pin};
    #[test]
    fn cpu_lists() {
        assert_eq!(parse_cpus("2,0-1").unwrap(), vec![2, 0, 1]);
        for value in ["", "0,", "0,0", "2-1", "1024", "x"] {
            assert!(parse_cpus(value).is_err());
        }
    }
    #[test]
    fn pinning_changes_kernel_mask() {
        let cpu = mask(0).unwrap()[0];
        std::thread::spawn(move || {
            pin(0, cpu).unwrap();
            assert_eq!(mask(0).unwrap(), vec![cpu]);
        })
        .join()
        .unwrap();
    }
}
