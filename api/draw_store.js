"use strict";

/**
 * Single-draw upsert into official_draws (RDS primary write path).
 * Used by penta5_backend_runner syncNow so new periods hit RDS before local history hydrate.
 * Retains latest RDS_HISTORY_KEEP (default MAX_HISTORY/5000) draws and drops orphan preds/evals.
 * Pred/eval retention: latest RDS_PRED_HISTORY_KEEP draw periods, preserving
 * every immutable prediction_id inside that window.
 */

const { createPool } = require("./db");

const RDS_HISTORY_KEEP = Math.max(
  1,
  Number(process.env.RDS_HISTORY_KEEP || process.env.MAX_HISTORY || 5000) || 5000
);

/** Keep predictions/evaluations only for newest N draw periods (default 3000; draws may keep 5000). */
const RDS_PRED_HISTORY_KEEP = Math.max(
  1,
  Number(process.env.RDS_PRED_HISTORY_KEEP || 3000) || 3000
);

let pool = null;

function getPool() {
  if (!pool) pool = createPool();
  return pool;
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

function historyKeepLimit(keep) {
  const n = Number(keep == null ? RDS_HISTORY_KEEP : keep);
  return Math.max(1, Number.isFinite(n) ? Math.floor(n) : RDS_HISTORY_KEEP);
}

function predHistoryKeepLimit() {
  const n = Number(RDS_PRED_HISTORY_KEEP);
  return Math.max(1, Number.isFinite(n) ? Math.floor(n) : 3000);
}

/**
 * Keep newest `keep` official_draws; delete older rows.
 * Also trim model_predictions / model_evaluations:
 *  - orphan periods not in draws
 *  - outside newest RDS_PRED_HISTORY_KEEP draw periods
 *  - keep distinct prediction_ids for the same period: an insertion id cannot
 *    determine which pre-draw sealed original must survive
 *  - evals whose prediction_id no longer exists
 * @param {number} [keep]
 * @returns {Promise<object>}
 */
async function pruneOfficialDraws(keep) {
  const limit = historyKeepLimit(keep);
  const predKeep = predHistoryKeepLimit();
  const conn = await getPool().getConnection();
  try {
    const [[row]] = await conn.query("SELECT COUNT(*) AS c FROM official_draws");
    const count = Number(row && row.c || 0);
    let deleted = 0;
    if (count > limit) {
      const [result] = await conn.query(
        `DELETE d FROM official_draws d
         LEFT JOIN (
           SELECT id FROM official_draws
           ORDER BY open_time DESC, id DESC
           LIMIT ?
         ) keep_ids ON keep_ids.id = d.id
         WHERE keep_ids.id IS NULL`,
        [limit]
      );
      deleted = Number(result && result.affectedRows || 0);
    }

    let predictionsDeleted = 0;
    let evaluationsDeleted = 0;

    // Orphans vs current draws
    const [evalOrphan] = await conn.query(
      `DELETE FROM model_evaluations
       WHERE target_period IS NOT NULL
         AND target_period <> ''
         AND target_period NOT IN (SELECT period FROM official_draws)`
    );
    evaluationsDeleted += Number(evalOrphan && evalOrphan.affectedRows || 0);

    const [predOrphan] = await conn.query(
      `DELETE FROM model_predictions
       WHERE base_period IS NOT NULL
         AND base_period <> ''
         AND base_period NOT IN (SELECT period FROM official_draws)`
    );
    predictionsDeleted += Number(predOrphan && predOrphan.affectedRows || 0);

    // Window: only newest predKeep draw periods
    const [predWindow] = await conn.query(
      `DELETE FROM model_predictions
       WHERE base_period IS NOT NULL
         AND base_period <> ''
         AND base_period NOT IN (
           SELECT period FROM (
             SELECT period FROM official_draws
             ORDER BY open_time DESC, id DESC
             LIMIT ?
           ) keep_periods
         )`,
      [predKeep]
    );
    predictionsDeleted += Number(predWindow && predWindow.affectedRows || 0);

    const [evalWindow] = await conn.query(
      `DELETE FROM model_evaluations
       WHERE target_period IS NOT NULL
         AND target_period <> ''
         AND target_period NOT IN (
           SELECT period FROM (
             SELECT period FROM official_draws
             ORDER BY open_time DESC, id DESC
             LIMIT ?
           ) keep_periods
         )`,
      [predKeep]
    );
    evaluationsDeleted += Number(evalWindow && evalWindow.affectedRows || 0);

    // A later mirror can reinsert a slim row with a larger database id.
    // Never let it delete another prediction_id's signed original or settlement.
    const predDedupeDeleted = 0;

    // Drop evals pointing at deleted prediction_ids
    const [evalDangling] = await conn.query(
      `DELETE FROM model_evaluations
       WHERE prediction_id IS NOT NULL
         AND prediction_id <> ''
         AND prediction_id NOT IN (
           SELECT prediction_id FROM model_predictions
           WHERE prediction_id IS NOT NULL AND prediction_id <> ''
         )`
    );
    evaluationsDeleted += Number(evalDangling && evalDangling.affectedRows || 0);

    return {
      ok: true,
      deleted,
      evaluationsDeleted,
      predictionsDeleted,
      count,
      keep: limit,
      predKeep,
      predWindowDeleted: Number(predWindow && predWindow.affectedRows || 0),
      predDedupeDeleted,
      evalWindowDeleted: Number(evalWindow && evalWindow.affectedRows || 0),
      evalDanglingDeleted: Number(evalDangling && evalDangling.affectedRows || 0)
    };
  } finally {
    conn.release();
  }
}

/**
 * Upsert one lottery record into official_draws.
 * @param {object} record parseLotteryJsonToRecord output
 * @param {{skipPrune?:boolean}} [options]
 * @returns {Promise<{ok:true,period:string,uid:string,prune?:object}>}
 */
async function upsertOfficialDraw(record, options) {
  if (!record || typeof record !== "object") {
    throw new Error("upsertOfficialDraw: empty record");
  }
  const uid = String(record.uid || record.period || "");
  const period = String(record.period || record.uid || "");
  if (!uid || !period || period === "--") {
    throw new Error("upsertOfficialDraw: missing period/uid");
  }
  const balls = pickBalls(record);
  if (balls.filter((n) => n != null).length < 4) {
    throw new Error("upsertOfficialDraw: invalid numbers");
  }
  const skipPrune = !!(options && options.skipPrune);
  const conn = await getPool().getConnection();
  try {
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
        toMysqlDateTime(record.opentime),
        balls[0],
        balls[1],
        balls[2],
        balls[3],
        balls[4],
        balls[4],
        toMysqlDateTime(record.capturedAt || record.opentime),
        JSON.stringify(record),
      ]
    );
  } finally {
    conn.release();
  }

  const out = { ok: true, period, uid };
  if (!skipPrune) {
    out.prune = await pruneOfficialDraws();
  }
  return out;
}

async function closeDrawStore() {
  if (!pool) return;
  const p = pool;
  pool = null;
  await p.end();
}

module.exports = {
  upsertOfficialDraw,
  pruneOfficialDraws,
  closeDrawStore,
  toMysqlDateTime,
  pickBalls,
  getPool,
  RDS_HISTORY_KEEP,
  RDS_PRED_HISTORY_KEEP,
};
