"use strict";
/**
 * claude-ensemble sidecar — 零侵入旁路运行器 v2。
 *
 * 不修改 penta5_backend_runner，也不写主 state.json。独立进程：
 *   1. 轮询官网 latest（或 CLAUDE_ENSEMBLE_LATEST_URL）检测新开奖；
 *   2. 新期出现后等主 runner 生成并封存当期预测，读 data/state.json 取
 *      currentPrediction（含六框架+threeDuishu 快照）与已评估预测摘要；
 *   3. 从 http://127.0.0.1:8787/history 取真实开奖历史；
 *   4. 调 algorithms/claude-ensemble/engine 计算集成快照并落到
 *      data/algorithm-projects/claude-ensemble/state.json（own file，不与任何模块共享）；
 *   5. 开奖后自评：整组全部避开=成功，累计滚动战绩；漏掉的期数从历史接口补评。
 *
 * v2 修复：
 *   - 基准期/目标期双重防重与过期守卫（目标期已开奖则不再生成，杜绝无法验证的孤儿快照）
 *   - 漏评补结算：进程重启/网络抖动错过的期数，从 history 接口找回实际开奖补评
 *   - 主 state.json 按 mtime 跳过未变化的重复解析（15MB/30s → 仅预测更新时解析）
 */

const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");

const REPO_ROOT = path.join(__dirname, "..", "..", "..");

const engine = require("./engine");

const DATA_DIR = path.join(REPO_ROOT, "data");
const OWN_DIR = path.join(DATA_DIR, "algorithm-projects", "claude-ensemble");
const OWN_STATE = path.join(OWN_DIR, "state.json");
const MAIN_STATE = path.join(DATA_DIR, "state.json");

const LATEST_URL = process.env.CLAUDE_ENSEMBLE_LATEST_URL || "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest";
const HISTORY_URL = process.env.CLAUDE_ENSEMBLE_HISTORY_URL || "http://127.0.0.1:8787/history?limit=520";
const SETTLE_HISTORY_URL = process.env.CLAUDE_ENSEMBLE_SETTLE_URL || "http://127.0.0.1:8787/history?limit=60";
const POLL_MS = Math.max(10000, Number(process.env.CLAUDE_ENSEMBLE_POLL_MS || 30000));
const SEAL_WAIT_MS = Math.max(5000, Number(process.env.CLAUDE_ENSEMBLE_SEAL_WAIT_MS || 20000));
const WEB_PORT = Number(process.env.CLAUDE_ENSEMBLE_WEB_PORT || 3002);
const KEEP_RECORDS = 400;

function isoNow() { return new Date().toISOString(); }

function log(message, extra) {
  const line = "[" + isoNow() + "] " + message;
  if (extra !== undefined) {
    console.log(line, typeof extra === "string" ? extra : JSON.stringify(extra));
  } else {
    console.log(line);
  }
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

function httpGetJson(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const lib = String(url || "").startsWith("https:") ? https : http;
    const req = lib.get(url, { timeout: timeoutMs || 8000 }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        } catch (err) {
          reject(new Error("bad json from " + url + ": " + err.message));
        }
      });
    });
    req.on("timeout", () => { req.destroy(new Error("timeout " + url)); });
    req.on("error", reject);
  });
}

/** 历史拉取：大 limit 易超时，失败后降级到较小窗口，避免四项空窗。 */
async function fetchHistoryRows(timeoutMs) {
  const urls = [
    HISTORY_URL,
    "http://127.0.0.1:8787/history?limit=800",
    "http://127.0.0.1:8787/history?limit=520",
    "http://127.0.0.1:8787/history?limit=200"
  ];
  const seen = new Set();
  for (const url of urls) {
    if (seen.has(url)) continue;
    seen.add(url);
    try {
      const payload = await httpGetJson(url, timeoutMs || 25000);
      const rows = Array.isArray(payload) ? payload : (payload.history || payload.rows || []);
      if (rows.length) {
        if (url !== HISTORY_URL) log("history fallback ok via " + url + " n=" + rows.length);
        return rows;
      }
    } catch (err) {
      log("history fetch failed: " + err.message);
    }
  }
  return [];
}

function readJson(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return fallback;
  }
}

function writeJsonAtomic(file, payload) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = file + "." + process.pid + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), "utf8");
  fs.renameSync(tmp, file);
}

function firstFourDigits(numbers) {
  if (!Array.isArray(numbers) || numbers.length < 4) return null;
  const digits = numbers.slice(0, 4).map((v) => {
    const n = Number(String(v).trim());
    return Number.isInteger(n) && n >= 0 && n <= 9 ? n : null;
  });
  return digits.every((d) => d !== null) ? digits : null;
}

