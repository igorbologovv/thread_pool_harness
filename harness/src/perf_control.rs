use std::{
    env,
    error::Error,
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    os::unix::{ffi::OsStrExt, fs::OpenOptionsExt, process::ExitStatusExt},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::atomic::{AtomicU64, Ordering},
    thread,
    time::{Duration, Instant},
};

use serde_json::{Value, json};

use crate::result::RunResult;

type PerfError = Box<dyn Error + Send + Sync + 'static>;
type PerfResult<T> = Result<T, PerfError>;

const ACK_TIMEOUT: Duration = Duration::from_secs(10);

static SESSION_COUNTER: AtomicU64 = AtomicU64::new(0);

const STANDARD_EVENTS: &[&str] = &[
    "task-clock",
    "cycles",
    "instructions",
    "branches",
    "branch-misses",
    "context-switches",
    "cpu-migrations",
    "page-faults",
];

const DEEP_CACHE_EVENTS: &[&str] = &["task-clock", "cache-references", "cache-misses"];

const DEEP_L1D_EVENTS: &[&str] = &["task-clock", "L1-dcache-loads", "L1-dcache-load-misses"];

const DEEP_DTLB_EVENTS: &[&str] = &["task-clock", "l1_dtlb_misses", "l2_dtlb_misses"];

const DEEP_ITLB_EVENTS: &[&str] = &["task-clock", "bp_l1_tlb_miss_l2_tlb_hit", "l2_itlb_misses"];

#[derive(Debug, Clone, Copy)]
pub struct PerfPass {
    pub name: &'static str,
    pub events: &'static [&'static str],
}

pub const STANDARD_PASS: PerfPass = PerfPass {
    name: "standard",
    events: STANDARD_EVENTS,
};

pub const DEEP_PASSES: &[PerfPass] = &[
    PerfPass {
        name: "cache",
        events: DEEP_CACHE_EVENTS,
    },
    PerfPass {
        name: "l1d",
        events: DEEP_L1D_EVENTS,
    },
    PerfPass {
        name: "dtlb",
        events: DEEP_DTLB_EVENTS,
    },
    PerfPass {
        name: "itlb",
        events: DEEP_ITLB_EVENTS,
    },
];

#[derive(Debug)]
pub struct PerfMetric {
    pub event_name: String,
    pub counter_value: Option<f64>,
    pub unit: Option<String>,
    pub event_runtime_ns: Option<i64>,
    pub percent_running: Option<f64>,
    pub metric_value: Option<f64>,
    pub metric_unit: Option<String>,
    pub status: String,
}

#[derive(Debug)]
pub struct PerfCapture {
    pub pass_name: String,
    pub perf_command: String,
    pub raw_json: String,
    pub perf_exit_code: i32,
    pub metrics: Vec<PerfMetric>,
}

pub fn measure_run<F>(pass: PerfPass, run_once: F) -> PerfResult<(RunResult, PerfCapture)>
where
    F: FnOnce() -> RunResult,
{
    let mut perf = PerfSession::start(pass.events)?;

    perf.send_command("enable")?;

    let result = run_once();

    perf.send_command("disable")?;

    let mut capture = perf.finish()?;
    capture.pass_name = pass.name.to_owned();

    Ok((result, capture))
}

struct PerfSession {
    child: Option<Child>,
    control: File,
    ack: File,

    temp_dir: PathBuf,
    output_path: PathBuf,

    command_line: String,
}

impl PerfSession {
    fn start(events: &[&str]) -> PerfResult<Self> {
        let sequence = SESSION_COUNTER.fetch_add(1, Ordering::Relaxed);

        let temp_dir =
            env::temp_dir().join(format!("threadance-perf-{}-{sequence}", std::process::id(),));

        fs::create_dir(&temp_dir)?;

        let control_path = temp_dir.join("control.fifo");
        let ack_path = temp_dir.join("ack.fifo");
        let output_path = temp_dir.join("perf.json");

        create_fifo(&control_path)?;
        create_fifo(&ack_path)?;

        let control = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&control_path)?;

