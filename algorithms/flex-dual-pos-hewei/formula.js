"use strict";

/**
 * 灵活二位排除（千百 / 百十合尾驱动）
 * - 四个位置（千/百/十/个）中选 2 个
 * - 每个选中位置排除 1 个数字
 * - 用「千百合尾」「百十合尾」做工况罗盘，连错时切换选位模式（像人脑换思路）
 * 只用共享 history，不读其它算法 state，不进 tipReady 核心门禁。
 */

const {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  MODEL_LOW_HIT_RATE,
  stableWindowStage,
  stableWindowScore,
  wilsonLowerRate
} = require("../_shared/formula_kit");

const VERSION = "flex-dual-pos-hewei-v1";
const LABEL = "灵活二位排除(千百/百十合尾)";
const KIND = "flexDualPosHewei";

const POSITION_LIST = Object.freeze([
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 }
]);

const HEWEI_DRIVERS = Object.freeze([
  {
    key: "qianBaiHewei",
    label: "千百合尾",
    indexes: [0, 1],
    affinity: ["wan", "qian"]
  },
  {
    key: "baiShiHewei",
    label: "百十合尾",
    indexes: [1, 2],
    affinity: ["qian", "bai"]
  }
]);

const RATE_WINDOWS = Object.freeze([30, 60, 120, MODEL_STABLE_WINDOW, MODEL_LONG_WINDOW]);
const PAIR_KEYS = Object.freeze([
  ["wan", "qian"],
  ["wan", "bai"],
  ["wan", "shi"],
  ["qian", "bai"],
  ["qian", "shi"],
  ["bai", "shi"]
]);

function asHistory(history) {
  return Array.isArray(history)
    ? history.filter((row) => row && Array.isArray(row.numbers) && row.numbers.length >= 4)
    : [];
}

function actualDigit(record, index) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  return nums[index] === undefined || nums[index] === null ? "" : String(nums[index]);
}

function heweiOf(record, indexes) {
  const a = actualDigit(record, indexes[0]);
  const b = actualDigit(record, indexes[1]);
  if (!/^\d$/.test(a) || !/^\d$/.test(b)) return "";
  return String((Number(a) + Number(b)) % 10);
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
  const weighted = stableWindowScore(r30, r60, r120, r300, rLong, 3.0);
  const lower120 = wilsonLowerRate(r120.hit, r120.total, 1.44);
  let score = weighted.score;
  if (Number(r30.rate || 0) < MODEL_LOW_HIT_RATE) score -= 2.2;
  if (Number(r30.missStreak || 0) >= 2) score -= 1.6;
  score += Math.max(0, lower120 - 88) * 0.07;
  return {
    digit: String(digit),
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    r30,
    r60,
    r120,
    r300,
    rLong,
    lower120: Number(lower120.toFixed(2))
  };
}

function pickPositionExclude(history, position) {
  const safeHistory = asHistory(history);
  const index = Number(position.index);
  const candidates = DIGIT_POOL.map((digit) => {
    const windows = digitRateMultiWindow(digit, safeHistory, index, RATE_WINDOWS);
    return scoreDigit(digit, windows);
  });
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
    return String(a.digit).localeCompare(String(b.digit));
  });
  const best = candidates[0] || null;
  const backup = candidates.find((item) => item && best && item.digit !== best.digit) || null;
  return {
    key: position.key,
    label: position.label,
    index,
    code: best && /^\d$/.test(best.digit) ? best.digit : "",
    backup: backup && /^\d$/.test(backup.digit) ? backup.digit : "",
    score: best ? best.score : 0,
    stage: best ? best.stage : stableWindowStage(0),
    rate30: best ? Number(Number(best.r30.rate || 0).toFixed(2)) : null,
    rate120: best ? Number(Number(best.r120.rate || 0).toFixed(2)) : null,
    lower120: best ? best.lower120 : null,
    missStreak30: best ? Number(best.r30.missStreak || 0) : 0
  };
}

