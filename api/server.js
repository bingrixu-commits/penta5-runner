"use strict";

const http = require("http");
const { createPool } = require("./db");

const PORT = Number(process.env.RDS_API_PORT || 8787);
const HOST = process.env.RDS_API_HOST || "127.0.0.1";

const pool = createPool();

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(payload);
}

function parseQuery(url) {
  const q = {};
  const idx = url.indexOf("?");
  if (idx < 0) return q;
  for (const part of url.slice(idx + 1).split("&")) {
    if (!part) continue;
    const eq = part.indexOf("=");
    const k = decodeURIComponent(eq >= 0 ? part.slice(0, eq) : part);
    const v = decodeURIComponent(eq >= 0 ? part.slice(eq + 1) : "");
    q[k] = v;
  }
  return q;
}

function parseJsonField(value) {
  if (value == null) return null;
  if (typeof value === "object") return value;
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch (_) {
    return null;
  }
}

function ballsFromRow(row) {
  return [row.ball1, row.ball2, row.ball3, row.ball4, row.ball5].map((n) =>
    n == null ? null : Number(n)
  );
}

function drawToHistoryRecord(row) {
  const raw = parseJsonField(row.raw_json);
  if (raw && typeof raw === "object") return raw;
  return {
    uid: row.uid,
    period: row.period,
    opentime: row.open_time,
    numbers: ballsFromRow(row),
  };
}

async function handleHealth(_req, res) {
  let dbOk = false;
  let dbError = null;
  let drawCount = null;
  let latestPeriod = null;
  try {
    const [rows] = await pool.query(
      "SELECT COUNT(*) AS c, MAX(period) AS latest_period FROM official_draws"
    );
    dbOk = true;
    drawCount = Number(rows[0].c || 0);
    latestPeriod = rows[0].latest_period || null;
  } catch (err) {
    dbError = String(err && err.message ? err.message : err);
  }
  sendJson(res, dbOk ? 200 : 503, {
    ok: dbOk,
    service: "penta5-rds-api",
    port: PORT,
    schema: "existing-v71-import",
    db: dbOk ? "up" : "down",
    drawCount,
    latestPeriod,
    error: dbError,
    ts: new Date().toISOString(),
  });
}

async function handleHistory(req, res) {
  const q = parseQuery(req.url || "");
  const limit = Math.min(Math.max(Number(q.limit || 100) || 100, 1), 5000);
  const offset = Math.max(Number(q.offset || 0) || 0, 0);
  const [countRows] = await pool.query("SELECT COUNT(*) AS c FROM official_draws");
  const [rows] = await pool.query(
    `SELECT uid, period, open_time, ball1, ball2, ball3, ball4, ball5, raw_json
     FROM official_draws
     ORDER BY open_time DESC, id DESC
     LIMIT ? OFFSET ?`,
    [limit, offset]
  );
  sendJson(res, 200, {
    ok: true,
    total: Number(countRows[0].c || 0),
    limit,
    offset,
    history: rows.map(drawToHistoryRecord),
  });
}

/** 四定回删专用：直查 RDS，只返回近 N 期唯一四位旧码，不走 runner 内存历史缓存。 */
async function handleFourBackdeleteOpened(req, res) {
  const q = parseQuery(req.url || "");
  const limit = Math.min(Math.max(Number(q.limit || 1200) || 1200, 1), 1200);
  // 与面板/attack_txt 一致：低于 100 期才停；100–limit 之间按实际期数截断继续真删
  const historyMin = 100;
  const [rows] = await pool.query(
    `SELECT period, open_time, ball1, ball2, ball3, ball4
     FROM official_draws
     ORDER BY period DESC
     LIMIT ?`,
    [limit]
  );
  const scanned = Array.isArray(rows) ? rows.length : 0;
  if (!Array.isArray(rows) || scanned < historyMin) {
    sendJson(res, 503, {
      ok: false,
      error: "四定回删：RDS 不足 " + historyMin + " 期（当前 " + scanned + "），无法回删",
      scanned,
      limit,
      historyMin,
      truncated: true,
      source: "rds-direct",
    });
    return;
  }
  const truncated = scanned < limit;
  const seenPeriod = Object.create(null);
  const seenCode = Object.create(null);
  const opened = [];
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i] || {};
    const period = String(row.period == null ? "" : row.period).replace(/\D/g, "");
    const balls = [row.ball1, row.ball2, row.ball3, row.ball4].map((n) =>
      n == null ? "" : String(n)
    );
    if (!/^\d+$/.test(period) || seenPeriod[period] || !String(row.open_time || "").trim()) {
      sendJson(res, 500, {
        ok: false,
        error: "RDS 第 " + (i + 1) + " 条记录期号/开奖时间不完整或重复",
        source: "rds-direct",
      });
      return;
    }
    if (balls.some((v) => !/^\d$/.test(v))) {
      sendJson(res, 500, {
        ok: false,
        error: "RDS 第 " + (i + 1) + " 条记录前四位号码不完整",
        source: "rds-direct",
      });
      return;
    }
    seenPeriod[period] = true;
    const code = balls.join("");
    if (!seenCode[code]) {
      seenCode[code] = true;
      opened.push(code);
    }
  }
  sendJson(res, 200, {
    ok: true,
    source: "rds-direct",
    cache: false,
    limit,
    scanned,
    truncated,
    openedUnique: opened.length,
    latestPeriod: String(rows[0].period || ""),
    oldestPeriod: String(rows[rows.length - 1].period || ""),
    opened,
    ts: new Date().toISOString(),
  });
}

