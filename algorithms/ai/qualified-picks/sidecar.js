"use strict";
/**
 * qualified-picks sidecar — 零侵入旁路。
 * 数据源：RDS official_draws 为主，runner /history 交叉验真；不一致则拒写 picks。
 * 面板 GET /qualified-picks + GET /qualified-picks/verify 双重读取。
 */

const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");

const REPO_ROOT = path.join(__dirname, "..", "..", "..");

const scanner = require("./scanner");
const audit = require("./audit");
const sticky = require("./sticky");
const dualPosition = require("./dual-position");
const seal = require("./seal");
const secureStore = require("./secure-store");

const DATA_DIR = path.join(REPO_ROOT, "data");
const OUT_FILE = path.join(DATA_DIR, "qualified-picks.json");
const SEALED_FILE = path.join(DATA_DIR, "qualified-picks.sealed.json");
const LOCKS_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "locks.json");
const LOCKS_SEALED_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "locks.sealed.json");
const DUAL_LOCKS_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "dual-locks.json");
const DUAL_LOCKS_SEALED_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "dual-locks.sealed.json");
const OWN_STATE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "state.json");

const POLL_MS = Math.max(15000, Number(process.env.QUALIFIED_PICKS_POLL_MS || 60000));
const WINDOW = Math.max(20, Number(process.env.QUALIFIED_PICKS_WINDOW || 100));
const MIN_RATE = Number(process.env.QUALIFIED_PICKS_MIN_RATE || 92);
const EXIT_MIN_RATE = Number(process.env.QUALIFIED_PICKS_EXIT_MIN_RATE || 88);
const MIN_LOCK_PERIODS = Math.max(0, Number(process.env.QUALIFIED_PICKS_MIN_LOCK_PERIODS || 12));
const STICKY_ENABLED = String(process.env.QUALIFIED_PICKS_STICKY || "1") !== "0";
const DUAL_ENABLED = String(process.env.QUALIFIED_PICKS_DUAL_ENABLED || "1") !== "0";
const DUAL_MIN_RATE = Number(process.env.QUALIFIED_PICKS_DUAL_MIN_RATE || 89);
const DUAL_EXIT_MIN_RATE = Number(process.env.QUALIFIED_PICKS_DUAL_EXIT_MIN_RATE || 85);
const MAX_SHOW_STREAK = Number(process.env.QUALIFIED_PICKS_MAX_SHOW_STREAK != null
  ? process.env.QUALIFIED_PICKS_MAX_SHOW_STREAK : 0);
const MAX_ENTRY_CONSECUTIVE = Number(process.env.QUALIFIED_PICKS_MAX_ENTRY_CONSECUTIVE != null
  ? process.env.QUALIFIED_PICKS_MAX_ENTRY_CONSECUTIVE : 1);
const FAIL_COOLDOWN = Math.max(0, Number(process.env.QUALIFIED_PICKS_FAIL_COOLDOWN || 3));
const ENTRY_LOOKBACK = Math.max(3, Number(process.env.QUALIFIED_PICKS_ENTRY_LOOKBACK || 8));
const HISTORY_LIMIT = Math.max(WINDOW + 20, Number(process.env.QUALIFIED_PICKS_HISTORY_LIMIT || 500));
const RUNNER_HISTORY_URL = process.env.QUALIFIED_PICKS_HISTORY_URL
  || `http://127.0.0.1:3001/history?limit=${HISTORY_LIMIT}`;
const RUNNER_HISTORY_FALLBACKS = [
  `http://127.0.0.1:3001/history?limit=${Math.max(WINDOW + 20, 300)}`,
  `http://127.0.0.1:8787/history?limit=${HISTORY_LIMIT}`,
  "http://127.0.0.1:8787/history?limit=200"
];

let rdsPool = null;

function isoNow() {
  return new Date().toISOString();
}

function log(msg, extra) {
  const line = `[${isoNow()}] qualified-picks ${msg}`;
  if (extra !== undefined) console.log(line, typeof extra === "string" ? extra : JSON.stringify(extra));
  else console.log(line);
}