function heweiKillScore(history, driver) {
  const safeHistory = asHistory(history);
  const candidates = DIGIT_POOL.map((digit) => {
    const lims = [30, 60, 120];
    const maxLimit = 120;
    const sliced = safeHistory.slice(0, maxLimit);
    const windows = {};
    lims.forEach((limit) => {
      windows[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, _currentRun: 0, _leading: true };
    });
    for (let i = 0; i < sliced.length; i += 1) {
      const hw = heweiOf(sliced[i], driver.indexes);
      if (!/^\d$/.test(hw)) continue;
      const ok = hw !== String(digit);
      lims.forEach((limit) => {
        if (i >= limit) return;
        const result = windows[limit];
        result.total += 1;
        if (ok) {
          result.hit += 1;
          result._currentRun = 0;
          result._leading = false;
        } else {
          result._currentRun += 1;
          if (result._leading) result.missStreak += 1;
        }
      });
    }
    lims.forEach((limit) => {
      const result = windows[limit];
      result.rate = result.total ? (result.hit * 100) / result.total : 0;
      delete result._currentRun;
      delete result._leading;
    });
    return scoreDigit(digit, {
      30: windows[30],
      60: windows[60],
      120: windows[120],
      [MODEL_STABLE_WINDOW]: windows[120],
      [MODEL_LONG_WINDOW]: windows[120]
    });
  });
  candidates.sort((a, b) => b.score - a.score);
  const best = candidates[0] || null;
  const latest = safeHistory[0] ? heweiOf(safeHistory[0], driver.indexes) : "";
  return {
    key: driver.key,
    label: driver.label,
    killDigit: best && best.digit || "",
    score: best ? best.score : 0,
    stage: best ? best.stage : stableWindowStage(0),
    rate30: best ? Number(Number(best.r30.rate || 0).toFixed(2)) : null,
    rate120: best ? Number(Number(best.r120.rate || 0).toFixed(2)) : null,
    missStreak30: best ? Number(best.r30.missStreak || 0) : 0,
    latestHewei: latest,
    affinity: driver.affinity.slice()
  };
}

function chooseRegime(heweiSignals, positionPicks) {
  const qb = heweiSignals.qianBaiHewei || {};
  const bs = heweiSignals.baiShiHewei || {};
  const miss = Math.max(Number(qb.missStreak30 || 0), Number(bs.missStreak30 || 0));
  const weakPos = positionPicks.filter((p) => Number(p.missStreak30 || 0) >= 2).length;
  if (miss >= 3 || weakPos >= 3) return "storm-rotate";
  if (miss >= 2 || Number(qb.rate30 || 100) < 86 || Number(bs.rate30 || 100) < 86) return "hewei-led";
  if (Number(qb.score || 0) + Number(bs.score || 0) >= 0) return "mixed";
  return "score-led";
}

function pairAffinityBonus(pairKeys, heweiSignals, regime) {
  const set = new Set(pairKeys);
  const qb = heweiSignals.qianBaiHewei || {};
  const bs = heweiSignals.baiShiHewei || {};
  let bonus = 0;
  const qbHit = qb.affinity && qb.affinity.filter((k) => set.has(k)).length;
  const bsHit = bs.affinity && bs.affinity.filter((k) => set.has(k)).length;
  if (regime === "hewei-led") {
    bonus += qbHit * (1.8 + Math.max(0, Number(qb.score || 0)) * 0.02);
    bonus += bsHit * (1.8 + Math.max(0, Number(bs.score || 0)) * 0.02);
    if (qbHit >= 2) bonus += 1.2;
    if (bsHit >= 2) bonus += 1.2;
  } else if (regime === "mixed") {
    bonus += qbHit * 1.1;
    bonus += bsHit * 1.1;
  } else if (regime === "storm-rotate") {
    // 风暴：故意降低「总是千百/百十粘连」的惯性，给边角位机会
    if (qbHit >= 2) bonus -= 0.8;
    if (bsHit >= 2) bonus -= 0.8;
    if (set.has("wan") && set.has("shi")) bonus += 1.4;
    if (set.has("wan") && set.has("bai")) bonus += 0.6;
  }
  return bonus;
}

function simulatePairHitRate(history, pairKeys, byKey, limit) {
  const safeHistory = asHistory(history);
  const n = Math.min(safeHistory.length, Math.max(20, Number(limit || 60)));
  let total = 0;
  let hit = 0;
  let missStreak = 0;
  let leading = true;
  for (let i = 0; i < n; i += 1) {
    const row = safeHistory[i];
    let ok = true;
    for (let p = 0; p < pairKeys.length; p += 1) {
      const pos = byKey[pairKeys[p]];
      if (!pos || !/^\d$/.test(pos.code)) {
        ok = false;
        break;
      }
      if (actualDigit(row, pos.index) === pos.code) ok = false;
    }
    total += 1;
    if (ok) {
      hit += 1;
      leading = false;
    } else if (leading) {
      missStreak += 1;
    }
  }
  return {
    total,
    hit,
    rate: total ? Number(((hit * 100) / total).toFixed(2)) : 0,
    missStreak
  };
}

