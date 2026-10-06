PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

ALTER TABLE run
    ADD COLUMN perf_pass TEXT;

ALTER TABLE run
    ADD COLUMN pass_run_index INTEGER;

UPDATE run
SET
    perf_pass = CASE
        WHEN (
            SELECT profile_mode
            FROM experiment
            WHERE experiment.id = run.experiment_id
        ) = 'deep'
        THEN 'legacy-deep'

        ELSE (
            SELECT profile_mode
            FROM experiment
            WHERE experiment.id = run.experiment_id
        )
    END,

    pass_run_index = run_index;

DROP VIEW IF EXISTS benchmark_run_view;

CREATE VIEW benchmark_run_view AS
SELECT
    r.id AS run_id,
    r.run_index,
    r.pass_run_index,
    r.perf_pass,

    e.id AS experiment_id,
    e.scheduler,
    e.workload,
    e.profile_mode,
    e.workers,
    e.queue_capacity,
    e.delivery_mode,
    e.arrival_window_ms,
    e.arrival_seed,
    e.work_units,
    e.workload_seed,

    r.completed_work_units,
    r.elapsed_ns,
    r.work_units_per_second,

    s.id AS session_id,
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

DELETE FROM schema_migrations
WHERE version = 2;

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    2,
    'Record physical perf pass for multi-pass deep profiling'
);

COMMIT;