let own = readJson(OWN_STATE, null) || {
  version: "claude-ensemble-sidecar-v2",
  createdAt: isoNow(),
  updatedAt: "",
  mode: "sidecar-shadow",
  pending: [],      // 已生成、等待开奖验证的快照
  records: [],      // 已验证记录（新在前）
  missed: [],       // 目标期已过但找不到开奖数据的（只作审计）
  aggregate: { total: 0, hit: 0, rate: null, lower: 0 },
  itemAggregate: {} // kind/key 维度战绩
};
own.version = "claude-ensemble-sidecar-v5-hold-display";
if (!Array.isArray(own.missed)) own.missed = [];
if (own.displayHold === undefined) own.displayHold = null;
if (!own.poolStats || typeof own.poolStats !== "object") {
  own.poolStats = { total: 0, hit: 0, rate: null, poolSize: 500, baseline: 50 };
}
// 三位池基线 = poolSize/1000（历史遗留的错误默认值就地纠正）
if (Number(own.poolStats.baseline) < 40) own.poolStats.baseline = 50;
if (!own.taskStats || typeof own.taskStats !== "object") {
  own.taskStats = { total: 0, hit: 0, rate: null, baseline: 63.8 };
}
if (!own.anyTwoStats || typeof own.anyTwoStats !== "object") {
  own.anyTwoStats = { total: 0, hit: 0, rate: null, baseline: 99.0, label: "二数合·02468排1码" };
}

function saveOwn() {
  own.updatedAt = isoNow();
  writeJsonAtomic(OWN_STATE, own);
}

function updateAggregate() {
  const agg = own.aggregate;
  agg.rate = agg.total ? Number(((agg.hit / agg.total) * 100).toFixed(2)) : null;
  const n = agg.total;
  if (n) {
    const z = 1.6449;
    const p = agg.hit / n;
    const denom = 1 + (z * z) / n;
    const centre = p + (z * z) / (2 * n);
    const margin = z * Math.sqrt((p * (1 - p) + (z * z) / (4 * n)) / n);
    agg.lower = Number((((centre - margin) / denom) * 100).toFixed(2));
  }
}

/** 用实际开奖记录结算一条挂起快照。 */
function recordOutcome(entry, actualRecord) {
  const numbers = Array.isArray(actualRecord.numbers) ? actualRecord.numbers : [];
  const digits = firstFourDigits(numbers);
  if (!digits) return false;
  const itemResults = entry.snapshot.items.map((item) => {
    const outcome = engine.itemOutcome(item, digits);
    return { name: item.name, kind: item.kind, key: item.key, code: item.code, outcome };
  });
  const judged = itemResults.filter((r) => r.outcome !== null);
  const success = judged.length > 0 && judged.every((r) => r.outcome === true);
  // 500组"四选三"池命中判定：开奖号在本期选定的三个位置全对且落池即算赢
  let poolHit = null;
  let poolRank = null;
  const pool = entry.pool && Array.isArray(entry.pool.codes) ? entry.pool.codes : null;
  if (pool && pool.length) {
    const positions = Array.isArray(entry.pool.positions) && entry.pool.positions.length === 3
      ? entry.pool.positions
      : [0, 1, 2];
    const actualCode3 = positions.map((p) => digits[p]).join("");
    const idx = pool.indexOf(actualCode3);
    poolHit = idx >= 0;
    if (poolHit) poolRank = idx + 1;
    own.poolStats.total += 1;
    if (poolHit) own.poolStats.hit += 1;
    own.poolStats.poolSize = pool.length;
    own.poolStats.rate = own.poolStats.total
      ? Number(((own.poolStats.hit / own.poolStats.total) * 100).toFixed(2))
      : null;
    own.poolStats.baseline = Number(((pool.length / 1000) * 100).toFixed(2));
  }
  // 四项任务结算：全过 = 过关
  let task4Result = null;
  if (entry.task4 && Array.isArray(entry.task4.items) && entry.task4.items.length) {
    const taskItems = entry.task4.items.map((item) => ({
      name: item.name, kind: item.kind, code: item.code,
      outcome: engine.itemOutcome(item, digits)
    }));
    const taskJudged = taskItems.filter((r) => r.outcome !== null);
    const taskSuccess = taskJudged.length === entry.task4.items.length && taskJudged.every((r) => r.outcome === true);
    task4Result = {
      success: taskSuccess,
      passed: taskJudged.filter((r) => r.outcome === true).length,
      total: entry.task4.items.length,
      items: taskItems
    };
    own.taskStats.total += 1;
    if (taskSuccess) own.taskStats.hit += 1;
    own.taskStats.rate = own.taskStats.total
      ? Number(((own.taskStats.hit / own.taskStats.total) * 100).toFixed(2))
      : null;
  }
  // 独立正式二数合结算（与四项任务分轨）
  let formalAnyTwoResult = null;
  if (entry.formalAnyTwo && entry.formalAnyTwo.code) {
    const outcome = engine.itemOutcome(entry.formalAnyTwo, digits);
    if (outcome !== null) {
      formalAnyTwoResult = {
        success: outcome === true,
        code: entry.formalAnyTwo.code,
        name: entry.formalAnyTwo.name || ("二数合" + entry.formalAnyTwo.code),
        trioLabel: entry.formalAnyTwo.trioLabel || "",
        excludedDigit: entry.formalAnyTwo.excludedDigit || "",
        rate120: entry.formalAnyTwo.rate120,
        outcome
      };
      own.anyTwoStats.total += 1;
      if (outcome === true) own.anyTwoStats.hit += 1;
      own.anyTwoStats.rate = own.anyTwoStats.total
        ? Number(((own.anyTwoStats.hit / own.anyTwoStats.total) * 100).toFixed(2))
        : null;
      if (entry.formalAnyTwo.structBase != null) {
        own.anyTwoStats.baseline = Number(entry.formalAnyTwo.structBase);
      }
    }
  }
  own.records.unshift({
    basePeriod: entry.basePeriod,
    targetPeriod: String(entry.targetPeriod || ""),
    actualNumbers: numbers.slice(0, 5),
    evaluatedAt: isoNow(),
    success,
    passed: judged.filter((r) => r.outcome === true).length,
    total: judged.length,
    poolHit,
    poolRank,
    poolSize: pool ? pool.length : null,
    task4: task4Result,
    formalAnyTwo: formalAnyTwoResult,
    quality: entry.snapshot.quality,
    items: itemResults
  });
  own.records = own.records.slice(0, KEEP_RECORDS);
  own.aggregate.total += 1;
  if (success) own.aggregate.hit += 1;
  for (const r of judged) {
    const sig = r.kind + "/" + r.key;
    const slot = own.itemAggregate[sig] || { total: 0, hit: 0 };
    slot.total += 1;
    if (r.outcome === true) slot.hit += 1;
    own.itemAggregate[sig] = slot;
  }
  updateAggregate();
  log("evaluated " + entry.targetPeriod + " success=" + success +
    " passed=" + judged.filter((r) => r.outcome === true).length + "/" + judged.length +
    " | rolling " + own.aggregate.hit + "/" + own.aggregate.total + " (" + own.aggregate.rate + "%)");
  return true;
}

