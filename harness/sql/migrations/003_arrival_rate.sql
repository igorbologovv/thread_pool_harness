ALTER TABLE experiment
ADD COLUMN arrival_rate REAL
    CHECK (
        arrival_rate IS NULL
        OR arrival_rate > 0
    );

INSERT OR IGNORE INTO schema_migrations (
    version,
    description
) VALUES (
    3,
    'Add mean arrival rate to benchmark experiments'
);

DROP VIEW IF EXISTS benchmark_run_view;

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
