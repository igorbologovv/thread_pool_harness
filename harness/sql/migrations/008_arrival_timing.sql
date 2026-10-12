BEGIN;

CREATE TABLE IF NOT EXISTS run_arrival (
    run_id                      INTEGER NOT NULL
                                    REFERENCES run(id)
                                    ON DELETE CASCADE,

    work_unit_index             INTEGER NOT NULL
                                    CHECK (work_unit_index >= 0),

    scheduled_offset_ns         INTEGER NOT NULL
                                    CHECK (scheduled_offset_ns >= 0),

    actual_submit_offset_ns     INTEGER NOT NULL
                                    CHECK (actual_submit_offset_ns >= 0),

    lateness_ns                 INTEGER NOT NULL
                                    CHECK (lateness_ns >= 0),

    PRIMARY KEY (run_id, work_unit_index)
);

INSERT OR IGNORE INTO schema_migrations (
    version,
    description
)
VALUES (
    8,
    'Record actual paced submission timing'
);

COMMIT;