/**
 * 结算所有已到期的挂起快照。目标期==最新期用 latest 直接结算；
 * 目标期更早（漏评）则从历史接口找回；找不到的挪进 missed 审计列表。
 */
async function settlePending(latest) {
  if (!own.pending.length) return;
  const latestPeriod = String(latest.period || "");
  let historyRows = null;
  const still = [];
  let changed = false;
  let lastSettledHold = null;
  for (const entry of own.pending) {
    const target = String(entry.targetPeriod || "");
    if (!target || target > latestPeriod) { still.push(entry); continue; } // 还没开奖
    let actual = null;
    if (target === latestPeriod) {
      actual = latest;
    } else {
      if (historyRows === null) {
        try {
          const payload = await httpGetJson(SETTLE_HISTORY_URL, 10000);
          historyRows = Array.isArray(payload) ? payload : (payload.history || payload.rows || []);
        } catch (err) {
          log("settle history fetch failed: " + err.message);
          historyRows = [];
        }
      }
      actual = historyRows.find((r) => String(r.period || "") === target) || null;
    }
    if (!actual) {
      log("target " + target + " passed but draw not found; moved to missed");
      own.missed.unshift({ basePeriod: entry.basePeriod, targetPeriod: target, generatedAt: entry.generatedAt, missedAt: isoNow() });
      own.missed = own.missed.slice(0, 50);
      changed = true;
      continue;
    }
    // 结算前先留住四项展示，避免 pending 清空后页面空白
    if (entry.task4 && Array.isArray(entry.task4.items) && entry.task4.items.length) {
      lastSettledHold = {
        basePeriod: entry.basePeriod,
        targetPeriod: entry.targetPeriod,
        task4: entry.task4,
        formalAnyTwo: entry.formalAnyTwo || null,
        settledAt: isoNow(),
        actualNumbers: Array.isArray(actual.numbers) ? actual.numbers.slice(0, 5) : null
      };
    }
    if (recordOutcome(entry, actual)) changed = true;
  }
  own.pending = still;
  if (lastSettledHold && !own.pending.length) {
    own.displayHold = lastSettledHold;
    changed = true;
  }
  if (changed) saveOwn();
}

let lastMainMtimeMs = 0;
let mainNeedsRetry = false;


/** 兼容官网 latest 包：{period} 或 {Data:{Results:[{PeriodNo,OpenCode}]}} */
function normalizeLatest(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  if (src.period || src.PeriodNo) {
    const period = String(src.period || src.PeriodNo || "");
    let numbers = Array.isArray(src.numbers) ? src.numbers.slice(0, 5) : null;
    if (!numbers && src.OpenCode) {
      numbers = String(src.OpenCode).split(/[,\s]+/).filter(Boolean).slice(0, 5);
    }
    return { period, numbers, raw: src };
  }
  const row = src.Data && Array.isArray(src.Data.Results) ? src.Data.Results[0]
    : (Array.isArray(src.Results) ? src.Results[0] : null);
  if (row && typeof row === "object") {
    const period = String(row.PeriodNo || row.period || "");
    const numbers = String(row.OpenCode || row.openCode || "")
      .split(/[,\s]+/).filter(Boolean).slice(0, 5);
    return { period, numbers: numbers.length ? numbers : null, raw: src };
  }
  return { period: "", numbers: null, raw: src };
}

