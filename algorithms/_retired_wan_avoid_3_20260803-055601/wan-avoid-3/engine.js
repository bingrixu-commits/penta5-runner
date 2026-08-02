"use strict";
/**
 * 千位避开3组 —— 对标形态避开3组 / 对数避开3组。
 * 只杀千位（index=0），按 30/60/120/300/1000 多窗稳度取 Top3 单码。
 * 成功：开奖千位 ≠ 该组 code。
 */
const fs = require("fs");
const path = require("path");
const http = require("http");
const {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  MODEL_LOW_HIT_RATE,
  stableWindowScore,
  wilsonLowerRate
} = require("../../_shared/formula_kit");

const MODEL_ID = "wan-avoid-3";
const GROUP_N = Math.max(1, Number(process.env.WAN_AVOID_GROUPS || 3));
const POS_INDEX = 0;
const POS_LABEL = "千";
const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const RATE_WINDOWS = Object.freeze([30, 60, 120, MODEL_STABLE_WINDOW, MODEL_LONG_WINDOW]);

function httpGetJson(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs || 15000 }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
        catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

function asHistory(rows) {
  return (Array.isArray(rows) ? rows : [])
    .filter((r) => r && Array.isArray(r.numbers) && r.numbers.length >= 4)
    .map((r) => ({
      period: String(r.period || r.uid || ""),
      numbers: r.numbers.slice(0, 5).map(String),
      opentime: r.opentime || ""
    }));
}

async function fetchHistory(limit) {
  const lim = Number(limit || 1500);
  const urls = [
    process.env.WAN_HISTORY_URL || ("http://127.0.0.1:8787/history?limit=" + lim),
    "http://127.0.0.1:8787/history?limit=800",
    "http://127.0.0.1:3001/history?limit=500"
  ];
  for (const url of urls) {
    try {
      const data = await httpGetJson(url, 20000);
      const rows = Array.isArray(data) ? data
        : (Array.isArray(data && data.history) ? data.history
          : (Array.isArray(data && data.rows) ? data.rows : []));
      if (rows.length) return asHistory(rows);
    } catch (_) { /* next */ }
  }
  return [];
}

function digitRateMultiWindow(digit, records, index) {
  const lims = RATE_WINDOWS.slice();
  const maxLimit = Math.max(...lims);
  const sliced = (records || []).slice(0, maxLimit);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0, _currentRun: 0, _leading: true };
  });
  for (let i = 0; i < sliced.length; i += 1) {
    const actual = sliced[i].numbers[index];
    if (!/^\d$/.test(String(actual))) continue;
    const ok = String(actual) !== String(digit);
    lims.forEach((limit) => {
      if (i >= limit) return;
      const result = results[limit];
      result.total += 1;
      if (ok) {
        result.hit += 1;
        result._currentRun = 0;
        result._leading = false;
      } else {
        result._currentRun += 1;
        if (result._currentRun > result.maxMissStreak) result.maxMissStreak = result._currentRun;
        if (result._leading) result.missStreak += 1;
      }
    });
  }
  lims.forEach((limit) => {
    const result = results[limit];
    result.rate = result.total ? (result.hit * 100) / result.total : 0;
    delete result._currentRun;
    delete result._leading;
  });
  return results;
}

function scoreDigit(digit, rateWindows) {
  const r30 = rateWindows[30] || { total: 0, hit: 0, rate: 0, missStreak: 0 };
  const r60 = rateWindows[60] || r30;
  const r120 = rateWindows[120] || r60;
  const r300 = rateWindows[MODEL_STABLE_WINDOW] || r120;
  const rLong = rateWindows[MODEL_LONG_WINDOW] || r300;
  const weighted = stableWindowScore(r30, r60, r120, r300, rLong, 3.2);
  const lower120 = wilsonLowerRate(r120.hit, r120.total, 1.44);
  const lowerLong = wilsonLowerRate(rLong.hit, rLong.total, 1.44);
  let score = weighted.score;
  if (Number(r30.rate || 0) < MODEL_LOW_HIT_RATE) score -= 2.4;
  if (Number(r30.missStreak || 0) >= 2) score -= 1.8;
  score += Math.max(0, lower120 - 88) * 0.08;
  score += Math.max(0, lowerLong - 88) * 0.06;
  return {
    digit: String(digit),
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    rate30: Number(Number(r30.rate || 0).toFixed(2)),
    rate60: Number(Number(r60.rate || 0).toFixed(2)),
    rate120: Number(Number(r120.rate || 0).toFixed(2)),
    rate300: Number(Number(r300.rate || 0).toFixed(2)),
    rateLong: Number(Number(rLong.rate || 0).toFixed(2)),
    lower120: Number(lower120.toFixed(2)),
    lowerLong: Number(lowerLong.toFixed(2)),
    hit120: Number(r120.hit || 0),
    total120: Number(r120.total || 0),
    hitLong: Number(rLong.hit || 0),
    totalLong: Number(rLong.total || 0),
    missStreak30: Number(r30.missStreak || 0)
  };
}

function rankWanTop(history, topN) {
  const want = Math.max(1, Number(topN || GROUP_N));
  const safe = asHistory(history);
  const candidates = DIGIT_POOL.map((digit) => {
    const windows = digitRateMultiWindow(digit, safe, POS_INDEX);
    return scoreDigit(digit, windows);
  });
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.lowerLong !== a.lowerLong) return b.lowerLong - a.lowerLong;
    if (b.rateLong !== a.rateLong) return b.rateLong - a.rateLong;
    if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
    return String(a.digit).localeCompare(String(b.digit));
  });
  return candidates.slice(0, want).map((item, idx) => Object.assign({}, item, {
    group: idx + 1,
    groupLabel: "千位避开" + (idx + 1) + "组",
    code: item.digit,
    attack: "除千" + item.digit
  }));
}

function buildState(history, groups) {
  return {
    version: MODEL_ID + "-v1",
    kind: "wanAvoidGroups",
    id: MODEL_ID,
    label: "千位避开" + GROUP_N + "组",
    parallelTo: "形态避开3组 / 对数避开3组",
    position: POS_LABEL,
    positionIndex: POS_INDEX,
    longWindow: MODEL_LONG_WINDOW,
    groupCount: groups.length,
    generatedAt: new Date().toISOString(),
    historyCount: history.length,
    latestPeriod: history[0] && history[0].period,
    groups: groups.map((g) => ({
      key: "wanAvoid" + g.group,
      group: g.group,
      name: g.groupLabel,
      code: g.code,
      attack: g.attack,
      score: g.score,
      stage: g.stage,
      rate30: g.rate30,
      rate120: g.rate120,
      rateLong: g.rateLong,
      lower120: g.lower120,
      lowerLong: g.lowerLong,
      hit120: g.hit120,
      total120: g.total120,
      hitLong: g.hitLong,
      totalLong: g.totalLong,
      missStreak30: g.missStreak30
    })),
    shot: groups.map((g) => g.attack).filter(Boolean).join(""),
    note: "千位避开" + GROUP_N + "组：多窗含近" + MODEL_LONG_WINDOW + "期稳度 Top" + GROUP_N + "；各组独立结算"
  };
}

async function refresh() {
  const history = await fetchHistory(Number(process.env.WAN_HISTORY_LIMIT || 1500));
  if (!history.length) throw new Error("history empty");
  const groups = rankWanTop(history, GROUP_N);
  const state = buildState(history, groups);
  const dir = path.join(REPO_ROOT, "data", "algorithm-projects", MODEL_ID);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify(state, null, 2));
  return state;
}

module.exports = { MODEL_ID, GROUP_N, refresh, rankWanTop, buildState };