function readEnvAuth() {
  const envPath = process.env.PENTA5_ENV_PATH || path.join(REPO_ROOT, ".env");
  const out = { user: process.env.PANEL_USER || "admin", pass: process.env.PANEL_PASSWORD || "" };
  if (out.pass) return out;
  try {
    if (!fs.existsSync(envPath)) return out;
    fs.readFileSync(envPath, "utf8").split("\n").forEach((line) => {
      const t = line.trim();
      if (!t || t.startsWith("#") || !t.includes("=")) return;
      const i = t.indexOf("=");
      const k = t.slice(0, i).trim();
      const v = t.slice(i + 1).trim().replace(/^["']|["']$/g, "");
      if (k === "PANEL_USER") out.user = v;
      if (k === "PANEL_PASSWORD") out.pass = v;
    });
  } catch (_) {}
  return out;
}

const AUTH = readEnvAuth();

function streakScanOptions(now) {
  return {
    window: WINDOW,
    minRate: MIN_RATE,
    exitMinRate: EXIT_MIN_RATE,
    minLockPeriods: MIN_LOCK_PERIODS,
    maxShowStreak: MAX_SHOW_STREAK,
    maxEntryConsecutive: MAX_ENTRY_CONSECUTIVE,
    cooldownPeriods: FAIL_COOLDOWN,
    entryLookback: ENTRY_LOOKBACK,
    now
  };
}

function getRdsPool() {
  if (!rdsPool) rdsPool = require(path.join(REPO_ROOT, "api", "db")).createPool();
  return rdsPool;
}

async function fetchRdsHistoryRows(limit) {
  const pool = getRdsPool();
  const lim = Math.max(WINDOW, Number(limit) || HISTORY_LIMIT);
  const [rows] = await pool.query(
    `SELECT period, uid, open_time, ball1, ball2, ball3, ball4, ball5
     FROM official_draws
     ORDER BY open_time DESC, id DESC
     LIMIT ?`,
    [lim]
  );
  return rows.map((row) => ({
    period: String(row.period || ""),
    uid: row.uid || `period:${row.period}`,
    opentime: row.open_time,
    numbers: [row.ball1, row.ball2, row.ball3, row.ball4, row.ball5].map((n) => String(n))
  }));
}

async function fetchRdsDrawCount() {
  const pool = getRdsPool();
  const [rows] = await pool.query("SELECT COUNT(*) AS c, MAX(period) AS latest_period FROM official_draws");
  return {
    total: Number(rows[0].c || 0),
    latestPeriod: rows[0].latest_period ? String(rows[0].latest_period) : null
  };
}

function httpGetJson(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const lib = String(url).startsWith("https:") ? https : http;
    const headers = { "User-Agent": "qualified-picks-sidecar/2.0" };
    if (AUTH.pass) {
      headers.Authorization = "Basic " + Buffer.from(`${AUTH.user}:${AUTH.pass}`).toString("base64");
    }
    const req = lib.get(url, { timeout: timeoutMs || 20000, headers }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        } catch (err) {
          reject(new Error("bad json: " + err.message));
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

async function fetchRunnerHistoryRows() {
  const urls = [RUNNER_HISTORY_URL].concat(RUNNER_HISTORY_FALLBACKS);
  const seen = new Set();
  for (const url of urls) {
    if (seen.has(url)) continue;
    seen.add(url);
    try {
      const payload = await httpGetJson(url, 30000);
      const rows = Array.isArray(payload) ? payload : (payload.history || payload.rows || []);
      if (rows.length) {
        if (url !== RUNNER_HISTORY_URL) log("runner history fallback", url + " n=" + rows.length);
        return {
          rows,
          sharedHistorySource: payload.sharedHistorySource || null,
          sharedHistoryLatestPeriod: payload.sharedHistoryLatestPeriod || null
        };
      }
    } catch (err) {
      log("runner history fail " + url, err.message);
    }
  }
  return { rows: [], sharedHistorySource: null, sharedHistoryLatestPeriod: null };
}

function writeJsonAtomic(file, payload) {
  secureStore.writeSecureJsonAtomic(file, payload);
}

function readJson(file, fallback) {
  return secureStore.readJsonFile(file, fallback);
}

function loadLocks() {
  return secureStore.readSealedField({
    sealedFile: LOCKS_SEALED_FILE,
    metaFile: LOCKS_FILE,
    password: AUTH.pass,
    field: "locks",
    legacyReader() {
      const raw = readJson(LOCKS_FILE, null);
      if (!raw) return {};
      return raw.locks && typeof raw.locks === "object" ? raw.locks : raw;
    }
  }) || {};
}

function loadDualLock() {
  return secureStore.readSealedField({
    sealedFile: DUAL_LOCKS_SEALED_FILE,
    metaFile: DUAL_LOCKS_FILE,
    password: AUTH.pass,
    field: "lock",
    legacyReader() {
      const raw = readJson(DUAL_LOCKS_FILE, null);
      return raw && raw.lock ? raw.lock : null;
    }
  });
}

function writeSealedLocks(locks) {
  secureStore.writeSealedBundle({
    sealedFile: LOCKS_SEALED_FILE,
    metaFile: LOCKS_FILE,
    payload: { locks },
    password: AUTH.pass,
    sealedVersion: "qualified-picks-locks-sealed-v1",
    buildMeta(payload) {
      const ids = Object.keys(payload.locks || {});
      return {
        version: "qualified-picks-locks-v1",
        updatedAt: isoNow(),
        strategy: "sticky-lock",
        locksSealed: !!AUTH.pass,
        lockCount: ids.length,
        lockIds: ids
      };
    }
  });
}

function writeSealedDualLock(lock) {
  secureStore.writeSealedBundle({
    sealedFile: DUAL_LOCKS_SEALED_FILE,
    metaFile: DUAL_LOCKS_FILE,
    payload: { lock },
    password: AUTH.pass,
    sealedVersion: "qualified-picks-dual-lock-sealed-v1",
    buildMeta() {
      return {
        version: "qualified-picks-dual-lock-v1",
        updatedAt: isoNow(),
        lockSealed: !!AUTH.pass,
        hasLock: !!lock
      };
    }
  });
}

let own = readJson(OWN_STATE, null) || {
  version: "qualified-picks-sidecar-v2",
  createdAt: isoNow(),
  updatedAt: "",
  runs: 0,
  lastPickCount: 0,
  lastCrossCheckOk: null
};

function saveOwn() {
  own.updatedAt = isoNow();
  writeJsonAtomic(OWN_STATE, own);
}

function buildFailurePayload(scanMeta, cross, rdsMeta, runnerMeta) {
  return {
    version: "qualified-picks-v2",
    updatedAt: isoNow(),
    window: WINDOW,
    minRate: MIN_RATE,
    historyCount: scanMeta.historyCount || 0,
    latestPeriod: scanMeta.latestPeriod || rdsMeta.latestPeriod || null,
    picks: [],
    audit: {
      crossCheckOk: false,
      historySource: "rds+runner-crosscheck",
      issues: cross.issues,
      rdsLatestPeriod: rdsMeta.latestPeriod,
      rdsTotalDraws: rdsMeta.total,
      runnerLatestPeriod: runnerMeta.latestPeriod,
      runnerHistorySource: runnerMeta.sharedHistorySource,
      windowDigest: scanMeta.windowDigest || null,
      windowNewestPeriod: scanMeta.windowNewestPeriod || null,
      windowOldestPeriod: scanMeta.windowOldestPeriod || null,
      scannedAt: isoNow(),
      sidecarPid: process.pid
    }
  };
}

function writeSealedOutput(payload) {
  const sealPass = AUTH.pass;
  if (sealPass) {
    secureStore.writeSecureJsonAtomic(SEALED_FILE, {
      version: "qualified-picks-sealed-v1",
      updatedAt: isoNow(),
      envelope: seal.sealPayload(payload, sealPass)
    });
    const meta = Object.assign({}, payload, {
      picks: [],
      dualPicks: [],
      picksSealed: true,
      positionPickCount: payload.picks.filter((p) => p.kind === "position").length,
      heweiPickCount: payload.picks.filter((p) => p.kind === "hewei").length,
      dualPickCount: (payload.dualPicks || []).length
    });
    secureStore.writeSecureJsonAtomic(OUT_FILE, meta);
    return;
  }
  secureStore.writeSecureJsonAtomic(OUT_FILE, payload);
}

async function tick() {
  const [rdsRows, runnerPack, rdsMeta] = await Promise.all([
    fetchRdsHistoryRows(HISTORY_LIMIT),
    fetchRunnerHistoryRows(),
    fetchRdsDrawCount()
  ]);
  const runnerRows = runnerPack.rows;

  if (rdsRows.length < WINDOW) {
    log("rds history too short", { have: rdsRows.length, need: WINDOW });
    return;
  }
  if (runnerRows.length < WINDOW) {
    log("runner history too short", { have: runnerRows.length, need: WINDOW });
    return;
  }

  const normRds = scanner.normalizeHistoryRows(rdsRows);
  const normRunner = scanner.normalizeHistoryRows(runnerRows);
  const cross = audit.crossCheckHistories(normRds, normRunner, WINDOW);

  const digestRds = audit.historyWindowDigest(normRds, WINDOW);
  const digestRunner = audit.historyWindowDigest(normRunner, WINDOW);
  if (digestRds.digest !== digestRunner.digest) {
    cross.ok = false;
    cross.issues = (cross.issues || []).concat([`digest_mismatch:${digestRds.digest.slice(0, 12)}_vs_${digestRunner.digest.slice(0, 12)}`]);
  }

  if (rdsMeta.latestPeriod && normRds[0] && rdsMeta.latestPeriod !== normRds[0].period) {
    cross.ok = false;
    cross.issues = (cross.issues || []).concat([`rds_meta_latest_mismatch:${rdsMeta.latestPeriod}_vs_${normRds[0].period}`]);
  }

  const scanMeta = {
    historyCount: normRds.length,
    latestPeriod: normRds[0] ? normRds[0].period : null,
    windowDigest: digestRds.digest,
    windowNewestPeriod: digestRds.newestPeriod,
    windowOldestPeriod: digestRds.oldestPeriod
  };

  if (!cross.ok) {
    log("CROSSCHECK FAILED — picks withheld", cross.issues);
    const payload = buildFailurePayload(scanMeta, cross, rdsMeta, {
      latestPeriod: normRunner[0] ? normRunner[0].period : runnerPack.sharedHistoryLatestPeriod,
      sharedHistorySource: runnerPack.sharedHistorySource
    });
    writeJsonAtomic(OUT_FILE, payload);
    own.runs += 1;
    own.lastPickCount = 0;
    own.lastLatestPeriod = scanMeta.latestPeriod;
    own.lastCrossCheckOk = false;
    own.lastIssues = cross.issues;
    saveOwn();
    return;
  }

  const now = isoNow();
  const streakOpts = streakScanOptions(now);

  const scan = STICKY_ENABLED
    ? sticky.scanQualifiedPicksSticky(rdsRows, streakOpts, loadLocks())
    : scanner.scanQualifiedPicks(rdsRows, { window: WINDOW, minRate: MIN_RATE });
  if (scan.locks) {
    writeSealedLocks(scan.locks);
  }

  let dualScan = { picks: [], lock: null, minRate: DUAL_MIN_RATE };
  if (DUAL_ENABLED) {
    const dualOpts = Object.assign({}, streakOpts, {
      minRate: DUAL_MIN_RATE,
      exitMinRate: DUAL_EXIT_MIN_RATE
    });
    dualScan = STICKY_ENABLED
      ? dualPosition.scanDualPositionSticky(rdsRows, dualOpts, loadDualLock())
      : dualPosition.scanDualPositionPicks(rdsRows, dualOpts);
    if (dualScan.lock) {
      writeSealedDualLock(dualScan.lock);
    }
  }

  const payload = {
    version: "qualified-picks-v4",
    updatedAt: isoNow(),
    window: scan.window,
    minRate: scan.minRate,
    exitMinRate: scan.exitMinRate || EXIT_MIN_RATE,
    minLockPeriods: scan.minLockPeriods || MIN_LOCK_PERIODS,
    maxShowStreak: MAX_SHOW_STREAK,
    maxEntryConsecutive: MAX_ENTRY_CONSECUTIVE,
    failCooldown: FAIL_COOLDOWN,
    strategy: scan.strategy || (STICKY_ENABLED ? "sticky-lock" : "history-gate"),
    historyCount: scan.historyCount,
    latestPeriod: scan.latestPeriod,
    picks: scan.picks,
    dualPicks: dualScan.picks || [],
    dualMinRate: dualScan.minRate || DUAL_MIN_RATE,
    dualExitMinRate: dualScan.exitMinRate || DUAL_EXIT_MIN_RATE,
    audit: {
      crossCheckOk: true,
      historySource: "rds+runner-crosscheck",
      issues: [],
      rdsLatestPeriod: rdsMeta.latestPeriod,
      rdsTotalDraws: rdsMeta.total,
      runnerLatestPeriod: normRunner[0] ? normRunner[0].period : null,
      runnerHistorySource: runnerPack.sharedHistorySource || "rds",
      windowDigest: scan.windowDigest,
      windowNewestPeriod: scan.windowNewestPeriod,
      windowOldestPeriod: scan.windowOldestPeriod,
      picksFingerprint: audit.pickFingerprint(scan.picks),
      dualPicksFingerprint: dualPosition.dualPickFingerprint(dualScan.picks || []),
      scannedAt: isoNow(),
      sidecarPid: process.pid,
      stickyEnabled: STICKY_ENABLED,
      dualEnabled: DUAL_ENABLED,
      streakGate: {
        maxShowStreak: MAX_SHOW_STREAK,
        maxEntryConsecutive: MAX_ENTRY_CONSECUTIVE,
        failCooldown: FAIL_COOLDOWN,
        entryLookback: ENTRY_LOOKBACK
      }
    }
  };
  writeSealedOutput(payload);
  own.runs += 1;
  own.lastPickCount = scan.picks.length;
  own.lastLatestPeriod = scan.latestPeriod;
  own.lastCrossCheckOk = true;
  own.lastIssues = [];
  saveOwn();
  const posN = scan.picks.filter((p) => p.kind === "position").length;
  const heweiN = scan.picks.filter((p) => p.kind === "hewei").length;
  const dualN = (dualScan.picks || []).length;
  log("updated picks=" + scan.picks.length + " pos=" + posN + " hewei=" + heweiN + " dual=" + dualN + " period=" + (scan.latestPeriod || "--") + " sealed=" + (AUTH.pass ? "yes" : "no"));
}

async function main() {
  const once = process.argv.includes("--once");
  fs.mkdirSync(path.dirname(OWN_STATE), { recursive: true });
  log("start v4 poll=" + POLL_MS + "ms window=" + WINDOW + " minRate=" + MIN_RATE + " exitRate=" + EXIT_MIN_RATE + " minLock=" + MIN_LOCK_PERIODS + " sticky=" + (STICKY_ENABLED ? "on" : "off") + " dual=" + (DUAL_ENABLED ? "on" : "off") + " streak=" + MAX_SHOW_STREAK + "/" + MAX_ENTRY_CONSECUTIVE + "/" + FAIL_COOLDOWN + " disk-seal=on locks-seal=on mode=600 rds-crosscheck=on");
  if (!AUTH.pass) log("SECURITY WARN: PANEL_PASSWORD missing — picks will be written in plaintext");
  await tick();
  if (once) {
    process.exit(0);
    return;
  }
  setInterval(() => {
    tick().catch((err) => log("tick error", err.message));
  }, POLL_MS);
}

main().catch((err) => {
  console.error("fatal:", err);
  process.exit(1);
});