async function computeForCurrentPrediction(latestPeriod) {
  let stat;
  try {
    stat = fs.statSync(MAIN_STATE);
  } catch (_) {
    return;
  }
  // 主 state 未变化且上次不是暂时性失败 → 不必重复解析 15MB 文件
  if (stat.mtimeMs === lastMainMtimeMs && !mainNeedsRetry) return;

  const mainState = readJson(MAIN_STATE, null);
  if (!mainState || !mainState.currentPrediction) {
    log("main state.json unavailable, skip");
    lastMainMtimeMs = stat.mtimeMs;
    mainNeedsRetry = false;
    return;
  }
  lastMainMtimeMs = stat.mtimeMs;
  mainNeedsRetry = false;

  const prediction = mainState.currentPrediction;
  const basePeriod = String(prediction.basePeriod || "");
  const targetPeriod = String(prediction.targetPeriod || "");
  if (!basePeriod) return;
  // 防重：该基准期已有挂起或已验证记录
  if (own.pending.some((p) => String(p.basePeriod) === basePeriod)) return;
  if (own.records.some((r) => String(r.basePeriod) === basePeriod)) return;
  // 防过期：目标期已开奖（主 runner 还没轮换预测时的竞态窗口）
  if (targetPeriod && latestPeriod && targetPeriod <= latestPeriod) {
    log("prediction base=" + basePeriod + " target=" + targetPeriod + " already drawn; wait for runner rollover");
    return;
  }
  // 全量封存优先；若 compactAttack 等单项卡住导致 allSnapshotsSealed 长期为 false，
  // 只要集成所需的多数 L1 源已 sealed，仍允许软生成四项任务（否则页面一直空白）。
  if (prediction.allSnapshotsSealed !== true) {
    const softSources = ["autoTop6", "recommend2", "killFramework", "threeDingTwo", "threeDingOne", "fourPosExclude", "threeDuishu"];
    const softSealed = softSources.filter((key) => {
      const snap = prediction[key];
      return !!(snap && typeof snap === "object" && snap.sealed === true);
    }).length;
    if (softSealed < 5) {
      log("prediction " + basePeriod + " not sealed yet (soft=" + softSealed + "/7), will retry");
      mainNeedsRetry = true;
      return;
    }
    log("prediction " + basePeriod + " soft-ready for task4 (sealedSources=" + softSealed + "/7, allSnapshotsSealed=false)");
  }

  const historyRows = await fetchHistoryRows(25000);
  if (!historyRows.length) {
    log("history unavailable, will retry");
    mainNeedsRetry = true;
    return;
  }

  const shim = {
    history: historyRows,
    latestRecord: historyRows[0],
    predictions: Array.isArray(mainState.predictions) ? mainState.predictions : [],
    currentPrediction: prediction
  };
  engine.bindRuntime({ getState: () => shim });
  engine.clearClaudeEnsembleStatsCache();
  const t0 = Date.now();
  const snapshot = engine.buildClaudeEnsembleSnapshot(prediction);
  if (!snapshot) {
    log("snapshot null for " + basePeriod + " (insufficient proposals)");
    return;
  }
  // 500组池生成已按用户要求停用（2026-07-21）。仍用池选位逻辑确定三定位置，
  // 但不再生成/存储/结算号码池，页面只保留四项任务 + 独立正式二数合。
  let task4 = null;
  let formalAnyTwo = null;
  try {
    const prePool = engine.buildCandidatePool(snapshot, historyRows, 500);
    const trioPos = prePool && prePool.positions;
    task4 = engine.buildCompactTask(snapshot, historyRows, trioPos);
    formalAnyTwo = engine.buildFormalAnyTwo(
      historyRows,
      (task4 && task4.trio) || trioPos,
      targetPeriod || basePeriod
    );
  } catch (err) {
    log("task/anyTwo build failed: " + err.message);
  }
  own.pending.push({
    basePeriod,
    targetPeriod,
    generatedAt: isoNow(),
    snapshot,
    pool: null,
    task4,
    formalAnyTwo
  });
  // 新四项已就绪，清掉空窗占位
  if (own.displayHold) own.displayHold = null;
  saveOwn();
  log("snapshot built for base=" + basePeriod + " target=" + targetPeriod +
    " items=" + snapshot.itemCount + " quality=" + snapshot.quality +
    (task4 ? " task4=" + task4.trioLabel + "四项" : " task4=null") +
    (formalAnyTwo ? " anyTwo=" + formalAnyTwo.code : " anyTwo=null") +
    " in " + (Date.now() - t0) + "ms");
  snapshot.items.forEach((item) => {
    log("  #" + item.rank + " " + item.name + " votes=" + item.votes + " lower120=" + item.lower120 + " score=" + item.score);
  });
}

let lastSeenPeriod = "";
let busy = false;

