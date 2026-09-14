CREATE TABLE IF NOT EXISTS studio_segment_notes (
 id VARCHAR(64) PRIMARY KEY, revision_id VARCHAR(64) NOT NULL, segment_id VARCHAR(64) NOT NULL,
 version INT NOT NULL, notes TEXT NOT NULL, carried_from VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), UNIQUE(revision_id,segment_id,version),
 FOREIGN KEY(revision_id) REFERENCES studio_revisions(id)
);
