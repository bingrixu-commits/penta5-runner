"use strict";

/**
 * Full sync: frozen state.json -> existing RDS schema (v71 import tables).
 *
 *   node --max-old-space-size=6144 import_full_existing.js --file /path/to/frozen.json
 *
 * Refuses live data/state.json unless --allow-live.
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

function toMysqlDateTime(value) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 23).replace("T", " ");
}

function pickBalls(record) {
  const nums = Array.isArray(record && record.numbers) ? record.numbers : [];
  return [0, 1, 2, 3, 4].map((i) => {
    const raw = nums[i];
    if (raw == null || raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  });
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function rateOf(total, match) {
  const t = Number(total) || 0;
  const m = Number(match) || 0;
  if (!t) return null;
  return Math.round((m / t) * 1000000) / 1000000;
}

async function importDraws(conn, history) {
  let n = 0;
  for (const batch of chunk(history, 400)) {
    for (const row of batch) {
      if (!row) continue;
      const uid = String(row.uid || row.period || "");
      const period = String(row.period || row.uid || "");
      if (!uid || !period) continue;
      const balls = pickBalls(row);
      await conn.query(
        `INSERT INTO official_draws
          (period, uid, open_time, ball1, ball2, ball3, ball4, ball5,
           ball5_display, excluded_from_model, captured_at, raw_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
         ON DUPLICATE KEY UPDATE
           open_time=VALUES(open_time),
           ball1=VALUES(ball1),
           ball2=VALUES(ball2),
           ball3=VALUES(ball3),
           ball4=VALUES(ball4),
           ball5=VALUES(ball5),
           ball5_display=VALUES(ball5_display),
           captured_at=VALUES(captured_at),
           raw_json=VALUES(raw_json)`,
        [
          period,
          uid,
          toMysqlDateTime(row.opentime),
          balls[0],
          balls[1],
          balls[2],
          balls[3],
          balls[4],
          balls[4],
          toMysqlDateTime(row.capturedAt || row.opentime),
          JSON.stringify(row),
        ]
      );
      n += 1;
    }
    process.stdout.write("\rdraws=" + n);
  }
  process.stdout.write("\n");
  return n;
}

async function importStates(conn, state, options) {
  const opts = options && typeof options === "object" ? options : {};
  const modules = [
    ["modelWatchdog", "dict", state.modelWatchdog],
    ["modelSelectionLocks", "dict", state.modelSelectionLocks],
    ["positionStrategyLocks", "dict", state.positionStrategyLocks],
    ["stable120Locks", "dict", state.stable120Locks],
    ["threeDuishuSelectionLock", "dict", state.threeDuishuSelectionLock],
    ["currentPrediction", "dict", state.currentPrediction],
    ["lastSync", "dict", state.lastSync],
    ["lastError", "dict", state.lastError],
    ["algorithmProjectHealth", "dict", state.algorithmProjectHealth],
    ["dailyStreakLedger", "dict", state.dailyStreakLedger],
    ["tipReady", "scalar", state.tipReady],
  ];
  const snaps = state && state.algorithmProjectSnapshots;
  if (snaps && typeof snaps === "object") {
    Object.keys(snaps).forEach((id) => {
      if (snaps[id] === undefined) return;
      modules.push(["algoProject:" + String(id), "dict", snaps[id]]);
    });
  } else if (opts.dataRoot) {
    try {
      const root = path.join(opts.dataRoot, "algorithm-projects");
      if (fs.existsSync(root)) {
        for (const name of fs.readdirSync(root)) {
          const file = path.join(root, name, "state.json");
          if (!fs.existsSync(file)) continue;
          try {
            modules.push(["algoProject:" + name, "dict", JSON.parse(fs.readFileSync(file, "utf8"))]);
          } catch (_) {}
        }
      }
    } catch (_) {}
  }
  let n = 0;
  for (const [key, type, body] of modules) {
    if (body === undefined) continue;
    await conn.query(
      `INSERT INTO model_states
        (state_key, state_type, ball5_display, excluded_from_model, raw_json)
       VALUES (?, ?, NULL, 1, ?)
       ON DUPLICATE KEY UPDATE
         state_type=VALUES(state_type),
         raw_json=VALUES(raw_json),
         updated_at=CURRENT_TIMESTAMP`,
      [String(key).slice(0, 191), type, JSON.stringify(body)]
    );
    n += 1;
  }
  return n;
}

async function importCounters(conn, counters, daily) {
  let n = 0;
  for (const [key, value] of Object.entries(counters || {})) {
    const ledger = value && typeof value === "object" ? value : {};
    const total = Number(ledger.total || ledger.totalCount || 0) || 0;
    const match = Number(ledger.match || ledger.matchCount || 0) || 0;
    const counterKey = String(key).startsWith("counter:")
      ? String(key)
      : "counter:" + String(key);
    await conn.query(
      `INSERT INTO model_counters
        (counter_key, counter_type, counter_day, total_count, match_count, rate_value,
         ball5_display, excluded_from_model, raw_json)
       VALUES (?, 'counter', NULL, ?, ?, ?, NULL, 1, ?)
       ON DUPLICATE KEY UPDATE
         total_count=VALUES(total_count),
         match_count=VALUES(match_count),
         rate_value=VALUES(rate_value),
         raw_json=VALUES(raw_json),
         updated_at=CURRENT_TIMESTAMP`,
      [counterKey.slice(0, 191), total, match, rateOf(total, match), JSON.stringify(ledger)]
    );
    n += 1;
  }
  for (const [name, byDay] of Object.entries(daily || {})) {
    if (!byDay || typeof byDay !== "object") continue;
    for (const [day, stats] of Object.entries(byDay)) {
      const row = stats && typeof stats === "object" ? stats : {};
      const total = Number(row.total || 0) || 0;
      const match = Number(row.match || row.hit || 0) || 0;
      const counterKey = ("daily:" + name).slice(0, 191);
      await conn.query(
        `INSERT INTO model_counters
          (counter_key, counter_type, counter_day, total_count, match_count, rate_value,
           ball5_display, excluded_from_model, raw_json)
         VALUES (?, 'daily_streak', ?, ?, ?, ?, NULL, 1, ?)
         ON DUPLICATE KEY UPDATE
           total_count=VALUES(total_count),
           match_count=VALUES(match_count),
           rate_value=VALUES(rate_value),
           raw_json=VALUES(raw_json),
           updated_at=CURRENT_TIMESTAMP`,
        [
          counterKey,
          String(day).slice(0, 32),
          total,
          match,
          rateOf(total, match),
          JSON.stringify(row),
        ]
      );
      n += 1;
    }
  }
  return n;
}

async function importPredictions(conn, predictions) {
  let predN = 0;
  let evalN = 0;
  for (const batch of chunk(predictions, 100)) {
    for (const prediction of batch) {
      if (!prediction || !prediction.id) continue;
      const predictionId = String(prediction.id).slice(0, 190);
      const balls = pickBalls({
        numbers:
          (prediction.detail && prediction.detail.numbers) ||
          prediction.modelNumbers ||
          [],
      });
      await conn.query(
        `INSERT INTO model_predictions
          (prediction_id, base_uid, base_period, model_version, generated_at,
           model_ball1, model_ball2, model_ball3, model_ball4,
           ball5_display, excluded_from_model, evaluated, counter_applied, raw_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, ?)
         ON DUPLICATE KEY UPDATE
           base_uid=VALUES(base_uid),
           base_period=VALUES(base_period),
           model_version=VALUES(model_version),
           generated_at=VALUES(generated_at),
           model_ball1=VALUES(model_ball1),
           model_ball2=VALUES(model_ball2),
           model_ball3=VALUES(model_ball3),
           model_ball4=VALUES(model_ball4),
           evaluated=VALUES(evaluated),
           counter_applied=VALUES(counter_applied),
           raw_json=VALUES(raw_json)`,
        [
          predictionId,
          prediction.baseUid || null,
          prediction.basePeriod || null,
          prediction.modelVersion || null,
          toMysqlDateTime(prediction.generatedAt) || prediction.generatedAt || null,
          balls[0],
          balls[1],
          balls[2],
          balls[3],
          prediction.evaluated ? 1 : 0,
          prediction.counterApplied ? 1 : 0,
          JSON.stringify(prediction),
        ]
      );
      predN += 1;

      const evaluation = prediction.evaluation;
      if (evaluation && typeof evaluation === "object") {
        const actual = evaluation.actualNumbers || evaluation.numbers || [];
        const ab = [0, 1, 2, 3, 4].map((i) => {
          const x = Number(actual[i]);
          return Number.isFinite(x) ? x : null;
        });
        await conn.query(
          `INSERT INTO model_evaluations
            (prediction_id, target_uid, target_period, evaluated_at,
             actual_ball1, actual_ball2, actual_ball3, actual_ball4,
             ball5_display, excluded_from_model, position_total_success, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
           ON DUPLICATE KEY UPDATE
             target_uid=VALUES(target_uid),
             target_period=VALUES(target_period),
             evaluated_at=VALUES(evaluated_at),
             actual_ball1=VALUES(actual_ball1),
             actual_ball2=VALUES(actual_ball2),
             actual_ball3=VALUES(actual_ball3),
             actual_ball4=VALUES(actual_ball4),
             ball5_display=VALUES(ball5_display),
             position_total_success=VALUES(position_total_success),
             raw_json=VALUES(raw_json)`,
          [
            predictionId,
            evaluation.targetUid || evaluation.uid || null,
            evaluation.targetPeriod || evaluation.period || null,
            toMysqlDateTime(evaluation.evaluatedAt),
            ab[0],
            ab[1],
            ab[2],
            ab[3],
            ab[4],
            evaluation.positionTotalSuccess == null
              ? null
              : evaluation.positionTotalSuccess
                ? 1
                : 0,
            JSON.stringify(evaluation),
          ]
        );
        evalN += 1;
      }
    }
    process.stdout.write("\rpredictions=" + predN + " evaluations=" + evalN);
  }
  process.stdout.write("\n");
  return { predN, evalN };
}

async function main() {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const defaultFile =
    "/root/penta5-runner/data/backups/state_full_sync_" + stamp.slice(0, 19) + ".json";
  let filePath = argValue("--file", "");
  const allowLive = hasFlag("--allow-live");
  const fromLive = hasFlag("--from-live");

  if (fromLive) {
    const live = "/root/penta5-runner/data/state.json";
    if (!fs.existsSync(live)) throw new Error("live state.json missing");
    const dest = argValue("--freeze-to", defaultFile);
    fs.copyFileSync(live, dest);
    filePath = dest;
    console.log("froze live state to " + dest);
  }

  if (!filePath) {
    throw new Error("pass --file PATH or --from-live");
  }
  filePath = path.resolve(filePath);
  if (!fs.existsSync(filePath)) throw new Error("file not found: " + filePath);
  if (filePath.replace(/\\/g, "/").endsWith("/data/state.json") && !allowLive) {
    throw new Error("refusing live state.json; use --from-live (copies first) or --allow-live");
  }

  const sizeBytes = fs.statSync(filePath).size;
  console.log("reading " + filePath + " (" + sizeBytes + " bytes)");
  const raw = fs.readFileSync(filePath, "utf8");
  const hash = sha256Text(raw);
  console.log("parsing...");
  const state = JSON.parse(raw);
  const history = Array.isArray(state.history) ? state.history : [];
  const predictions = Array.isArray(state.predictions) ? state.predictions : [];

  const pool = createPool();
  const conn = await pool.getConnection();
  try {
    await conn.query(
      `INSERT INTO system_snapshots
        (snapshot_sha256, source_path, state_version, history_count, prediction_count,
         ball5_display, excluded_from_model, raw_json)
       VALUES (?, ?, ?, ?, ?, NULL, 1, ?)
       ON DUPLICATE KEY UPDATE
         source_path=VALUES(source_path),
         state_version=VALUES(state_version),
         history_count=VALUES(history_count),
         prediction_count=VALUES(prediction_count),
         raw_json=VALUES(raw_json)`,
      [
        hash,
        filePath,
        state.version || null,
        history.length,
        predictions.length,
        JSON.stringify({
          topKeys: Object.keys(state),
          updatedAt: state.updatedAt || null,
          latestPeriod:
            (state.latestRecord && state.latestRecord.period) ||
            (history[0] && history[0].period) ||
            null,
        }),
      ]
    );

    const draws = await importDraws(conn, history);
    const dataRoot = (() => {
      const resolved = path.resolve(filePath);
      if (resolved.includes(`${path.sep}data${path.sep}`)) {
        const idx = resolved.lastIndexOf(`${path.sep}data${path.sep}`);
        return resolved.slice(0, idx + `${path.sep}data`.length);
      }
      return path.join("/root", "penta5-runner", "data");
    })();
    const states = await importStates(conn, state, { dataRoot });
    const counters = await importCounters(
      conn,
      state.counters || {},
      state.dailyStreakLedger || {}
    );
    const preds = await importPredictions(conn, predictions);

    const [[drawCount]] = await conn.query("SELECT COUNT(*) AS c FROM official_draws");
    const [[predCount]] = await conn.query("SELECT COUNT(*) AS c FROM model_predictions");
    const [[evalCount]] = await conn.query("SELECT COUNT(*) AS c FROM model_evaluations");
    const [latest] = await conn.query(
      "SELECT period, uid, open_time FROM official_draws ORDER BY open_time DESC, id DESC LIMIT 1"
    );

    console.log(
      JSON.stringify(
        {
          ok: true,
          sourceFile: filePath,
          sourceHistory: history.length,
          sourcePredictions: predictions.length,
          importedDraws: draws,
          importedStates: states,
          importedCounters: counters,
          importedPredictions: preds.predN,
          importedEvaluations: preds.evalN,
          dbDrawCount: Number(drawCount.c),
          dbPredictionCount: Number(predCount.c),
          dbEvaluationCount: Number(evalCount.c),
          latest: latest[0] || null,
          sha256: hash,
        },
        null,
        2
      )
    );
  } finally {
    conn.release();
    await pool.end();
  }
}

main().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
