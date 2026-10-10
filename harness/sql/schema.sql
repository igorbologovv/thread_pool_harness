PRAGMA foreign_keys = ON;

-- ============================================================
-- Schema version
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version         INTEGER PRIMARY KEY,
    applied_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description     TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations (
    version,
    description
) VALUES
    (1, 'Initial benchmark storage schema'),
    (2, 'Record physical perf pass for multi-pass deep profiling'),
    (3, 'Add mean arrival rate to benchmark experiments'),
    (4, 'Add Threadance worker spin-before-block duration'),
    (5, 'Allow deterministic fixed-arrivals delivery mode'),
    (6, 'Record actual wall-clock start time for measured runs'),
    (7, 'Group benchmark sessions into campaigns');


-- ============================================================
-- Benchmark session
--
-- One session represents one invocation of the benchmark harness.
-- Multiple sessions may belong to the same benchmark campaign / sweep.
-- ============================================================

CREATE TABLE IF NOT EXISTS benchmark_session (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    started_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Groups separate harness invocations belonging to one sweep.
    campaign_id         TEXT,

    -- Git state
    git_commit          TEXT NOT NULL,
    git_branch          TEXT,
    git_dirty           INTEGER NOT NULL
                            CHECK (git_dirty IN (0, 1)),
    git_remote_url      TEXT,

    -- Host
    hostname            TEXT,
    operating_system    TEXT,
    kernel_version      TEXT,

    -- CPU
    cpu_arch            TEXT,
    cpu_model           TEXT,
    physical_cores      INTEGER,
    logical_cpus        INTEGER,

    -- Memory
    memory_bytes        INTEGER,

    -- Toolchain
    rustc_version       TEXT,
    cargo_version       TEXT,
    perf_version        TEXT,

    -- Relevant Linux perf configuration
    perf_event_paranoid INTEGER,

    -- Optional additional information
    notes               TEXT
);


-- ============================================================
-- Additional session metadata
--
-- Key/value storage lets us record machine/environment details
-- later without changing the main schema.
--
-- Examples:
--   cpu_governor
--   smt_state
--   numa_nodes
--   cpu_microcode
--   transparent_hugepages
-- ============================================================

CREATE TABLE IF NOT EXISTS session_metadata (
    session_id      INTEGER NOT NULL
                        REFERENCES benchmark_session(id)
                        ON DELETE CASCADE,

    key             TEXT NOT NULL,
    value           TEXT NOT NULL,

    PRIMARY KEY (session_id, key)
);


-- ============================================================
-- Experiment
--
-- One experiment is one exact benchmark configuration:
--
-- scheduler + workload + workers + delivery configuration +
-- workload parameters + profiling mode.
--
-- The experiment contains multiple measured runs.
-- ============================================================

CREATE TABLE IF NOT EXISTS experiment (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    session_id                      INTEGER NOT NULL
                                        REFERENCES benchmark_session(id)
                                        ON DELETE CASCADE,

    created_at                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Main configuration
    scheduler                       TEXT NOT NULL,
    workload                        TEXT NOT NULL,

    profile_mode                    TEXT NOT NULL
                                        CHECK (
                                            profile_mode IN (
                                                'none',
                                                'standard',
                                                'deep'
                                            )
                                        ),

    workers                         INTEGER NOT NULL
                                        CHECK (workers > 0),

    -- Relevant only to Threadance.
    -- NULL for schedulers where this parameter does not apply.
    queue_capacity                  INTEGER,

    -- Threadance active-poll duration before blocking.
    -- Zero means immediate blocking. NULL for other schedulers.
    threadance_spin_us              INTEGER
                                        CHECK (
                                            threadance_spin_us IS NULL
                                            OR threadance_spin_us >= 0
                                        ),

    -- Work delivery
    delivery_mode                   TEXT NOT NULL
                                        CHECK (
                                            delivery_mode IN (
                                                'all-at-once',
                                                'fixed-arrivals',
                                                'steady-arrivals',
                                                'variable-arrivals',
                                                'bursty-arrivals'
                                            )
                                        ),

    -- NULL for all-at-once.
    arrival_window_ms               INTEGER,

    -- Mean work-unit arrival rate for paced delivery modes.
    arrival_rate                    REAL
                                        CHECK (
                                            arrival_rate IS NULL
                                            OR arrival_rate > 0
                                        ),

    arrival_seed                    INTEGER NOT NULL,

    -- Common workload configuration
    work_units                      INTEGER NOT NULL
                                        CHECK (work_units > 0),

    workload_seed                   INTEGER NOT NULL,

    warmup_runs                     INTEGER NOT NULL
                                        CHECK (warmup_runs >= 0),

    measured_runs                   INTEGER NOT NULL
                                        CHECK (measured_runs > 0),

    -- --------------------------------------------------------
    -- compute-heavy workload parameters
    -- --------------------------------------------------------

    operations_per_work_unit        INTEGER,

    -- --------------------------------------------------------
    -- BLS aggregate verify parameters
    -- --------------------------------------------------------

    validators                      INTEGER,
    signers_per_certificate         INTEGER,
    certificates_per_work_unit      INTEGER,

    -- Exact command that produced this experiment.
    command_line                    TEXT NOT NULL,

    -- Escape hatch for future workload/configuration parameters.
    -- Store JSON here if a new workload gets parameters before
    -- we decide whether they deserve dedicated columns.
    extra_config_json               TEXT,

    notes                           TEXT
);


