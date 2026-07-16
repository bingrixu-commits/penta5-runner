"use strict";

/**
 * Import frozen state.json backup into RDS (phase-1).
 * Usage:
 *   node --max-old-space-size=4096 import_from_state.js --file /path/to/backup.json
 *   node import_from_state.js --file ... --history-only
 *   node import_from_state.js --file ... --skip-predictions
 *
 * Never reads live mutating state.json by default; pass --allow-live to override.
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { createPool } = require("./db");

function argValue(name, fallback) {
  const idx = process.argv.indexOf(name);
  if (idx < 0) return fallback;
  return process.argv[idx + 1] || fallback;
}

function hasFlag(name) {
  return process.argv.includes(name);
}

function sha256Text(text) {
  return crypto.createHash("sha256").update(text).digest("hex");
}

function sha256Json(value) {
  return sha256Text(JSON.stringify(value));
}

function toMysqlDateTime(value) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 23).replace("T", " ");
}

function pickBalls(record) {
  const nums = Array.isArray(record && record.numbers) ? record.numbers : [];
  const balls = [0, 1, 2, 3, 4].map((i) => {
    const raw = nums[i];
    if (raw == null || raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  });
  return {
    ball1: balls[0],
    ball2: balls[1],
    ball3: balls[2],
    ball4: balls[3],
    ball5: balls[4],
  };
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

async function insertSnapshot(conn, meta) {
  const [result] = await conn.query(
    `INSERT INTO system_snapshots
      (snapshot_name, state_version, source_path, captured_at, source_size_bytes, sha256,
       payload_format, external_backup_path, top_keys_json, history_count, prediction_count, import_status)
     VALUES (?, ?, ?, ?, ?, ?, 'json', ?, CAST(? AS JSON), ?, ?, 'pending')
     ON DUPLICATE KEY UPDATE
       id=LAST_INSERT_ID(id),
       state_version=VALUES(state_version),
       history_count=VALUES(history_count),
       prediction_count=VALUES(prediction_count),
       import_status='pending'`,
    [
      meta.snapshotName,
      meta.stateVersion,
      meta.sourcePath,
      meta.capturedAt,
      meta.sizeBytes,
      meta.sha256,
      meta.sourcePath,
      JSON.stringify(meta.topKeys),
      meta.historyCount,
      meta.predictionCount,
    ]
  );
  return result.insertId;
}

async function importHistory(conn, history, snapshotId, batchSize) {
  let imported = 0;
  let skipped = 0;
  for (const batch of chunk(history, batchSize)) {
    const values = [];
    const params = [];
    for (const row of batch) {
      if (!row || (!row.uid && !row.period)) {
        skipped += 1;
        continue;
      }
      const uid = String(row.uid || row.period || "");
      const period = String(row.period || row.uid || "");
      if (!uid || !period) {
        skipped += 1;
        continue;
      }
      const balls = pickBalls(row);
      values.push("(?,?,?,?,?,?,?,?,CAST(? AS JSON),CAST(? AS JSON),CAST(? AS JSON),?,?,?)");
      params.push(
        uid,
        period,
        toMysqlDateTime(row.opentime),
        balls.ball1,
        balls.ball2,
        balls.ball3,
        balls.ball4,
        balls.ball5,
        row.positions ? JSON.stringify(row.positions) : null,
        row.metrics ? JSON.stringify(row.metrics) : null,
        JSON.stringify(row),
        toMysqlDateTime(row.capturedAt || row.opentime),
        snapshotId,
        toMysqlDateTime(new Date().toISOString())
      );
    }
    if (!values.length) continue;
    await conn.query(
      `INSERT INTO official_draws
        (uid, period, open_time, ball1, ball2, ball3, ball4, ball5,
         positions_json, metrics_json, raw_json, captured_at, source_snapshot_id, created_at)
       VALUES ${values.join(",")}
       ON DUPLICATE KEY UPDATE
         open_time=VALUES(open_time),
         ball1=VALUES(ball1),
         ball2=VALUES(ball2),
         ball3=VALUES(ball3),
         ball4=VALUES(ball4),
         ball5=VALUES(ball5),
         positions_json=VALUES(positions_json),
         metrics_json=VALUES(metrics_json),
         raw_json=VALUES(raw_json),
         captured_at=VALUES(captured_at),
         source_snapshot_id=VALUES(source_snapshot_id)`,
      params
    );
    imported += values.length;
    process.stdout.write(`\rhistory imported=${imported} skipped=${skipped}`);
  }
  process.stdout.write("\n");
  return { imported, skipped };
}

async function upsertState(conn, modelKey, stateType, body, snapshotId, modelVersion, basePeriod) {
  const stateJson = JSON.stringify(body == null ? null : body);
  const hash = sha256Text(`${modelKey}|${stateType}|${stateJson}`);
  await conn.query(
    `INSERT INTO model_states
      (model_key, state_type, model_version, base_period, state_json, state_hash, recorded_at, is_current, source_snapshot_id)
     VALUES (?, ?, ?, ?, CAST(? AS JSON), ?, ?, 1, ?)
     ON DUPLICATE KEY UPDATE
       state_json=VALUES(state_json),
       model_version=VALUES(model_version),
       base_period=VALUES(base_period),
       recorded_at=VALUES(recorded_at),
       is_current=1,
       source_snapshot_id=VALUES(source_snapshot_id)`,
    [
      modelKey,
      stateType,
      modelVersion || null,
      basePeriod || null,
      stateJson,
      hash,
      toMysqlDateTime(new Date().toISOString()),
      snapshotId,
    ]
  );
}

async function importModelStates(conn, state, snapshotId) {
  const version = state.version || null;
  const basePeriod =
    (state.latestRecord && (state.latestRecord.period || state.latestRecord.uid)) || null;
  const modules = [
    ["currentPrediction", "prediction", state.currentPrediction],
    ["modelWatchdog", "watchdog", state.modelWatchdog],
    ["modelSelectionLocks", "lock", state.modelSelectionLocks],
    ["positionStrategyLocks", "lock", state.positionStrategyLocks],
    ["stable120Locks", "lock", state.stable120Locks],
    ["threeDuishuSelectionLock", "lock", state.threeDuishuSelectionLock],
    ["lastSync", "system", state.lastSync],
    ["lastError", "system", state.lastError],
    ["rawJson", "system", state.rawJson],
  ];
  let count = 0;
  for (const [key, type, body] of modules) {
    if (body === undefined) continue;
    await upsertState(conn, key, type, body, snapshotId, version, basePeriod);
    count += 1;
  }
  return count;
}

async function importCounters(conn, counters, snapshotId) {
  let count = 0;
  const entries = Object.entries(counters || {});
  for (const batch of chunk(entries, 200)) {
    for (const [key, value] of batch) {
      const ledger = value && typeof value === "object" ? value : {};
      await conn.query(
        `INSERT INTO model_counters
          (ledger_type, counter_key, scope_key, label, total_count, match_count, main_match_count,
           current_win_streak, current_miss_streak, max_win_streak, max_miss_streak,
           ledger_json, updated_at, source_snapshot_id)
         VALUES ('counter', ?, 'all', ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?)
         ON DUPLICATE KEY UPDATE
           label=VALUES(label),
           total_count=VALUES(total_count),
           match_count=VALUES(match_count),
           main_match_count=VALUES(main_match_count),
           current_win_streak=VALUES(current_win_streak),
           current_miss_streak=VALUES(current_miss_streak),
           max_win_streak=VALUES(max_win_streak),
           max_miss_streak=VALUES(max_miss_streak),
           ledger_json=VALUES(ledger_json),
           updated_at=VALUES(updated_at),
           source_snapshot_id=VALUES(source_snapshot_id)`,
        [
          String(key).slice(0, 191),
          ledger.label || key,
          Number(ledger.total || ledger.totalCount || 0) || 0,
          Number(ledger.match || ledger.matchCount || 0) || 0,
          Number(ledger.mainMatch || ledger.mainMatchCount || 0) || 0,
          Number(ledger.currentWin || ledger.currentWinStreak || 0) || 0,
          Number(ledger.currentMiss || ledger.currentMissStreak || 0) || 0,
          Number(ledger.maxWin || ledger.maxWinStreak || 0) || 0,
          Number(ledger.maxMiss || ledger.maxMissStreak || 0) || 0,
          JSON.stringify(ledger),
          toMysqlDateTime(ledger.updatedAt || new Date().toISOString()),
          snapshotId,
        ]
      );
      count += 1;
    }
  }
  return count;
}

async function importDailyStreak(conn, ledger, snapshotId) {
  let count = 0;
  for (const [name, byDay] of Object.entries(ledger || {})) {
    if (!byDay || typeof byDay !== "object") continue;
    for (const [day, stats] of Object.entries(byDay)) {
      const row = stats && typeof stats === "object" ? stats : {};
      await conn.query(
        `INSERT INTO model_counters
          (ledger_type, counter_key, scope_key, label, total_count, match_count, main_match_count,
           current_win_streak, current_miss_streak, max_win_streak, max_miss_streak,
           ledger_json, updated_at, source_snapshot_id)
         VALUES ('daily_streak', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?)
         ON DUPLICATE KEY UPDATE
           label=VALUES(label),
           total_count=VALUES(total_count),
           match_count=VALUES(match_count),
           main_match_count=VALUES(main_match_count),
           current_win_streak=VALUES(current_win_streak),
           current_miss_streak=VALUES(current_miss_streak),
           max_win_streak=VALUES(max_win_streak),
           max_miss_streak=VALUES(max_miss_streak),
           ledger_json=VALUES(ledger_json),
           updated_at=VALUES(updated_at),
           source_snapshot_id=VALUES(source_snapshot_id)`,
        [
          String(name).slice(0, 191),
          String(day).slice(0, 32),
          name,
          Number(row.total || 0) || 0,
          Number(row.match || row.hit || 0) || 0,
          Number(row.mainMatch || 0) || 0,
          Number(row.currentWin || 0) || 0,
          Number(row.currentMiss || 0) || 0,
          Number(row.maxWin || 0) || 0,
          Number(row.maxMiss || 0) || 0,
          JSON.stringify(row),
          toMysqlDateTime(row.updatedAt || new Date().toISOString()),
          snapshotId,
        ]
      );
      count += 1;
    }
  }
  return count;
}

function bool01(v) {
  return v ? 1 : 0;
}

async function importPredictions(conn, predictions, snapshotId, batchSize) {
  let predCount = 0;
  let evalCount = 0;
  for (const batch of chunk(predictions, batchSize)) {
    for (const prediction of batch) {
      if (!prediction || !prediction.id) continue;
      const predictionId = String(prediction.id).slice(0, 128);
      const evaluation = prediction.evaluation || null;
      await conn.query(
        `INSERT INTO model_predictions
          (prediction_id, model_version, base_uid, base_period, base_open_time, generated_at,
           evaluated, counter_applied, prediction_json, detail_json, auto_top6_json,
           recommend2_json, cold_framework_json, source_snapshot_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), CAST(? AS JSON), CAST(? AS JSON),
                 CAST(? AS JSON), CAST(? AS JSON), ?)
         ON DUPLICATE KEY UPDATE
           model_version=VALUES(model_version),
           base_uid=VALUES(base_uid),
           base_period=VALUES(base_period),
           base_open_time=VALUES(base_open_time),
           generated_at=VALUES(generated_at),
           evaluated=VALUES(evaluated),
           counter_applied=VALUES(counter_applied),
           prediction_json=VALUES(prediction_json),
           detail_json=VALUES(detail_json),
           auto_top6_json=VALUES(auto_top6_json),
           recommend2_json=VALUES(recommend2_json),
           cold_framework_json=VALUES(cold_framework_json),
           source_snapshot_id=VALUES(source_snapshot_id)`,
        [
          predictionId,
          prediction.modelVersion || null,
          prediction.baseUid || null,
          prediction.basePeriod || null,
          toMysqlDateTime(prediction.baseOpenTime),
          toMysqlDateTime(prediction.generatedAt),
          bool01(prediction.evaluated),
          bool01(prediction.counterApplied),
          JSON.stringify(prediction),
          prediction.detail ? JSON.stringify(prediction.detail) : null,
          prediction.autoTop6 || prediction.autoTop6Items
            ? JSON.stringify(prediction.autoTop6 || prediction.autoTop6Items)
            : null,
          prediction.recommend2 || prediction.recommend2Items
            ? JSON.stringify(prediction.recommend2 || prediction.recommend2Items)
            : null,
          prediction.coldFramework || prediction.coldFrameworkItems
            ? JSON.stringify(prediction.coldFramework || prediction.coldFrameworkItems)
            : null,
          snapshotId,
        ]
      );
      predCount += 1;

      if (evaluation && typeof evaluation === "object") {
        const actual = evaluation.actualNumbers || evaluation.numbers || [];
        const balls = [0, 1, 2, 3, 4].map((i) => {
          const n = Number(actual[i]);
          return Number.isFinite(n) ? n : null;
        });
        const resultHash = sha256Json({
          predictionId,
          targetUid: evaluation.targetUid || evaluation.uid || null,
          targetPeriod: evaluation.targetPeriod || evaluation.period || null,
          evaluation,
        });
        await conn.query(
          `INSERT INTO model_evaluations
            (prediction_id, target_uid, target_period, evaluated_at,
             actual_ball1, actual_ball2, actual_ball3, actual_ball4, actual_ball5,
             auto_top6_success, recommend2_success, cold_framework_success,
             evaluation_json, result_hash, source_snapshot_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?)
           ON DUPLICATE KEY UPDATE
             target_uid=VALUES(target_uid),
             target_period=VALUES(target_period),
             evaluated_at=VALUES(evaluated_at),
             actual_ball1=VALUES(actual_ball1),
             actual_ball2=VALUES(actual_ball2),
             actual_ball3=VALUES(actual_ball3),
             actual_ball4=VALUES(actual_ball4),
             actual_ball5=VALUES(actual_ball5),
             auto_top6_success=VALUES(auto_top6_success),
             recommend2_success=VALUES(recommend2_success),
             cold_framework_success=VALUES(cold_framework_success),
             evaluation_json=VALUES(evaluation_json),
             result_hash=VALUES(result_hash),
             source_snapshot_id=VALUES(source_snapshot_id)`,
          [
            predictionId,
            evaluation.targetUid || evaluation.uid || null,
            evaluation.targetPeriod || evaluation.period || null,
            toMysqlDateTime(evaluation.evaluatedAt),
            balls[0],
            balls[1],
            balls[2],
            balls[3],
            balls[4],
            evaluation.autoTop6Success == null ? null : bool01(evaluation.autoTop6Success),
            evaluation.recommend2Success == null ? null : bool01(evaluation.recommend2Success),
            evaluation.coldFrameworkSuccess == null
              ? null
              : bool01(evaluation.coldFrameworkSuccess),
            JSON.stringify(evaluation),
            resultHash,
            snapshotId,
          ]
        );
        evalCount += 1;
      }
    }
    process.stdout.write(`\rpredictions=${predCount} evaluations=${evalCount}`);
  }
  process.stdout.write("\n");
  return { predCount, evalCount };
}

async function main() {
  const defaultBackup =
    "/root/penta5-runner/data/backups/state_before_rds_import_20260712_203239.json";
  const filePath = path.resolve(argValue("--file", defaultBackup));
  const batchSize = Math.max(50, Number(argValue("--batch", "500")) || 500);
  const historyOnly = hasFlag("--history-only");
  const skipPredictions = hasFlag("--skip-predictions") || historyOnly;
  const allowLive = hasFlag("--allow-live");

  if (!fs.existsSync(filePath)) {
    throw new Error(`state file not found: ${filePath}`);
  }
  if (filePath.replace(/\\/g, "/").endsWith("/data/state.json") && !allowLive) {
    throw new Error("Refusing live state.json; pass a frozen backup or --allow-live");
  }

  const sizeBytes = fs.statSync(filePath).size;
  console.log(`reading ${filePath} (${sizeBytes} bytes)...`);
  const raw = fs.readFileSync(filePath, "utf8");
  const fileHash = sha256Text(raw);
  console.log("parsing JSON...");
  const state = JSON.parse(raw);

  const history = Array.isArray(state.history) ? state.history : [];
  const predictions = Array.isArray(state.predictions) ? state.predictions : [];
  const topKeys = Object.keys(state);
  const snapshotName = `import:${path.basename(filePath)}:${fileHash.slice(0, 12)}`;

  const pool = createPool();
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    const snapshotId = await insertSnapshot(conn, {
      snapshotName,
      stateVersion: state.version || null,
      sourcePath: filePath,
      capturedAt: toMysqlDateTime(state.updatedAt || state.createdAt || new Date().toISOString()),
      sizeBytes,
      sha256: fileHash,
      topKeys,
      historyCount: history.length,
      predictionCount: predictions.length,
    });
    console.log(`snapshot_id=${snapshotId}`);

    const hist = await importHistory(conn, history, snapshotId, batchSize);
    console.log(`history done: ${JSON.stringify(hist)}`);

    let states = 0;
    let counters = 0;
    let streaks = 0;
    let preds = { predCount: 0, evalCount: 0 };
    if (!historyOnly) {
      states = await importModelStates(conn, state, snapshotId);
      counters = await importCounters(conn, state.counters || {}, snapshotId);
      streaks = await importDailyStreak(conn, state.dailyStreakLedger || {}, snapshotId);
      console.log(`states=${states} counters=${counters} daily_streak_rows=${streaks}`);
      if (!skipPredictions) {
        preds = await importPredictions(conn, predictions, snapshotId, Math.min(batchSize, 100));
      }
    }

    await conn.query(
      `UPDATE system_snapshots SET import_status='verified', history_count=?, prediction_count=? WHERE id=?`,
      [history.length, predictions.length, snapshotId]
    );
    await conn.commit();

    const [[drawCount]] = await conn.query("SELECT COUNT(*) AS c FROM official_draws");
    const [[latest]] = await conn.query(
      "SELECT period, uid FROM official_draws ORDER BY open_time DESC, id DESC LIMIT 1"
    );
    console.log(
      JSON.stringify(
        {
          ok: true,
          snapshotId,
          historyImported: hist.imported,
          drawCount: Number(drawCount.c || 0),
          latestPeriod: latest && latest.period,
          latestUid: latest && latest.uid,
          modelStates: states,
          counters,
          dailyStreakRows: streaks,
          predictions: preds.predCount,
          evaluations: preds.evalCount,
          skippedPredictions: skipPredictions,
        },
        null,
        2
      )
    );
  } catch (err) {
    try {
      await conn.rollback();
    } catch (_) {}
    throw err;
  } finally {
    conn.release();
    await pool.end();
  }
}

main().catch((err) => {
  console.error(String(err && err.message ? err.message : err));
  process.exit(1);
});
