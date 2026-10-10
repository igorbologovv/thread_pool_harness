PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

ALTER TABLE benchmark_session
ADD COLUMN campaign_id TEXT;

CREATE INDEX IF NOT EXISTS idx_benchmark_session_campaign
    ON benchmark_session(campaign_id);

DROP VIEW IF EXISTS benchmark_run_view;

CREATE VIEW benchmark_run_view AS
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

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    7,
    'Group benchmark sessions into campaigns'
);

COMMIT;