function buildSnapshot(history) {
  const safeHistory = asHistory(history);
  const latest = safeHistory[0] || null;
  const positionPicks = POSITION_LIST.map((pos) => pickPositionExclude(safeHistory, pos));
  const byKey = {};
  positionPicks.forEach((item) => { byKey[item.key] = item; });

  const heweiSignals = {};
  HEWEI_DRIVERS.forEach((driver) => {
    heweiSignals[driver.key] = heweiKillScore(safeHistory, driver);
  });

  const regime = chooseRegime(heweiSignals, positionPicks);
  const pairCandidates = PAIR_KEYS.map((keys) => {
    const a = byKey[keys[0]];
    const b = byKey[keys[1]];
    const baseScore = Number(a && a.score || 0) + Number(b && b.score || 0);
    const affinity = pairAffinityBonus(keys, heweiSignals, regime);
    const sim = simulatePairHitRate(safeHistory, keys, byKey, 60);
    let score = baseScore + affinity + (sim.rate - 80) * 0.04;
    if (sim.missStreak >= 2) score -= 1.5;
    if (Number(a && a.missStreak30 || 0) >= 2) score -= 0.8;
    if (Number(b && b.missStreak30 || 0) >= 2) score -= 0.8;
    return {
      keys: keys.slice(),
      labels: [a && a.label || keys[0], b && b.label || keys[1]],
      score: Number(score.toFixed(4)),
      baseScore: Number(baseScore.toFixed(4)),
      affinity: Number(affinity.toFixed(4)),
      sim60: sim
    };
  });

  pairCandidates.sort((x, y) => {
    if (y.score !== x.score) return y.score - x.score;
    if (Number(y.sim60.rate || 0) !== Number(x.sim60.rate || 0)) return Number(y.sim60.rate || 0) - Number(x.sim60.rate || 0);
    return String(x.keys.join("-")).localeCompare(String(y.keys.join("-")));
  });

  // 风暴模式：若第一名近期连错，改用第二名（灵活换位）
  let chosen = pairCandidates[0] || null;
  if (regime === "storm-rotate" && pairCandidates[1] && chosen && Number(chosen.sim60.missStreak || 0) >= 2) {
    chosen = pairCandidates[1];
  }

  const items = (chosen && chosen.keys || []).map((key) => {
    const src = byKey[key] || {};
    return {
      key: src.key || key,
      label: src.label || key,
      index: src.index,
      code: src.code || "",
      backup: src.backup || "",
      score: src.score,
      stage: src.stage,
      rate30: src.rate30,
      rate120: src.rate120,
      lower120: src.lower120,
      missStreak30: src.missStreak30,
      reason: "flex-dual-pos:" + regime
    };
  });

  const basePeriod = latest && latest.period != null ? String(latest.period) : "";
  return {
    version: VERSION,
    kind: KIND,
    label: LABEL,
    algorithm: "flex-dual-pos-hewei",
    basePeriod,
    generatedAt: new Date().toISOString(),
    regime,
    heweiDrivers: {
      qianBaiHewei: heweiSignals.qianBaiHewei || null,
      baiShiHewei: heweiSignals.baiShiHewei || null
    },
    selectedPair: chosen
      ? {
          keys: chosen.keys,
          labels: chosen.labels,
          score: chosen.score,
          sim60: chosen.sim60
        }
      : null,
    items,
    itemCount: items.length,
    pairCandidates: pairCandidates.slice(0, 6),
    positionPool: positionPicks.map((p) => ({
      key: p.key,
      label: p.label,
      code: p.code,
      score: p.score,
      missStreak30: p.missStreak30
    })),
    complete: items.length === 2 && items.every((it) => /^\d$/.test(it.code)),
    note: "四位取二、各位排1码；千百/百十合尾驱动工况，不进 tipReady 核心门禁"
  };
}

function snapshotIsComplete(snapshot) {
  const snap = snapshot && typeof snapshot === "object" ? snapshot : null;
  if (!snap) return false;
  if (!Array.isArray(snap.items) || snap.items.length !== 2) return false;
  return snap.items.every((it) => it && /^\d$/.test(String(it.code || "")) && it.key);
}

module.exports = {
  VERSION,
  LABEL,
  KIND,
  POSITION_LIST,
  HEWEI_DRIVERS,
  buildSnapshot,
  snapshotIsComplete
};