async function tick() {
  if (busy) return;
  busy = true;
  try {
    let latestRaw;
    try {
      latestRaw = await httpGetJson(LATEST_URL, 6000);
    } catch (err) {
      log("latest fetch failed: " + err.message);
      return;
    }
    const latestNorm = normalizeLatest(latestRaw);
    const period = String(latestNorm.period || "");
    if (!period) {
      log("latest period missing; keys=" + Object.keys(latestRaw || {}).join(","));
      return;
    }
    const latest = {
      period,
      numbers: latestNorm.numbers,
      raw: latestNorm.raw
    };
    if (period !== lastSeenPeriod) {
      const isFirst = !lastSeenPeriod;
      lastSeenPeriod = period;
      log("new period detected: " + period + (isFirst ? " (startup)" : ""));
      await settlePending(latest);
      // 短轮询催生下期四项，避免一次傻等 20s 造成页面空白
      const waitTotal = isFirst ? 3000 : SEAL_WAIT_MS;
      const step = 2000;
      for (let waited = 0; waited <= waitTotal; waited += step) {
        if (waited > 0) await sleep(step);
        mainNeedsRetry = true;
        await computeForCurrentPrediction(lastSeenPeriod);
        if (own.pending.length) break;
      }
    } else if (own.pending.some((p) => String(p.targetPeriod || "") <= period)) {
      // 轮询间隙的补结算（重启找回等场景）
      await settlePending(latest);
      mainNeedsRetry = true;
      await computeForCurrentPrediction(lastSeenPeriod);
    } else {
      await computeForCurrentPrediction(lastSeenPeriod);
    }
    await backfillFormalAnyTwoOnPending();
  } catch (err) {
    log("tick error: " + (err && err.stack || err));
  } finally {
    busy = false;
  }
}

/** 已挂起但缺正式二数合的快照就地补齐（方案 B 上线后的一期过渡）。 */
async function backfillFormalAnyTwoOnPending() {
  const need = (own.pending || []).filter((p) => p && !p.formalAnyTwo);
  if (!need.length) return;
  const historyRows = await fetchHistoryRows(25000);
  if (!historyRows.length) return;
  let changed = false;
  for (const entry of need) {
    try {
      const trio = (entry.task4 && entry.task4.trio) || [0, 1, 2];
      const built = engine.buildFormalAnyTwo(historyRows, trio, entry.targetPeriod || entry.basePeriod);
      if (!built) continue;
      entry.formalAnyTwo = built;
      changed = true;
      log("backfilled formalAnyTwo for target=" + entry.targetPeriod + " code=" + built.code);
    } catch (err) {
      log("anyTwo backfill failed: " + err.message);
    }
  }
  if (changed) saveOwn();
}

