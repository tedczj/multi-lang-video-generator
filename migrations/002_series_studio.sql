CREATE TABLE IF NOT EXISTS studio_series (
 id VARCHAR(64) PRIMARY KEY, name VARCHAR(240) NOT NULL, brand VARCHAR(240) NOT NULL,
 archived BOOLEAN NOT NULL DEFAULT FALSE, created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6))
);
CREATE TABLE IF NOT EXISTS studio_characters (
 id VARCHAR(64) PRIMARY KEY, series_id VARCHAR(64) NOT NULL, name VARCHAR(240) NOT NULL,
 notes TEXT NOT NULL, active_profile_id VARCHAR(64) NULL, archived BOOLEAN NOT NULL DEFAULT FALSE,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), INDEX(series_id),
 FOREIGN KEY(series_id) REFERENCES studio_series(id)
);
CREATE TABLE IF NOT EXISTS studio_episodes (
 id VARCHAR(64) PRIMARY KEY, series_id VARCHAR(64) NOT NULL, title VARCHAR(240) NOT NULL,
 source_url TEXT NOT NULL, asset_sha512 CHAR(128) CHARACTER SET ascii NULL,
 source_artifact_id VARCHAR(64) NULL, active_revision_id VARCHAR(64) NULL,
 settings_json JSON NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)),
 INDEX(series_id), FOREIGN KEY(series_id) REFERENCES studio_series(id),
 FOREIGN KEY(asset_sha512) REFERENCES assets(sha512), FOREIGN KEY(source_artifact_id) REFERENCES artifacts(id)
);
CREATE TABLE IF NOT EXISTS studio_revisions (
 id VARCHAR(64) PRIMARY KEY, episode_id VARCHAR(64) NOT NULL, version INT NOT NULL,
 payload_json JSON NOT NULL, payload_sha512 CHAR(128) CHARACTER SET ascii NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), UNIQUE(episode_id,version),
 FOREIGN KEY(episode_id) REFERENCES studio_episodes(id)
);
CREATE TABLE IF NOT EXISTS studio_annotations (
 id VARCHAR(64) PRIMARY KEY, revision_id VARCHAR(64) NOT NULL, segment_id VARCHAR(64) NOT NULL,
 version INT NOT NULL, character_id VARCHAR(64) NULL, status VARCHAR(24) NOT NULL,
 english_text TEXT NOT NULL, chinese_text TEXT NOT NULL, reviewer VARCHAR(240) NOT NULL,
 reason TEXT NOT NULL, carried_from VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), UNIQUE(revision_id,segment_id,version),
 FOREIGN KEY(revision_id) REFERENCES studio_revisions(id), FOREIGN KEY(character_id) REFERENCES studio_characters(id)
);
CREATE TABLE IF NOT EXISTS studio_suggestions (
 id VARCHAR(64) PRIMARY KEY, revision_id VARCHAR(64) NOT NULL, segment_id VARCHAR(64) NOT NULL,
 character_id VARCHAR(64) NULL, version INT NOT NULL, payload_json JSON NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), UNIQUE(revision_id,segment_id,version),
 FOREIGN KEY(revision_id) REFERENCES studio_revisions(id), FOREIGN KEY(character_id) REFERENCES studio_characters(id)
);
CREATE TABLE IF NOT EXISTS studio_references (
 id VARCHAR(64) PRIMARY KEY, character_id VARCHAR(64) NOT NULL, revision_id VARCHAR(64) NOT NULL,
 payload_json JSON NOT NULL, payload_sha512 CHAR(128) CHARACTER SET ascii NOT NULL,
 state VARCHAR(24) NOT NULL, artifacts_json JSON NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), INDEX(character_id),
 FOREIGN KEY(character_id) REFERENCES studio_characters(id), FOREIGN KEY(revision_id) REFERENCES studio_revisions(id)
);
CREATE TABLE IF NOT EXISTS studio_profiles (
 id VARCHAR(64) PRIMARY KEY, character_id VARCHAR(64) NOT NULL, reference_id VARCHAR(64) NOT NULL,
 version INT NOT NULL, label VARCHAR(240) NOT NULL, payload_json JSON NOT NULL,
 payload_sha512 CHAR(128) CHARACTER SET ascii NOT NULL, state VARCHAR(24) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), UNIQUE(character_id,version),
 FOREIGN KEY(character_id) REFERENCES studio_characters(id), FOREIGN KEY(reference_id) REFERENCES studio_references(id)
);
CREATE TABLE IF NOT EXISTS studio_plans (
 id VARCHAR(64) PRIMARY KEY, episode_id VARCHAR(64) NOT NULL, revision_id VARCHAR(64) NOT NULL,
 payload_json JSON NOT NULL, payload_sha512 CHAR(128) CHARACTER SET ascii NOT NULL,
 bindings_json JSON NULL, state VARCHAR(24) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), INDEX(episode_id),
 FOREIGN KEY(episode_id) REFERENCES studio_episodes(id), FOREIGN KEY(revision_id) REFERENCES studio_revisions(id)
);
CREATE TABLE IF NOT EXISTS studio_jobs (
 id VARCHAR(64) PRIMARY KEY, episode_id VARCHAR(64) NULL, kind VARCHAR(40) NOT NULL,
 state VARCHAR(24) NOT NULL, payload_json JSON NOT NULL, result_json JSON NULL,
 error_text TEXT NULL, retry_of VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), started_at DATETIME(6) NULL, finished_at DATETIME(6) NULL,
 INDEX(state,created_at), INDEX(episode_id,created_at),
 FOREIGN KEY(episode_id) REFERENCES studio_episodes(id), FOREIGN KEY(retry_of) REFERENCES studio_jobs(id)
);
CREATE TABLE IF NOT EXISTS studio_clip_selections (
 id VARCHAR(64) PRIMARY KEY, plan_id VARCHAR(64) NOT NULL, unit_id VARCHAR(64) NOT NULL,
 job_id VARCHAR(64) NOT NULL, version INT NOT NULL, payload_json JSON NOT NULL,
 reviewer VARCHAR(240) NOT NULL, reason TEXT NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), UNIQUE(plan_id,unit_id,version),
 FOREIGN KEY(plan_id) REFERENCES studio_plans(id), FOREIGN KEY(job_id) REFERENCES studio_jobs(id)
);
CREATE TABLE IF NOT EXISTS studio_events (
 id VARCHAR(64) PRIMARY KEY, aggregate_id VARCHAR(64) NOT NULL, action VARCHAR(64) NOT NULL,
 payload_json JSON NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)), INDEX(aggregate_id,created_at)
);
