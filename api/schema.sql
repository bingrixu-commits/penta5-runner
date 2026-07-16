-- Penta5 RDS schema: existing-v71（与线上阿里 RDS 一致）
-- 警告：本文件必须与 dump_ddl.js / probe_schema.js 输出对齐。
-- 切勿用旧 phase-1（model_key / ledger_type）DDL 覆盖线上库。
-- Ball5 仅展示（ball5_display / excluded_from_model=1），模型只读 ball1-ball4。

CREATE TABLE IF NOT EXISTS official_draws (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  period VARCHAR(64) NOT NULL,
  uid VARCHAR(191) NULL,
  open_time VARCHAR(64) NULL,
  ball1 TINYINT UNSIGNED NULL,
  ball2 TINYINT UNSIGNED NULL,
  ball3 TINYINT UNSIGNED NULL,
  ball4 TINYINT UNSIGNED NULL,
  ball5 TINYINT UNSIGNED NULL,
  ball5_display VARCHAR(16) NULL,
  excluded_from_model TINYINT(1) NOT NULL DEFAULT 1,
  captured_at VARCHAR(64) NULL,
  raw_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_official_draws_period (period),
  KEY ix_official_draws_uid (uid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS model_predictions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  prediction_id VARCHAR(191) NOT NULL,
  base_uid VARCHAR(191) NULL,
  base_period VARCHAR(64) NULL,
  model_version VARCHAR(191) NULL,
  generated_at VARCHAR(64) NULL,
  model_ball1 TINYINT UNSIGNED NULL,
  model_ball2 TINYINT UNSIGNED NULL,
  model_ball3 TINYINT UNSIGNED NULL,
  model_ball4 TINYINT UNSIGNED NULL,
  ball5_display VARCHAR(16) NULL,
  excluded_from_model TINYINT(1) NOT NULL DEFAULT 1,
  evaluated TINYINT(1) NULL,
  counter_applied TINYINT(1) NULL,
  raw_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_model_predictions_id (prediction_id),
  KEY ix_model_predictions_period (base_period)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS model_evaluations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  prediction_id VARCHAR(191) NOT NULL,
  target_uid VARCHAR(191) NULL,
  target_period VARCHAR(64) NULL,
  evaluated_at VARCHAR(64) NULL,
  actual_ball1 TINYINT UNSIGNED NULL,
  actual_ball2 TINYINT UNSIGNED NULL,
  actual_ball3 TINYINT UNSIGNED NULL,
  actual_ball4 TINYINT UNSIGNED NULL,
  ball5_display VARCHAR(16) NULL,
  excluded_from_model TINYINT(1) NOT NULL DEFAULT 1,
  position_total_success TINYINT(1) NULL,
  raw_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_model_evaluations_prediction (prediction_id),
  KEY ix_model_evaluations_period (target_period)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS model_counters (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  counter_key VARCHAR(191) NOT NULL,
  counter_type VARCHAR(64) NOT NULL,
  counter_day VARCHAR(16) NULL,
  total_count BIGINT NULL,
  match_count BIGINT NULL,
  rate_value DECIMAL(12,6) NULL,
  ball5_display VARCHAR(16) NULL,
  excluded_from_model TINYINT(1) NOT NULL DEFAULT 1,
  raw_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_model_counters_key (counter_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS model_states (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  state_key VARCHAR(191) NOT NULL,
  state_type VARCHAR(64) NOT NULL,
  ball5_display VARCHAR(16) NULL,
  excluded_from_model TINYINT(1) NOT NULL DEFAULT 1,
  raw_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_model_states_key (state_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS system_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_sha256 CHAR(64) NOT NULL,
  source_path VARCHAR(255) NOT NULL,
  state_version VARCHAR(191) NULL,
  history_count BIGINT NOT NULL,
  prediction_count BIGINT NOT NULL,
  ball5_display VARCHAR(16) NULL,
  excluded_from_model TINYINT(1) NOT NULL DEFAULT 1,
  raw_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_system_snapshots_sha (snapshot_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
