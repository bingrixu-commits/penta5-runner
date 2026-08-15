"use strict";
/**
 * 四定反车2号 · 共丢 8 个（位+码）· 分位不固定
 * 丢掉后剩余号码直接接转发器（约 4300–4800 组量级）。
 * 命中（丢侧）：8 条排除都成立（该位开奖码 ≠ 丢掉码）。
 */
const fs = require("fs");
const path = require("path");
const lib = require("../../ai/_shape_lib");
const {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  MODEL_LOW_HIT_RATE,
  stableWindowScore,
  wilsonLowerRate
} = require("../../_shared/formula_kit");

const MODEL_ID = "four-fan-model-2";
const DROP_N = Math.max(1, Math.min(20, Number(process.env.FOUR_FAN_MODEL2_DROP_N || 8)));
const VERSION = MODEL_ID + "-v1-drop8-remain";

const POSITION_LIST = Object.freeze([
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 }
]);

const RATE_WINDOWS = Object.freeze([
  30,
  60,
  120,
  MODEL_STABLE_WINDOW,
  MODEL_LONG_WINDOW
]);

function outPaths() {
  const dir = path.join(lib.REPO_ROOT, "data", "algorithm-projects", MODEL_ID);
  return { dir, file: path.join(dir, "state.json") };
}

function actualDigit(record, index) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  return nums[index] === undefined || nums[index] === null ? "" : String(nums[index]);
}

function digitRateMultiWindow(digit, records, index, limits) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((n) => Math.max(1, Number(n || 120))))];
  const maxLimit = Math.max(...lims);
  const sliced = (records || []).slice(0, maxLimit);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0, _currentRun: 0, _leading: true };
  });
  for (let i = 0; i < sliced.length; i += 1) {
    const actual = actualDigit(sliced[i], index);
    if (!/^\d$/.test(actual)) continue;
    const ok = actual !== String(digit);
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
  const lower300 = wilsonLowerRate(r300.hit, r300.total, 1.44);
  const lowerLong = wilsonLowerRate(rLong.hit, rLong.total, 1.44);
  let score = weighted.score;
  if (Number(r30.rate || 0) < MODEL_LOW_HIT_RATE) score -= 2.4;
  if (Number(r30.missStreak || 0) >= 2) score -= 1.8;
  score += Math.max(0, lowerLong - 88) * 0.16;
  score += Math.max(0, Number(rLong.rate || 0) - 90) * 0.10;
  score += Math.max(0, lower300 - 88) * 0.06;
  score += Math.max(0, lower120 - 88) * 0.04;
  return {
    digit: String(digit),
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    r30,
    r60,
    r120,
    r300,
    rLong,
    lower120: Number(lower120.toFixed(2)),
    lower300: Number(lower300.toFixed(2)),
    lowerLong: Number(lowerLong.toFixed(2))
  };
}

function enumerateCandidates(history) {
  const safe = Array.isArray(history) ? history : [];
  const out = [];
  POSITION_LIST.forEach((pos) => {
    DIGIT_POOL.forEach((digit) => {
      const windows = digitRateMultiWindow(digit, safe, pos.index, RATE_WINDOWS);
      const scored = scoreDigit(digit, windows);
      out.push({
        key: pos.key,
        label: pos.label,
        index: pos.index,
        digit: scored.digit,
        id: pos.key + ":" + scored.digit,
        score: scored.score,
        stage: scored.stage,
        rate30: Number(Number(scored.r30.rate || 0).toFixed(2)),
        rate60: Number(Number(scored.r60.rate || 0).toFixed(2)),
        rate120: Number(Number(scored.r120.rate || 0).toFixed(2)),
        rate300: Number(Number(scored.r300.rate || 0).toFixed(2)),
        rateLong: Number(Number(scored.rLong.rate || 0).toFixed(2)),
        lower120: scored.lower120,
        lower300: scored.lower300,
        lowerLong: scored.lowerLong,
        hit120: Number(scored.r120.hit || 0),
        total120: Number(scored.r120.total || 0),
        missStreak30: Number(scored.r30.missStreak || 0),
        sampleLong: Number(scored.rLong.total || 0),
        attack: pos.label + "除" + scored.digit
      });
    });
  });
  out.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (Number(b.lowerLong || 0) !== Number(a.lowerLong || 0)) return Number(b.lowerLong || 0) - Number(a.lowerLong || 0);
    if (Number(b.rateLong || 0) !== Number(a.rateLong || 0)) return Number(b.rateLong || 0) - Number(a.rateLong || 0);
    if (Number(b.lower120 || 0) !== Number(a.lower120 || 0)) return Number(b.lower120 || 0) - Number(a.lower120 || 0);
    if (a.index !== b.index) return a.index - b.index;
    return String(a.digit).localeCompare(String(b.digit));
  });
  return out;
}

function pickDrops(history, n) {
  const want = Math.max(1, Number(n || DROP_N));
  const candidates = enumerateCandidates(history);
  const picked = [];
  const used = new Set();
  for (let i = 0; i < candidates.length && picked.length < want; i += 1) {
    const item = candidates[i];
    if (!item || used.has(item.id)) continue;
    used.add(item.id);
    picked.push(item);
  }
  return picked;
}

function killsByPosition(kills) {
  const map = { wan: [], qian: [], bai: [], shi: [] };
  (kills || []).forEach((k) => {
    if (k && map[k.key]) map[k.key].push(String(k.digit));
  });
  return map;
}