-- ============================================================
-- Individual measured run
--
-- Warm-up runs are intentionally NOT stored here.
--
-- Every measured repetition gets one row.
-- ============================================================

CREATE TABLE IF NOT EXISTS run (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

    experiment_id               INTEGER NOT NULL
                                    REFERENCES experiment(id)
                                    ON DELETE CASCADE,

    run_index                   INTEGER NOT NULL
                                    CHECK (run_index >= 0),

    -- Physical profiling pass. NULL for historical rows.
    perf_pass                   TEXT,
    pass_run_index              INTEGER,

    -- Actual measured-run wall-clock start time.
    started_at                  TEXT DEFAULT CURRENT_TIMESTAMP,

    started_unix_ns             INTEGER
                                    CHECK (
                                        started_unix_ns IS NULL
                                        OR started_unix_ns >= 0
                                    ),

    completed_work_units        INTEGER NOT NULL
                                    CHECK (completed_work_units >= 0),

    elapsed_ns                  INTEGER NOT NULL
                                    CHECK (elapsed_ns >= 0),

    work_units_per_second       REAL NOT NULL,

    -- Allows a failed/partial run to be retained for diagnostics.
    success                     INTEGER NOT NULL DEFAULT 1
                                    CHECK (success IN (0, 1)),

    error_text                  TEXT,

    UNIQUE (experiment_id, run_index)
);


-- ============================================================
-- Raw perf capture
--
-- Stores the original perf invocation/output for reproducibility.
--
-- One capture per measured run.
-- raw_json is expected to contain perf stat JSON output.
-- ============================================================

CREATE TABLE IF NOT EXISTS perf_capture (
    run_id              INTEGER PRIMARY KEY
                            REFERENCES run(id)
                            ON DELETE CASCADE,

    perf_command        TEXT,

    raw_json            TEXT,

    perf_exit_code      INTEGER
);


-- ============================================================
-- Parsed perf metrics
--
-- Generic key/value representation means we do not need a schema
-- migration every time another PMU event is added.
--
-- Examples:
--   task-clock
--   cycles
--   instructions
--   branches
--   branch-misses
--   cache-references
--   cache-misses
--   context-switches
--   cpu-migrations
--   page-faults
--
-- Deep profiling can additionally store:
--   L1-dcache-loads
--   L1-dcache-load-misses
--   LLC-loads
--   LLC-load-misses
--   dTLB-loads
--   dTLB-load-misses
--   ...
-- ============================================================

CREATE TABLE IF NOT EXISTS perf_metric (
    run_id                  INTEGER NOT NULL
                                REFERENCES run(id)
                                ON DELETE CASCADE,

    event_name              TEXT NOT NULL,

    -- Main counter value.
    -- Nullable for unsupported / failed counters.
    counter_value           REAL,

    unit                    TEXT,

    -- Time for which this event actually ran.
    event_runtime_ns        INTEGER,

    -- Important when perf multiplexes hardware counters.
    percent_running         REAL,

    -- Optional derived metric emitted by perf.
    metric_value            REAL,
    metric_unit             TEXT,

    -- Examples:
    --   ok
    --   not-supported
    --   not-counted
    status                  TEXT NOT NULL DEFAULT 'ok',

    PRIMARY KEY (run_id, event_name)
);


-- ============================================================
-- Useful indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_benchmark_session_campaign
    ON benchmark_session(campaign_id);

CREATE INDEX IF NOT EXISTS idx_experiment_session
    ON experiment(session_id);

CREATE INDEX IF NOT EXISTS idx_experiment_scheduler
    ON experiment(scheduler);

CREATE INDEX IF NOT EXISTS idx_experiment_workload
    ON experiment(workload);

CREATE INDEX IF NOT EXISTS idx_experiment_profile
    ON experiment(profile_mode);

CREATE INDEX IF NOT EXISTS idx_experiment_workers
    ON experiment(workers);

CREATE INDEX IF NOT EXISTS idx_experiment_delivery
    ON experiment(delivery_mode);

CREATE INDEX IF NOT EXISTS idx_run_experiment
    ON run(experiment_id);

CREATE INDEX IF NOT EXISTS idx_perf_metric_event
    ON perf_metric(event_name);


-- ============================================================
-- Convenience view
--
-- Gives the common benchmark result fields together with their
-- experiment and git context.
-- ============================================================

CREATE VIEW IF NOT EXISTS benchmark_run_view AS
SELECT
    r.id                        AS run_id,
    r.run_index,
    r.pass_run_index,
    r.perf_pass,
    r.started_at                AS run_started_at,
    r.started_unix_ns,

    e.id                        AS experiment_id,
    e.scheduler,
    e.workload,
    e.profile_mode,
    e.workers,
    e.queue_capacity,
    e.threadance_spin_us,
    e.delivery_mode,
    e.arrival_window_ms,
    e.arrival_rate,
    e.arrival_seed,
    e.work_units,
    e.workload_seed,

    r.completed_work_units,
    r.elapsed_ns,
    r.work_units_per_second,

    s.id                        AS session_id,
    s.campaign_id,
    s.started_at                AS session_started_at,
    s.git_commit,
    s.git_dirty,
    s.hostname,
    s.cpu_model,
    s.logical_cpus,
    s.kernel_version

FROM run r
JOIN experiment e
    ON e.id = r.experiment_id
JOIN benchmark_session s
    ON s.id = e.session_id;
