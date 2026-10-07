PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

DROP VIEW IF EXISTS benchmark_run_view;

CREATE TABLE experiment_new (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    session_id                      INTEGER NOT NULL
                                        REFERENCES benchmark_session(id)
                                        ON DELETE CASCADE,

    created_at                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

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

    queue_capacity                  INTEGER,

    threadance_spin_us              INTEGER
                                        CHECK (
                                            threadance_spin_us IS NULL
                                            OR threadance_spin_us >= 0
                                        ),

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

    arrival_window_ms               INTEGER,

    arrival_rate                    REAL
                                        CHECK (
                                            arrival_rate IS NULL
                                            OR arrival_rate > 0
                                        ),

    arrival_seed                    INTEGER NOT NULL,

    work_units                      INTEGER NOT NULL
                                        CHECK (work_units > 0),

    workload_seed                   INTEGER NOT NULL,

    warmup_runs                     INTEGER NOT NULL
                                        CHECK (warmup_runs >= 0),

    measured_runs                   INTEGER NOT NULL
                                        CHECK (measured_runs > 0),

    operations_per_work_unit        INTEGER,

    validators                      INTEGER,
    signers_per_certificate         INTEGER,
    certificates_per_work_unit      INTEGER,

    command_line                    TEXT NOT NULL,

    extra_config_json               TEXT,
    notes                           TEXT
);

INSERT INTO experiment_new (
    id,
    session_id,
    created_at,
    scheduler,
    workload,
    profile_mode,
    workers,
    queue_capacity,
    threadance_spin_us,
    delivery_mode,
    arrival_window_ms,
    arrival_rate,
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
SELECT
    id,
    session_id,
    created_at,
    scheduler,
    workload,
    profile_mode,
    workers,
    queue_capacity,
    threadance_spin_us,
    delivery_mode,
    arrival_window_ms,
    arrival_rate,
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
FROM experiment;

DROP TABLE experiment;

ALTER TABLE experiment_new RENAME TO experiment;

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

INSERT INTO schema_migrations (
    version,
    description
) VALUES (
    5,
    'Allow deterministic fixed-arrivals delivery mode'
);

CREATE VIEW benchmark_run_view AS
SELECT
    r.id                        AS run_id,
    r.run_index,

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

COMMIT;

PRAGMA foreign_keys = ON;