function remainCountForKills(kills) {
  const by = killsByPosition(kills);
  const sizes = POSITION_LIST.map((pos) => {
    const set = new Set(by[pos.key] || []);
    return Math.max(0, 10 - set.size);
  });
  return sizes.reduce((acc, n) => acc * n, 1);
}

function codeSurvives(code, kills) {
  const s = String(code || "");
  if (!/^\d{4}$/.test(s)) return false;
  for (let i = 0; i < (kills || []).length; i += 1) {
    const k = kills[i];
    if (!k) continue;
    const idx = Number(k.index);
    if (!Number.isFinite(idx) || idx < 0 || idx > 3) continue;
    if (s.charAt(idx) === String(k.digit)) return false;
  }
  return true;
}

function buildRemainCodes(kills) {
  const out = [];
  for (let n = 0; n < 10000; n += 1) {
    const code = String(n).padStart(4, "0");
    if (codeSurvives(code, kills)) out.push(code);
  }
  return out;
}

function jointDropOk(record, kills) {
  for (let i = 0; i < (kills || []).length; i += 1) {
    const k = kills[i];
    if (!k) return false;
    const actual = actualDigit(record, k.index);
    if (!/^\d$/.test(actual)) return false;
    if (actual === String(k.digit)) return false;
  }
  return true;
}

function jointStats(history, kills, limit) {
  const lim = Math.max(1, Number(limit || 120));
  const sliced = (history || []).slice(0, lim);
  let total = 0;
  let hit = 0;
  let missStreak = 0;
  let leading = true;
  let cur = 0;
  let maxMiss = 0;
  for (let i = 0; i < sliced.length; i += 1) {
    const ok = jointDropOk(sliced[i], kills);
    total += 1;
    if (ok) {
      hit += 1;
      cur = 0;
      leading = false;
    } else {
      cur += 1;
      if (cur > maxMiss) maxMiss = cur;
      if (leading) missStreak += 1;
    }
  }
  return {
    total,
    hit,
    rate: total ? Number(((hit * 100) / total).toFixed(2)) : 0,
    missStreak,
    maxMissStreak: maxMiss,
    lower: Number(wilsonLowerRate(hit, total, 1.44).toFixed(2))
  };
}

function buildState(history, kills) {
  const byPos = killsByPosition(kills);
  const remainCount = remainCountForKills(kills);
  const j120 = jointStats(history, kills, 120);
  const j300 = jointStats(history, kills, MODEL_STABLE_WINDOW);
  const jLong = jointStats(history, kills, MODEL_LONG_WINDOW);
  const posRemain = POSITION_LIST.map((pos) => ({
    key: pos.key,
    label: pos.label,
    dropDigits: (byPos[pos.key] || []).slice(),
    dropCount: (byPos[pos.key] || []).length,
    remainDigits: 10 - (byPos[pos.key] || []).length
  }));
  return {
    ok: true,
    version: VERSION,
    kind: "fourFanModel2Remain",
    id: MODEL_ID,
    label: "四定反车2号模型剩号",
    side: "reverse",
    mark: "四定反车2号模型剩号",
    dropN: DROP_N,
    generatedAt: new Date().toISOString(),
    historyCount: history.length,
    latestPeriod: history[0] && history[0].period,
    longWindow: MODEL_LONG_WINDOW,
    kills: (kills || []).map((k, i) => ({
      rank: i + 1,
      key: k.key,
      label: k.label,
      index: k.index,
      digit: k.digit,
      id: k.id,
      score: k.score,
      stage: k.stage,
      rate30: k.rate30,
      rate60: k.rate60,
      rate120: k.rate120,
      rate300: k.rate300,
      rateLong: k.rateLong,
      lower120: k.lower120,
      lowerLong: k.lowerLong,
      hit120: k.hit120,
      total120: k.total120,
      missStreak30: k.missStreak30,
      attack: k.attack
    })),
    positions: posRemain,
    remainCount,
    fullCount: 10000,
    joint: {
      rate120: j120.rate,
      hit120: j120.hit,
      total120: j120.total,
      lower120: j120.lower,
      missStreak120: j120.missStreak,
      rate300: j300.rate,
      hit300: j300.hit,
      total300: j300.total,
      rateLong: jLong.rate,
      hitLong: jLong.hit,
      totalLong: jLong.total,
      lowerLong: jLong.lower
    },
    shot: (kills || []).map((k) => k.attack).filter(Boolean).join(""),
    note: "标记「四定反车2号模型剩号」：四定从40路（位+码）打分取 top" + DROP_N + " 丢掉；剩余号码直接接转发器，不经回删；分位不固定"
  };
}

async function refresh() {
  const history = await lib.fetchHistory(Number(process.env.FOUR_FAN_MODEL2_HISTORY_LIMIT || 1500));
  if (!history.length) throw new Error("history empty");
  const kills = pickDrops(history, DROP_N);
  if (kills.length < DROP_N) throw new Error("not enough drop candidates");
  const state = buildState(history, kills);
  const { dir, file } = outPaths();
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(state, null, 2));
  return state;
}

function loadState() {
  const { file } = outPaths();
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return null;
  }
}

function remainCodesFromState(state) {
  const kills = state && Array.isArray(state.kills) ? state.kills : [];
  return buildRemainCodes(kills);
}

module.exports = {
  MODEL_ID,
  DROP_N,
  VERSION,
  POSITION_LIST,
  refresh,
  buildState,
  pickDrops,
  buildRemainCodes,
  remainCountForKills,
  remainCodesFromState,
  loadState,
  outPaths,
  codeSurvives
};