async function handleState(_req, res) {
  const [[meta]] = await pool.query(
    `SELECT COUNT(*) AS historyCount,
            MAX(period) AS latestPeriod,
            MAX(open_time) AS latestOpenTime
     FROM official_draws`
  );
  const [latestRows] = await pool.query(
    `SELECT uid, period, open_time, ball1, ball2, ball3, ball4, ball5, raw_json
     FROM official_draws
     ORDER BY open_time DESC, id DESC
     LIMIT 1`
  );
  const [predCountRows] = await pool.query(
    "SELECT COUNT(*) AS c FROM model_predictions"
  );
  const [counterRows] = await pool.query(
    `SELECT counter_key, counter_type, counter_day, total_count, match_count,
            rate_value, raw_json, updated_at
     FROM model_counters`
  );
  const [stateRows] = await pool.query(
    `SELECT state_key, state_type, raw_json, updated_at FROM model_states`
  );
  const [snapRows] = await pool.query(
    `SELECT id, snapshot_sha256, source_path, state_version, history_count,
            prediction_count, created_at
     FROM system_snapshots
     ORDER BY id DESC
     LIMIT 1`
  );

  const counters = {};
  const dailyStreakLedger = {};
  for (const row of counterRows) {
    const ledger = parseJsonField(row.raw_json);
    const key = String(row.counter_key || "");
    const shortKey = key.replace(/^counter:/, "").replace(/^daily:/, "");
    if (row.counter_type === "daily_streak" || row.counter_day) {
      if (!dailyStreakLedger[shortKey]) dailyStreakLedger[shortKey] = {};
      const day = String(row.counter_day || "all");
      dailyStreakLedger[shortKey][day] =
        ledger || {
          day,
          total: row.total_count,
          match: row.match_count,
        };
    } else {
      counters[shortKey] =
        ledger || {
          total: row.total_count,
          match: row.match_count,
          rate: row.rate_value,
        };
    }
  }

  const modelModules = {};
  for (const row of stateRows) {
    modelModules[row.state_key] = {
      stateKey: row.state_key,
      stateType: row.state_type,
      updatedAt: row.updated_at,
      state: parseJsonField(row.raw_json),
    };
  }

  const latestRecord = latestRows[0] ? drawToHistoryRecord(latestRows[0]) : null;
  sendJson(res, 200, {
    ok: true,
    source: "rds",
    schema: "existing-v71-import",
    readMode: process.env.DATA_READ_MODE || "rds-api",
    historyCount: Number(meta.historyCount || 0),
    predictionCount: Number(predCountRows[0].c || 0),
    latestPeriod: meta.latestPeriod || null,
    latestOpenTime: meta.latestOpenTime || null,
    latestRecord,
    counters,
    dailyStreakLedger,
    modelModules,
    snapshot: snapRows[0] || null,
    note:
      "Shared history API for models. Panel/robots hydrate 3001 history from this endpoint when PENTA5_HISTORY_FROM_RDS=1; ledgers stay on 3001.",
    ts: new Date().toISOString(),
  });
}

const server = http.createServer(async (req, res) => {
  try {
    const pathOnly = (req.url || "/").split("?")[0];
    if (req.method === "GET" && pathOnly === "/health") return await handleHealth(req, res);
    if (req.method === "GET" && pathOnly === "/history") return await handleHistory(req, res);
    if (req.method === "GET" && pathOnly === "/four-backdelete-opened") {
      return await handleFourBackdeleteOpened(req, res);
    }
    if (req.method === "GET" && pathOnly === "/state") return await handleState(req, res);
    sendJson(res, 404, { ok: false, error: "not_found" });
  } catch (err) {
    sendJson(res, 500, {
      ok: false,
      error: String(err && err.message ? err.message : err),
    });
  }
});

server.listen(PORT, HOST, () => {
  console.log("penta5-rds-api listening on http://" + HOST + ":" + PORT);
});

process.on("SIGINT", async () => {
  await pool.end();
  process.exit(0);
});
process.on("SIGTERM", async () => {
  await pool.end();
  process.exit(0);
});
