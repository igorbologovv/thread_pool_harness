use std::{
    collections::HashSet,
    env,
    error::Error,
    fs, io,
    path::{Path, PathBuf},
    process::Command,
};

use rusqlite::{Connection, params};

use crate::{
    cli::{Cli, ProfileMode, SchedulerKind, WorkloadKind},
    delivery::DeliveryMode,
    perf_control::{DEEP_PASSES, PerfCapture},
    result::RunResult,
};

const SCHEMA: &str = include_str!("../../sql/schema.sql");

const MIGRATION_002_PERF_PASSES: &str = include_str!("../../sql/migrations/002_perf_passes.sql");

type StorageError = Box<dyn Error + Send + Sync + 'static>;
type StorageResult<T> = Result<T, StorageError>;

#[derive(Debug)]
pub struct StoredBenchmark {
    pub session_id: i64,
    pub experiment_id: i64,
}

pub struct BenchmarkDb {
    connection: Connection,
    session_id: i64,
}

impl BenchmarkDb {
    /// Opens the benchmark database and creates a session for this invocation.
    ///
    /// Database creation and all inserts happen outside the measured benchmark
    /// interval.
    pub fn open(path: &Path, started_unix_seconds: i64) -> StorageResult<Self> {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)?;
        }

        let connection = Connection::open(path)?;

        connection.execute_batch(
            "
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = WAL;
            ",
        )?;

        // Keep database initialization tied to the source-controlled schema.
        connection.execute_batch(SCHEMA)?;
        apply_migrations(&connection)?;

        let session_id = insert_session(&connection, started_unix_seconds)?;

        Ok(Self {
            connection,
            session_id,
        })
    }

    /// Stores one benchmark configuration and all of its measured runs.
    ///
    /// Warm-up runs are intentionally not included.
    pub fn store_experiment(
        &mut self,
        cli: &Cli,
        results: &[RunResult],
        perf_captures: &[Option<PerfCapture>],
    ) -> StorageResult<StoredBenchmark> {
        let expected_results = match cli.profile {
            ProfileMode::Deep => {
                cli.runs
                    .get()
                    .checked_mul(DEEP_PASSES.len())
                    .ok_or_else(|| {
                        io::Error::new(io::ErrorKind::InvalidInput, "deep run count overflow")
                    })?
            }

            ProfileMode::None | ProfileMode::Standard => cli.runs.get(),
        };

        if results.len() != expected_results {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "expected {expected_results} physical runs but received {}",
                    results.len(),
                ),
            )
            .into());
        }

        if perf_captures.len() != results.len() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "received {} results but {} perf captures",
                    results.len(),
                    perf_captures.len()
                ),
            )
            .into());
        }

        match cli.profile {
            ProfileMode::None => {
                if perf_captures.iter().any(|capture| capture.is_some()) {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "profile=none produced perf data",
                    )
                    .into());
                }
            }

            ProfileMode::Standard | ProfileMode::Deep => {
                if perf_captures.iter().any(|capture| capture.is_none()) {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "profiled run missing perf capture",
                    )
                    .into());
                }
            }
        }

        let workers = usize_to_i64(cli.workers.get(), "workers")?;
        let work_units = usize_to_i64(cli.work_units.get(), "work_units")?;
        let warmup_runs = usize_to_i64(cli.warmup, "warmup_runs")?;
        let measured_runs = usize_to_i64(cli.runs.get(), "measured_runs")?;

        let queue_capacity = match cli.scheduler {
            SchedulerKind::Threadance => {
                Some(usize_to_i64(cli.queue_capacity.get(), "queue_capacity")?)
            }
            SchedulerKind::Rayon | SchedulerKind::Bevy => None,
        };

        let delivery_mode = cli.delivery_mode();

        let arrival_window_ms = match delivery_mode {
            DeliveryMode::AllAtOnce => None,
            DeliveryMode::SteadyArrivals
            | DeliveryMode::VariableArrivals
            | DeliveryMode::BurstyArrivals => Some(u64_to_i64(
                cli.arrival_window_ms.get(),
                "arrival_window_ms",
            )?),
        };

        let arrival_seed = u64_to_i64(cli.arrival_seed, "arrival_seed")?;
        let workload_seed = u64_to_i64(cli.seed, "workload_seed")?;

        let (
            operations_per_work_unit,
            validators,
            signers_per_certificate,
            certificates_per_work_unit,
        ) = match cli.workload {
            WorkloadKind::ComputeHeavy => (
                cli.operations_per_work_unit
                    .map(|value| usize_to_i64(value.get(), "operations_per_work_unit"))
                    .transpose()?,
                None,
                None,
                None,
            ),

            WorkloadKind::BlsAggregateVerify => (
                None,
                Some(usize_to_i64(cli.validators.get(), "validators")?),
                Some(usize_to_i64(
                    cli.signers_per_certificate.get(),
                    "signers_per_certificate",
                )?),
                Some(usize_to_i64(
                    cli.certificates_per_work_unit.get(),
                    "certificates_per_work_unit",
                )?),
            ),
        };

        let command_line = current_command_line();

        let tx = self.connection.transaction()?;

        tx.execute(
            "
            INSERT INTO experiment (
                session_id,
                scheduler,
                workload,
                profile_mode,
                workers,
                queue_capacity,
                delivery_mode,
                arrival_window_ms,
                arrival_seed,
                work_units,
                workload_seed,
                warmup_runs,
                measured_runs,
                operations_per_work_unit,
                validators,
                signers_per_certificate,
                certificates_per_work_unit,
                command_line,
                extra_config_json,
                notes
            )
            VALUES (
                ?1, ?2, ?3, ?4, ?5,
                ?6, ?7, ?8, ?9, ?10,
                ?11, ?12, ?13, ?14, ?15,
                ?16, ?17, ?18, NULL, NULL
            )
            ",
            params![
                self.session_id,
                scheduler_name(cli.scheduler),
                cli.workload.to_string(),
                profile_name(cli.profile),
                workers,
                queue_capacity,
                delivery_mode.to_string(),
                arrival_window_ms,
                arrival_seed,
                work_units,
                workload_seed,
                warmup_runs,
                measured_runs,
                operations_per_work_unit,
                validators,
                signers_per_certificate,
                certificates_per_work_unit,
                command_line,
            ],
        )?;

        let experiment_id = tx.last_insert_rowid();

        {
            let mut run_statement = tx.prepare(
                "
                INSERT INTO run (
                    experiment_id,
                    run_index,
                    perf_pass,
                    pass_run_index,
                    completed_work_units,
                    elapsed_ns,
                    work_units_per_second,
                    success,
                    error_text
                )
                VALUES (
                    ?1, ?2, ?3, ?4, ?5,
                    ?6, ?7, 1, NULL
                )
                ",
            )?;

            let mut capture_statement = tx.prepare(
                "
                INSERT INTO perf_capture (
                    run_id,
                    perf_command,
                    raw_json,
                    perf_exit_code
                )
                VALUES (?1, ?2, ?3, ?4)
                ",
            )?;

            let mut metric_statement = tx.prepare(
                "
                INSERT INTO perf_metric (
                    run_id,
                    event_name,
                    counter_value,
                    unit,
                    event_runtime_ns,
                    percent_running,
                    metric_value,
                    metric_unit,
                    status
                )
                VALUES (
                    ?1, ?2, ?3, ?4, ?5,
                    ?6, ?7, ?8, ?9
                )
                ",
            )?;

            for (run_index, (result, capture)) in
                results.iter().zip(perf_captures.iter()).enumerate()
            {
                let perf_pass = capture
                    .as_ref()
                    .map(|capture| capture.pass_name.as_str())
                    .unwrap_or("none");

                let pass_run_index = match cli.profile {
                    ProfileMode::Deep => run_index % cli.runs.get(),

                    ProfileMode::None | ProfileMode::Standard => run_index,
                };

                run_statement.execute(params![
                    experiment_id,
                    usize_to_i64(run_index, "run_index",)?,
                    perf_pass,
                    usize_to_i64(pass_run_index, "pass_run_index",)?,
                    u64_to_i64(result.completed_work_units, "completed_work_units",)?,
                    u64_to_i64(result.elapsed_ns, "elapsed_ns",)?,
                    result.work_units_per_second,
                ])?;

                let run_id = tx.last_insert_rowid();

                if let Some(capture) = capture {
                    capture_statement.execute(params![
                        run_id,
                        capture.perf_command,
                        capture.raw_json,
                        capture.perf_exit_code,
                    ])?;

                    for metric in &capture.metrics {
                        metric_statement.execute(params![
                            run_id,
                            metric.event_name,
                            metric.counter_value,
                            metric.unit,
                            metric.event_runtime_ns,
                            metric.percent_running,
                            metric.metric_value,
                            metric.metric_unit,
                            metric.status,
                        ])?;
                    }
                }
            }
        }

        tx.commit()?;

        Ok(StoredBenchmark {
            session_id: self.session_id,
            experiment_id,
        })
    }
}