function renderPage() {
  const agg = own.aggregate || {};
  const ps = own.poolStats || {};
  const ts = own.taskStats || {};
  const as = own.anyTwoStats || {};
  const pending = own.pending || [];
  const records = own.records || [];
  const esc = (v) => String(v === undefined || v === null ? "" : v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const POS_CN = { wan: "千", qian: "百", bai: "十", shi: "个" };
  const POS_ORDER = { wan: 0, qian: 1, bai: 2, shi: 3 };
  const HW_CN = { wanQianBai: "千百十尾", qianBaiShi: "百十个尾", wanQianGe: "千十个尾", wanBaiShi: "千十个尾" };
  const taskNums = ["①", "②", "③", "④"];
  function renderTaskBox(task4, titlePrefix) {
    if (!task4 || !Array.isArray(task4.items) || !task4.items.length) return "";
    return `<div class="taskbox"><div class="taskbox-title">${esc(titlePrefix || "四项任务")} · ${esc(task4.trioLabel || "千百十")}三定</div>` +
      task4.items.map((i, idx) =>
        `<div class="taskrow">${taskNums[idx] || "·"} ${esc(i.name)}<em>${i.rate120 != null ? ("近120期 " + esc(i.rate120) + "%") : (i.outcome === true ? "避开" : (i.outcome === false ? "打中" : ""))}</em></div>`
      ).join("") + `</div>`;
  }
  function renderAnyTwoBox(anyTwo) {
    if (!anyTwo || !anyTwo.code) return "";
    return `<div class="anytwobox"><div class="anytwobox-title">独立二数合 · 02468排1码 · ${esc(anyTwo.trioLabel || "")}三定</div>` +
      `<div class="anytworow">二数合${esc(anyTwo.code)}<span class="anytwo-ex">排掉${esc(anyTwo.excludedDigit || "?")}</span>` +
      `<em>${anyTwo.rate120 != null ? ("近120 " + esc(anyTwo.rate120) + "% · ") : ""}结构基线约${esc(anyTwo.structBase || as.baseline || 84.8)}%</em></div></div>`;
  }
  const pendingRows = pending.map((p) => {
    const all = (p.snapshot && p.snapshot.items || []);
    const posKills = all.filter((i) => i.kind === "position")
      .sort((a, b) => (POS_ORDER[a.key] || 0) - (POS_ORDER[b.key] || 0))
      .map((i) => (POS_CN[i.key] || i.key) + "位杀" + i.code);
    const hwKills = all.filter((i) => i.kind === "hewei").map((i) => (HW_CN[i.key] || i.key) + "杀" + i.code);
    const shapes = all.filter((i) => i.kind === "derivedReverseShape").map((i) => "避" + i.code);
    const duishu = all.filter((i) => i.kind === "duishu").map((i) => "除对数" + i.code);
    const killLine = [posKills.join("　"), hwKills.join("　"), shapes.join("　"), duishu.join("　")]
      .filter(Boolean).join("　｜　");
    const items = all.map((i) =>
      `<span class="tag">${esc(i.name)}<em>${esc(i.votes)}票</em></span>`).join(" ");
    const poolInfo = "";
    const taskLine = renderTaskBox(p.task4, "四项任务");
    const anyTwoLine = renderAnyTwoBox(p.formalAnyTwo);
    return `<div class="card pend"><h3>基准期 ${esc(p.basePeriod)} → 预测期 ${esc(p.targetPeriod)} <span class="wait">等待开奖…</span></h3>${taskLine}${anyTwoLine}${poolInfo}<div class="killline">${esc(killLine)}</div><div>${items}</div></div>`;
  }).join("");
  // 空窗占位：刚结算、下期未生成时，继续展示上期四项，避免“暂无待验证”
  let holdRows = "";
  if (!pendingRows) {
    const hold = own.displayHold;
    const latestRec = (records[0] && records[0].task4 && records[0].task4.items && records[0].task4.items.length)
      ? records[0]
      : null;
    const src = (hold && hold.task4 && hold.task4.items && hold.task4.items.length) ? hold : latestRec;
    if (src && src.task4) {
      const t4 = src.task4;
      const a2 = src.formalAnyTwo;
      const base = src.basePeriod || "";
      const target = src.targetPeriod || "";
      const draw = Array.isArray(src.actualNumbers) ? src.actualNumbers.join("") : "";
      holdRows = `<div class="card pend"><h3>基准期 ${esc(base)} → 预测期 ${esc(target)} <span class="wait">已结算，正在生成下期四项…</span></h3>` +
        (draw ? `<div class="sub" style="margin:0 0 8px">开奖 ${esc(draw)}</div>` : "") +
        renderTaskBox(t4, "上期四项") +
        renderAnyTwoBox(a2) +
        `</div>`;
    } else {
      holdRows = '<div class="card pend">暂无待验证快照（等待主系统封存本期预测）</div>';
    }
  }
  const recordRows = records.slice(0, 60).map((r) => {
    const cls = r.success ? "ok" : "bad";
    const badge = r.success ? "✓ 全避开" : "✗ 被打中";
    const t4 = r.task4;
    const taskCell = t4
      ? (t4.success
          ? '<span style="color:#68d391">✓过关</span>'
          : `<span style="color:#fc8181">✗(${esc(t4.passed)}/${esc(t4.total)})</span>`) +
        (Array.isArray(t4.items) && t4.items.length
          ? `<div style="margin-top:4px;line-height:1.55">` +
            t4.items.map((i, idx) => {
              const oc = i.outcome;
              const c = oc === true ? "#68d391" : (oc === false ? "#fc8181" : "#8b98b5");
              return `<div style="color:${c};font-size:12px">${taskNums[idx] || "·"} ${esc(i.name)}</div>`;
            }).join("") + `</div>`
          : "")
      : "-";
    const a2 = r.formalAnyTwo;
    const anyTwoCell = a2
      ? (a2.success ? `<span style="color:#68d391">✓${esc(a2.code)}</span>` : `<span style="color:#fc8181">✗${esc(a2.code)}</span>`)
      : "-";
    const items = (r.items || []).map((i) => {
      const state = i.outcome === true ? "hit-ok" : (i.outcome === false ? "hit-bad" : "hit-na");
      return `<span class="tag ${state}">${esc(i.name)}</span>`;
    }).join(" ");
    return `<tr class="${cls}"><td>${esc(r.targetPeriod)}</td><td>${esc((r.actualNumbers || []).join(""))}</td><td>${taskCell}</td><td>${anyTwoCell}</td><td class="badge">${badge}</td><td>${esc(r.passed)}/${esc(r.total)}</td><td class="items">${items}</td></tr>`;
  }).join("");
  const itemAgg = Object.entries(own.itemAggregate || {}).map(([sig, v]) => {
    const rate = v.total ? ((v.hit / v.total) * 100).toFixed(1) : "-";
    return `<tr><td>${esc(sig)}</td><td>${esc(v.hit)}/${esc(v.total)}</td><td>${esc(rate)}%</td></tr>`;
  }).join("");
  return `<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Claude集成评分 · 实时战绩</title><style>
body{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;background:#0f1420;color:#dfe6f3;margin:0;padding:16px}
h1{font-size:20px;margin:0 0 4px}h3{font-size:14px;margin:0 0 8px}
.sub{color:#8b98b5;font-size:12px;margin-bottom:16px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.stat{background:#1a2233;border-radius:10px;padding:12px 18px;min-width:110px}
.stat b{display:block;font-size:22px;color:#4fd1c5}.stat span{font-size:12px;color:#8b98b5}
.card{background:#1a2233;border-radius:10px;padding:12px;margin-bottom:12px}
.card.pend{border-left:3px solid #f6ad55}
.wait{color:#f6ad55;font-size:12px;font-weight:normal}
.tag{display:inline-block;background:#243049;border-radius:6px;padding:2px 8px;margin:2px;font-size:12px}
.tag em{color:#4fd1c5;font-style:normal;margin-left:4px}
.tag.hit-ok{background:#1d3b2f;color:#68d391}.tag.hit-bad{background:#4a1d24;color:#fc8181}.tag.hit-na{opacity:.5}
.killline{font-size:18px;font-weight:700;color:#f6e05e;margin:4px 0 10px;letter-spacing:1px;line-height:1.6}
.poolline{font-size:14px;color:#9ae6b4;margin:2px 0 8px}
.stat.pool{border:1px solid #4fd1c5}.stat.pool b{font-size:26px}
.stat.task{border:1px solid #f6e05e}.stat.task b{font-size:26px;color:#f6e05e}
.stat.anytwo{border:1px solid #f687b3}.stat.anytwo b{font-size:26px;color:#f687b3}
.taskbox{background:#12233a;border:1px solid #2c4a73;border-radius:10px;padding:10px 14px;margin:6px 0 10px;max-width:440px}
.taskbox-title{font-size:13px;color:#63b3ed;margin-bottom:6px;letter-spacing:1px}
.taskrow{font-size:20px;font-weight:700;color:#e8f0fe;padding:5px 0;border-bottom:1px dashed #24406a;letter-spacing:2px}
.taskrow:last-child{border-bottom:none}
.taskrow em{float:right;font-style:normal;font-size:12px;font-weight:400;color:#8b98b5;margin-top:6px;letter-spacing:0}
.anytwobox{background:#2a1524;border:1px solid #9b2c5a;border-radius:10px;padding:10px 14px;margin:6px 0 10px;max-width:440px}
.anytwobox-title{font-size:13px;color:#f687b3;margin-bottom:6px;letter-spacing:1px}
.anytworow{font-size:22px;font-weight:800;color:#fed7e2;letter-spacing:2px;line-height:1.5}
.anytworow .anytwo-ex{font-size:13px;font-weight:600;color:#fc8181;margin-left:10px;letter-spacing:0}
.anytworow em{float:right;font-style:normal;font-size:12px;font-weight:400;color:#8b98b5;margin-top:8px;letter-spacing:0}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 8px;border-bottom:1px solid #243049;text-align:left;vertical-align:top}
tr.ok .badge{color:#68d391}tr.bad .badge{color:#fc8181}
.items{max-width:520px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style></head><body>
<h1>Claude集成评分 <span style="color:#4fd1c5">claude-ensemble</span></h1>
<div class="sub">每30秒自动刷新 · 更新于 ${esc(own.updatedAt || "-")} · 版本 formal-anytwo-v6 / 四项任务与二数合分轨</div>
<div class="stats">
<div class="stat task"><b>${ts.total ? esc(ts.hit) + "/" + esc(ts.total) : "-"}</b><span>四项任务过关 / 已验证</span></div>
<div class="stat task"><b>${ts.rate === null || ts.rate === undefined ? "-" : esc(ts.rate) + "%"}</b><span>四项过关率（随机基线约${esc(ts.baseline || 63.8)}%）</span></div>
<div class="stat anytwo"><b>${as.total ? esc(as.hit) + "/" + esc(as.total) : "-"}</b><span>二数合命中 / 已验证</span></div>
<div class="stat anytwo"><b>${as.rate === null || as.rate === undefined ? "-" : esc(as.rate) + "%"}</b><span>二数合真实%（结构基线约${esc(as.baseline || 99)}%）</span></div>
<div class="stat"><b>${esc(agg.hit || 0)}/${esc(agg.total || 0)}</b><span>杀码整组全避开</span></div>
<div class="stat"><b>${esc(pending.length)}</b><span>等待开奖验证</span></div>
</div>
${pendingRows || holdRows}
<div class="grid"><div class="card"><h3>最近验证记录（新在前）</h3>
<table><tr><th>期号</th><th>开奖</th><th>四项</th><th>二数合</th><th>杀码</th><th>通过</th><th>条目明细（绿=避开 红=被打中）</th></tr>${recordRows || "<tr><td colspan=7>暂无</td></tr>"}</table></div>
<div class="card"><h3>分条目累计战绩</h3>
<table><tr><th>条目</th><th>避开/总数</th><th>避开率</th></tr>${itemAgg || "<tr><td colspan=3>暂无</td></tr>"}</table></div></div>
</body></html>`;
}

/**
 * 千百十三定 500 组页面：取最新待评期快照，套用当期可确定性过滤的规则
 * （千/百/十位杀、千百十合尾杀、形态与奇偶避开），按历史位置热度取前 500，
 * 个位任意（x 收尾）。纯读内存状态，不落盘、不影响评分主流程。
 */
function renderNumbersPage() {
  const pendList = Array.isArray(own.pending) ? own.pending : [];
  const pend = pendList[pendList.length - 1];
  // 只显示固化池：与开奖结算判定的是同一批号码，杜绝“显示一套、结算另一套”
  if (!pend || !pend.pool || !Array.isArray(pend.pool.codes) || !pend.pool.codes.length) return null;
  const poolMeta = pend.pool;
  const xIndex = Number.isInteger(poolMeta.xIndex) ? poolMeta.xIndex : 3;
  const codes = poolMeta.codes.slice().sort((a, b) => Number(a) - Number(b));
  const tokens = codes.map((c) => engine.formatPoolCode(c, xIndex));
  const posLabel = poolMeta.positionLabel || "千百十三定（个位任意x）";
  const ps = own.poolStats || {};
  const rateText = ps.total ? (ps.hit + "/" + ps.total + " = " + ps.rate + "%") : "暂无（等待首期结算）";
  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${pend.targetPeriod}期 千百十三定 ${tokens.length}组</title>
<style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:20px;background:#fafafa}h2{margin:0 0 4px}
.meta{color:#666;font-size:13px;margin-bottom:14px;line-height:1.6}
button{font-size:18px;padding:10px 28px;cursor:pointer;background:#1a73e8;color:#fff;border:0;border-radius:6px}
#ok{color:#188038;margin-left:12px;font-size:15px}
#nums{margin-top:14px;font-family:Consolas,monospace;font-size:13px;line-height:1.7;background:#fff;border:1px solid #ddd;border-radius:6px;padding:12px;word-break:break-all;user-select:all}</style></head>
<body><h2>${pend.targetPeriod} 期 · ${posLabel} · ${tokens.length} 组</h2>
<div class="meta"><b>本期池已固化</b>（开奖后按同一批号码自动结算，不可事后更改） · 位置由当期数据自动选定：可预测性最弱的位置控出（默认 X，可切 +） · 杀码过滤 ${poolMeta.killedByItems} 组${poolMeta.taskAligned ? "、四项任务再删 " + poolMeta.taskRemoved + " 组" : ""}${tokens.length >= poolMeta.survivors ? "，幸存 " + poolMeta.survivors + " 组全部给出（四项全过=必中池）" : "，剩 " + poolMeta.survivors + " 组按3000期历史频率取前 " + tokens.length + " 组"} · 快照生成: ${pend.generatedAt}<br><span style="color:#188038;font-weight:700">号码池历史胜率: ${rateText}</span>（随机基线 ${ps.baseline || "-"}%，按池大小动态计算）</div>
<button onclick="copyAll()">一键复制全部</button>
<button style="background:#5f6368" onclick="setPh('X')">X 格式</button>
<button style="background:#5f6368" onclick="setPh('+')">＋ 格式</button><span id="ok"></span>
<div id="nums">${tokens.join(", ")}</div>
<script>
var currentPh="X";
function setPh(p){
  if(p===currentPh)return;
  var el=document.getElementById("nums");
  el.innerText=el.innerText.split(currentPh).join(p);
  currentPh=p;
  document.getElementById("ok").textContent="已切换为 "+p+" 格式";
}
function copyAll(){
  var text=document.getElementById("nums").innerText;
  var okEl=document.getElementById("ok");
  function done(){okEl.textContent="已复制 ${tokens.length} 组 ✓";}
  function fallback(){
    var ta=document.createElement("textarea");
    ta.value=text;ta.style.position="fixed";ta.style.left="-9999px";
    document.body.appendChild(ta);ta.focus();ta.select();
    try{document.execCommand("copy");done();}catch(e){okEl.textContent="自动复制失败，请长按号码区全选复制";}
    document.body.removeChild(ta);
  }
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(done).catch(fallback);}else{fallback();}
}
</script></body></html>`;
}

function startWebServer() {
  const packFile = path.join(DATA_DIR, "jiu-robot-pack.tar.gz");
  const server = http.createServer((req, res) => {
  /* PANEL_ROUTE_TO_PANEL_HTML_PATCH */
  try {
    const __panelUrl = new URL(req.url || "/", "http://127.0.0.1");
    if (req.method === "GET" && (__panelUrl.pathname === "/panel" || __panelUrl.pathname === "/panel.html")) {
      const __fs = require("fs");
      const __path = require("path");
      const __panelPath = __path.join(REPO_ROOT, "panel.html");
      const __panelHtml = __fs.readFileSync(__panelPath, "utf8");
      res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store"
      });
      res.end(__panelHtml);
      return;
    }
  } catch (__panelErr) {
    console.error("panel route error:", __panelErr && __panelErr.message ? __panelErr.message : __panelErr);
  }


    const url = String(req.url || "/");
    if (url.startsWith("/state")) {
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(own));
      return;
    }
    if (url.startsWith("/500")) {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="font-family:system-ui;background:#0f1420;color:#dfe6f3;padding:24px">500组号码池功能已按要求停用，请看主页的四项任务。<a href="/" style="color:#4fd1c5">返回</a></body></html>');
      return;
    }
    // 临时：本机锁定包下载（Windows 桌面用）
    if (url.startsWith("/jiu-robot-pack.tar.gz") || url.startsWith("/救机器人包.tar.gz")) {
      if (!fs.existsSync(packFile)) {
        res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
        res.end("pack missing");
        return;
      }
      const buf = fs.readFileSync(packFile);
      res.writeHead(200, {
        "Content-Type": "application/gzip",
        "Content-Length": buf.length,
        "Content-Disposition": 'attachment; filename="jiu-robot-pack.tar.gz"'
      });
      res.end(buf);
      return;
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(renderPage());
  });
  server.on("error", (err) => log("web server error: " + err.message));
  server.listen(WEB_PORT, "0.0.0.0", () => log("web ui listening on :" + WEB_PORT));
}

async function main() {
  const once = process.argv.includes("--once");
  fs.mkdirSync(OWN_DIR, { recursive: true });
  log("claude-ensemble sidecar v2 starting | poll=" + POLL_MS + "ms | once=" + once);
  await tick();
  if (once) {
    log("once mode done. pending=" + own.pending.length + " records=" + own.records.length);
    return;
  }
  startWebServer();
  setInterval(tick, POLL_MS);
}

main().catch((err) => {
  console.error("fatal:", err);
  process.exit(1);
});