        let ack = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK)
            .open(&ack_path)?;

        let events = events.join(",");
        let pid = std::process::id().to_string();

        let control_spec = format!("fifo:{},{}", control_path.display(), ack_path.display(),);

        let args = vec![
            "stat".to_owned(),
            "-j".to_owned(),
            "--no-big-num".to_owned(),
            "-e".to_owned(),
            events,
            "-p".to_owned(),
            pid,
            "-D".to_owned(),
            "-1".to_owned(),
            "--control".to_owned(),
            control_spec,
            "-o".to_owned(),
            output_path.display().to_string(),
        ];

        let command_line = format_command("perf", &args);

        let child = Command::new("perf")
            .args(&args)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()?;

        let mut session = Self {
            child: Some(child),
            control,
            ack,
            temp_dir,
            output_path,
            command_line,
        };

        // Readiness handshake. Counters are already disabled
        // because perf was started using -D -1.
        session.send_command("disable")?;

        Ok(session)
    }

    fn send_command(&mut self, command: &str) -> PerfResult<()> {
        writeln!(self.control, "{command}")?;
        self.control.flush()?;

        self.wait_for_ack(command)
    }

    fn wait_for_ack(&mut self, command: &str) -> PerfResult<()> {
        let deadline = Instant::now() + ACK_TIMEOUT;
        let mut response = Vec::new();

        loop {
            let mut buffer = [0_u8; 64];

            match self.ack.read(&mut buffer) {
                Ok(0) => {}

                Ok(length) => {
                    response.extend_from_slice(&buffer[..length]);

                    if response.iter().any(|byte| *byte == b'\n' || *byte == 0) {
                        let response = String::from_utf8_lossy(&response)
                            .trim_matches(|c: char| c == '\0' || c.is_whitespace())
                            .to_owned();

                        if response == "ack" {
                            return Ok(());
                        }

                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            format!(
                                "unexpected perf acknowledgement \
                                     for {command:?}: {response:?}"
                            ),
                        )
                        .into());
                    }
                }

                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {}

                Err(error) => return Err(error.into()),
            }

            if let Some(child) = self.child.as_mut() {
                if let Some(status) = child.try_wait()? {
                    return Err(io::Error::other(format!(
                        "perf exited before acknowledging \
                             {command:?}: {status}"
                    ))
                    .into());
                }
            }

            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    format!(
                        "timed out waiting for perf \
                             acknowledgement of {command:?}"
                    ),
                )
                .into());
            }

            thread::sleep(Duration::from_millis(1));
        }
    }

    fn finish(mut self) -> PerfResult<PerfCapture> {
        let mut child = self.child.take().expect("perf child must exist");

        let status = match child.try_wait()? {
            Some(status) => status,

            None => {
                send_signal(&child, libc::SIGINT)?;
                child.wait()?
            }
        };

        let mut stderr = String::new();

        if let Some(mut pipe) = child.stderr.take() {
            pipe.read_to_string(&mut stderr)?;
        }

        let perf_exit_code = status
            .code()
            .unwrap_or_else(|| status.signal().map(|signal| 128 + signal).unwrap_or(-1));

        let raw_output = fs::read_to_string(&self.output_path).unwrap_or_default();

        let (raw_json, metrics) = parse_perf_output(&raw_output, &stderr)?;

        let _ = fs::remove_dir_all(&self.temp_dir);

        Ok(PerfCapture {
            pass_name: String::new(),
            perf_command: self.command_line.clone(),
            raw_json,
            perf_exit_code,
            metrics,
        })
    }
}

impl Drop for PerfSession {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            if child.try_wait().ok().flatten().is_none() {
                let _ = send_signal(&child, libc::SIGINT);

                let _ = child.wait();
            }
        }

        let _ = fs::remove_dir_all(&self.temp_dir);
    }
}

fn create_fifo(path: &Path) -> PerfResult<()> {
    let path = CString::new(path.as_os_str().as_bytes())?;

    let result = unsafe { libc::mkfifo(path.as_ptr(), 0o600) };

    if result == -1 {
        return Err(io::Error::last_os_error().into());
    }

    Ok(())
}

fn send_signal(child: &Child, signal: i32) -> PerfResult<()> {
    let result = unsafe { libc::kill(child.id() as libc::pid_t, signal) };

    if result == -1 {
        let error = io::Error::last_os_error();

        if error.raw_os_error() != Some(libc::ESRCH) {
            return Err(error.into());
        }
    }

    Ok(())
}

fn parse_perf_output(raw_output: &str, stderr: &str) -> PerfResult<(String, Vec<PerfMetric>)> {
    let mut records = Vec::new();
    let mut unparsed_lines = Vec::new();
    let mut metrics = Vec::new();

    for line in raw_output.lines() {
        let line = line.trim().trim_end_matches(',');

        if line.is_empty() {
            continue;
        }

        match serde_json::from_str::<Value>(line) {
            Ok(record) => {
                if let Some(metric) = parse_metric(&record) {
                    metrics.push(metric);
                }

                records.push(record);
            }

            Err(_) => {
                unparsed_lines.push(line.to_owned());
            }
        }
    }

    let stored = json!({
        "records": records,
        "unparsed": unparsed_lines,
        "stderr": stderr.trim(),
    });

    Ok((serde_json::to_string(&stored)?, metrics))
}

fn parse_metric(record: &Value) -> Option<PerfMetric> {
    let event_name = record.get("event")?.as_str()?.to_owned();

    let counter_field = record.get("counter-value");

    let (counter_value, status) = parse_counter_value(counter_field);

    Some(PerfMetric {
        event_name,
        counter_value,

        unit: optional_string(record.get("unit")),

        event_runtime_ns: numeric_value(record.get("event-runtime")).and_then(float_to_i64),

        percent_running: numeric_value(record.get("pcnt-running")),

        metric_value: numeric_value(record.get("metric-value")),

        metric_unit: optional_string(record.get("metric-unit")),

        status,
    })
}

fn parse_counter_value(value: Option<&Value>) -> (Option<f64>, String) {
    match value {
        Some(Value::String(value)) if value.contains("not supported") => {
            (None, "not-supported".to_owned())
        }

        Some(Value::String(value)) if value.contains("not counted") => {
            (None, "not-counted".to_owned())
        }

        Some(value) => match numeric_value(Some(value)) {
            Some(number) => (Some(number), "ok".to_owned()),

            None => (None, "unavailable".to_owned()),
        },

        None => (None, "unavailable".to_owned()),
    }
}

fn numeric_value(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Number(number) => number.as_f64(),

        Value::String(value) => value.replace(',', "").parse::<f64>().ok(),

        _ => None,
    }
}

fn optional_string(value: Option<&Value>) -> Option<String> {
    let value = value?.as_str()?.trim();

    if value.is_empty() {
        None
    } else {
        Some(value.to_owned())
    }
}

fn float_to_i64(value: f64) -> Option<i64> {
    if value.is_finite() && value >= i64::MIN as f64 && value <= i64::MAX as f64 {
        Some(value.round() as i64)
    } else {
        None
    }
}

fn format_command(program: &str, args: &[String]) -> String {
    std::iter::once(program.to_owned())
        .chain(args.iter().cloned())
        .map(|part| shell_quote(&part))
        .collect::<Vec<_>>()
        .join(" ")
}

fn shell_quote(value: &str) -> String {
    if !value.is_empty()
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "-_./=:,".contains(character))
    {
        value.to_owned()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"),)
    }
}