fn apply_migrations(connection: &Connection) -> StorageResult<()> {
    let has_perf_pass: bool = connection.query_row(
        "
        SELECT EXISTS (
            SELECT 1
            FROM pragma_table_info('run')
            WHERE name = 'perf_pass'
        )
        ",
        [],
        |row| row.get(0),
    )?;

    if !has_perf_pass {
        // Version 2 may have been used by an abandoned local
        // migration while developing the profiling design.
        connection.execute("DELETE FROM schema_migrations WHERE version = 2", [])?;

        connection.execute_batch(MIGRATION_002_PERF_PASSES)?;
    }

    Ok(())
}

fn insert_session(connection: &Connection, started_unix_seconds: i64) -> StorageResult<i64> {
    let git_root = git_root();

    let git_commit = git_root
        .as_deref()
        .and_then(|root| git_output(root, &["rev-parse", "HEAD"]))
        .unwrap_or_else(|| "unknown".to_owned());

    let git_branch = git_root
        .as_deref()
        .and_then(|root| git_output(root, &["branch", "--show-current"]))
        .filter(|value| !value.is_empty());

    let git_remote_url = git_root
        .as_deref()
        .and_then(|root| git_output(root, &["config", "--get", "remote.origin.url"]))
        .filter(|value| !value.is_empty());

    let git_dirty = git_root
        .as_deref()
        .and_then(|root| git_output(root, &["status", "--porcelain"]))
        .map(|status| !status.is_empty())
        .unwrap_or(true);

    let hostname = command_output("hostname", &[]);
    let operating_system = operating_system();
    let kernel_version = command_output("uname", &["-r"]);
    let cpu_arch = command_output("uname", &["-m"]);
    let cpu_model = cpu_model();

    let physical_cores = physical_core_count()
        .map(|value| usize_to_i64(value, "physical_cores"))
        .transpose()?;

    let logical_cpus = std::thread::available_parallelism()
        .ok()
        .map(|value| usize_to_i64(value.get(), "logical_cpus"))
        .transpose()?;

    let memory_bytes = memory_bytes()
        .map(|value| u64_to_i64(value, "memory_bytes"))
        .transpose()?;

    let rustc_version = command_output("rustc", &["--version"]);
    let cargo_version = command_output("cargo", &["--version"]);
    let perf_version = command_output("perf", &["--version"]);

    let perf_event_paranoid = fs::read_to_string("/proc/sys/kernel/perf_event_paranoid")
        .ok()
        .and_then(|value| value.trim().parse::<i64>().ok());

    connection.execute(
        "
        INSERT INTO benchmark_session (
            started_at,
            git_commit,
            git_branch,
            git_dirty,
            git_remote_url,
            hostname,
            operating_system,
            kernel_version,
            cpu_arch,
            cpu_model,
            physical_cores,
            logical_cpus,
            memory_bytes,
            rustc_version,
            cargo_version,
            perf_version,
            perf_event_paranoid,
            notes
        )
        VALUES (
            datetime(?1, 'unixepoch'),
            ?2, ?3, ?4, ?5,
            ?6, ?7, ?8, ?9, ?10,
            ?11, ?12, ?13, ?14, ?15,
            ?16, ?17, NULL
        )
        ",
        params![
            started_unix_seconds,
            git_commit,
            git_branch,
            i64::from(git_dirty),
            git_remote_url,
            hostname,
            operating_system,
            kernel_version,
            cpu_arch,
            cpu_model,
            physical_cores,
            logical_cpus,
            memory_bytes,
            rustc_version,
            cargo_version,
            perf_version,
            perf_event_paranoid,
        ],
    )?;

    Ok(connection.last_insert_rowid())
}

