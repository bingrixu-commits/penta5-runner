"use strict";

/**
 * Single-draw upsert into official_draws (RDS primary write path).
 * Used by penta5_backend_runner syncNow so new periods hit RDS before local history hydrate.
 */

const { createPool } = require("./db");

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

/**
 * Upsert one lottery record into official_draws.
 * @param {object} record parseLotteryJsonToRecord output
 * @returns {Promise<{ok:true,period:string,uid:string}>}
 */
async function upsertOfficialDraw(record) {
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
    return { ok: true, period, uid };
  } finally {
    conn.release();
  }
}

async function closeDrawStore() {
  if (!pool) return;
  const p = pool;
  pool = null;
  await p.end();
}

module.exports = {
  upsertOfficialDraw,
  closeDrawStore,
  toMysqlDateTime,
  pickBalls,
  getPool,
};