fn scheduler_name(scheduler: SchedulerKind) -> &'static str {
    match scheduler {
        SchedulerKind::Rayon => "rayon",
        SchedulerKind::Bevy => "bevy",
        SchedulerKind::Threadance => "threadance",
    }
}

fn profile_name(profile: ProfileMode) -> &'static str {
    match profile {
        ProfileMode::None => "none",
        ProfileMode::Standard => "standard",
        ProfileMode::Deep => "deep",
    }
}

fn usize_to_i64(value: usize, field: &str) -> StorageResult<i64> {
    i64::try_from(value).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{field} does not fit into SQLite INTEGER"),
        )
        .into()
    })
}

fn u64_to_i64(value: u64, field: &str) -> StorageResult<i64> {
    i64::try_from(value).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{field} does not fit into SQLite INTEGER"),
        )
        .into()
    })
}

fn current_command_line() -> String {
    env::args_os()
        .map(|arg| shell_quote(&arg.to_string_lossy()))
        .collect::<Vec<_>>()
        .join(" ")
}

fn shell_quote(value: &str) -> String {
    if !value.is_empty()
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "-_./=:".contains(character))
    {
        value.to_owned()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

fn git_root() -> Option<PathBuf> {
    command_output("git", &["rev-parse", "--show-toplevel"]).map(PathBuf::from)
}

fn git_output(root: &Path, args: &[&str]) -> Option<String> {
    let root = root.to_string_lossy();

    let mut full_args = vec!["-C", root.as_ref()];
    full_args.extend_from_slice(args);

    command_output("git", &full_args)
}

fn command_output(program: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(program).args(args).output().ok()?;

    if !output.status.success() {
        return None;
    }

    Some(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn operating_system() -> Option<String> {
    let contents = fs::read_to_string("/etc/os-release").ok()?;

    contents.lines().find_map(|line| {
        line.strip_prefix("PRETTY_NAME=")
            .map(|value| value.trim_matches('"').to_owned())
    })
}

fn cpu_model() -> Option<String> {
    if let Some(lscpu) = command_output("lscpu", &[]) {
        if let Some(model) = lscpu.lines().find_map(|line| {
            line.strip_prefix("Model name:")
                .map(|value| value.trim().to_owned())
        }) {
            return Some(model);
        }
    }

    let cpuinfo = fs::read_to_string("/proc/cpuinfo").ok()?;

    cpuinfo.lines().find_map(|line| {
        let (key, value) = line.split_once(':')?;

        match key.trim() {
            "model name" | "Hardware" | "Processor" => Some(value.trim().to_owned()),
            _ => None,
        }
    })
}

fn physical_core_count() -> Option<usize> {
    let output = command_output("lscpu", &["-p=CORE,SOCKET"])?;

    let cores: HashSet<&str> = output
        .lines()
        .filter(|line| !line.starts_with('#'))
        .filter(|line| !line.trim().is_empty())
        .collect();

    (!cores.is_empty()).then_some(cores.len())
}

fn memory_bytes() -> Option<u64> {
    let meminfo = fs::read_to_string("/proc/meminfo").ok()?;

    let line = meminfo.lines().find(|line| line.starts_with("MemTotal:"))?;

    let kib = line.split_whitespace().nth(1)?.parse::<u64>().ok()?;

    kib.checked_mul(1024)
}
