"use strict";

/**
 * Shared helpers for 千百个 / 千十个 independent hewei algorithms.
 * Positions: wan=千(0) qian=百(1) bai=十(2) shi=个(3)
 */

const {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  getModelStat,
  modelPerformanceSummary
} = require("./formula_kit");

const FREQ_WINDOW = Number(process.env.HEWEI_TRIO_FREQ_WINDOW || 120);

const TRIO_DEFS = Object.freeze({
  wanQianGe: {
    key: "wanQianGe",
    label: "千百个合尾",
    indexes: [0, 1, 3],
    metricField: "wanQianGeHewei"
  },
  wanBaiShi: {
    key: "wanBaiShi",
    label: "千十个合尾",
    indexes: [0, 2, 3],
    metricField: "wanBaiShiHewei"
  }
});

function normalizeNumberList(numbers) {
  if (Array.isArray(numbers)) {
    return numbers.map((n) => (n === undefined || n === null ? "" : String(n))).filter((n) => n !== "");
  }
  if (typeof numbers === "string") return numbers.replace(/\D/g, "").split("").filter(Boolean);
  return [];
}

function trioSumTailFromNumbers(numbers, indexes) {
  const nums = normalizeNumberList(numbers).slice(0, 4);
  const idxs = Array.isArray(indexes) ? indexes : [];
  let sum = 0;
  for (let i = 0; i < idxs.length; i += 1) {
    sum += Number(nums[idxs[i]] || 0);
  }
  return String(sum % 10);
}

function trioActualGetter(indexes) {
  return function actual(record) {
    const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
    if (!nums || nums.length < 4) return "";
    return trioSumTailFromNumbers(nums, indexes);
  };
}

function metricFieldsFromNumbers(numbers) {
  const nums = normalizeNumberList(numbers).slice(0, 5);
  return {
    wanQianGeHewei: trioSumTailFromNumbers(nums, TRIO_DEFS.wanQianGe.indexes),
    wanBaiShiHewei: trioSumTailFromNumbers(nums, TRIO_DEFS.wanBaiShi.indexes)
  };
}

/**
 * Freq/cold algorithm: window avoid-rate first, then coldest (lowest freq) as tie-break.
 * No lock / hold — independent from stable-block hewei.
 */
function calculateFreqHeweiKill(history, actualGetter, options) {
  const opts = options && typeof options === "object" ? options : {};
  const window = Math.max(10, Number(opts.window || FREQ_WINDOW));
  const label = String(opts.label || "合尾");
  const key = String(opts.key || "trio");
  const performanceKey = String(opts.performanceKey || `hewei.${key}`);
  const performance = opts.performance || {};
  const safeHistory = (Array.isArray(history) ? history : []).filter(Boolean).slice(0, window);

  const counts = {};
  DIGIT_POOL.forEach((d) => { counts[String(d)] = 0; });
  let sample = 0;
  safeHistory.forEach((record) => {
    const digit = String(actualGetter(record) || "");
    if (!/^\d$/.test(digit)) return;
    counts[digit] += 1;
    sample += 1;
  });

  const candidates = DIGIT_POOL.map((digit) => {
    const digitText = String(digit);
    const freq = Number(counts[digitText] || 0);
    const hit = sample - freq; // periods where this digit was avoided
    const rate = sample ? (hit * 100) / sample : 0;
    // Prefer high avoid rate; among ties prefer colder (lower freq)
    const score = rate * 10 - freq;
    return {
      digit: digitText,
      freq,
      hit,
      total: sample,
      rate: Number(rate.toFixed(2)),
      score: Number(score.toFixed(4))
    };
  }).sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (a.freq !== b.freq) return a.freq - b.freq;
    return Number(a.digit) - Number(b.digit);
  });

  const selected = candidates[0] || { digit: "--", rate: null, freq: 0, score: 0 };
  const backup = candidates[1] || selected;
  const recommendable = sample >= 30 && Number(selected.rate || 0) >= 70;

  return {
    label,
    key,
    killDigit: selected.digit,
    primaryDigit: selected.digit,
    backupDigit: backup.digit,
    algorithm: `trio-hewei-freq-window-${window}`,
    performance: modelPerformanceSummary(performance, performanceKey),
    detail: candidates.slice(0, 10).map((item) => ({
      digit: item.digit,
      rate120: item.rate,
      freq: item.freq,
      score: item.score
    })),
    stage: sample >= 60 ? "stable" : (sample >= 30 ? "warming" : "cold-start"),
    stability: {
      window,
      sample,
      selectedRate: selected.rate,
      selectedFreq: selected.freq
    },
    confidence: recommendable ? "normal" : "low",
    recommendable,
    reason: "freq-cold-window"
  };
}

/**
 * Stable algorithm: reuse hewei formula stability-block selector.
 */
function calculateStableHeweiKill(history, actualGetter, options, heweiFormula) {
  const opts = options && typeof options === "object" ? options : {};
  const label = String(opts.label || "合尾");
  const key = String(opts.key || "trio");
  const lockKey = String(opts.lockKey || `v43:hewei:${key}`);
  const performanceKey = String(opts.performanceKey || `hewei.${key}`);
  const performance = opts.performance || {};
  const formula = heweiFormula || require("../hewei/formula");
  const safeHistory = Array.isArray(history) ? history.filter(Boolean) : [];

  const HEWEI_BLOCK_FLOOR = Number(process.env.HEWEI_BLOCK_FLOOR || 72);
  const HEWEI_GUARD_RATE = Number(process.env.HEWEI_GUARD_RATE || 85);

  const candidates = formula.buildStableHeweiCandidates(safeHistory, actualGetter, {
    floor: HEWEI_BLOCK_FLOOR,
    guardRate: HEWEI_GUARD_RATE,
    missPenalty: 2.5,
    getterId: `hewei:${key}`
  });
  const picked = formula.selectStableHeweiCandidate(
    candidates,
    lockKey,
    performance,
    performanceKey,
    {
      floor: HEWEI_BLOCK_FLOOR,
      guardRate: HEWEI_GUARD_RATE,
      lowerFloor: 72,
      performanceFloor: 78
    }
  );
  const selected = picked.selected;
  const backup = picked.backup;
  const r30 = selected && selected.r30 ? selected.r30.rate : 0;
  const r60 = selected && selected.r60 ? selected.r60.rate : 0;
  const r120 = selected && selected.r120 ? selected.r120.rate : 0;
  const recommendable = Number(r30 || 0) >= 55 || Number(r60 || 0) >= 55 || Number(r120 || 0) >= HEWEI_GUARD_RATE - 8;

  return {
    label,
    key,
    killDigit: selected.digit,
    primaryDigit: selected.digit,
    backupDigit: backup.digit,
    algorithm: `v43-independent-hewei-${key}-stability-blocks`,
    performance: modelPerformanceSummary(performance, performanceKey),
    detail: (Array.isArray(candidates) ? candidates : []).slice(0, 10).map((item) => ({
      digit: item.digit,
      rateLong: Number(item.rLong.rate.toFixed(2)),
      rate300: Number(item.r300.rate.toFixed(2)),
      rate120: Number(item.r120.rate.toFixed(2)),
      rate60: Number(item.r60.rate.toFixed(2)),
      rate30: Number(item.r30.rate.toFixed(2)),
      score: item.score,
      stage: item.stage
    })),
    stage: selected.stage,
    stability: {
      lower120: Number((selected.lower120 || 0).toFixed(2)),
      block: selected.block || null,
      guardRate: HEWEI_GUARD_RATE
    },
    confidence: recommendable ? "normal" : "low",
    recommendable,
    reason: (picked && picked.reason) || "best-stability-window"
  };
}

function bindHeweiRuntimeFromHost(heweiFormula, host) {
  if (!heweiFormula || typeof heweiFormula.bindRuntime !== "function") return;
  const h = host && typeof host === "object" ? host : {};
  heweiFormula.bindRuntime({
    getStable120Locks: h.getStable120Locks,
    setStable120Locks: h.setStable120Locks,
    getStableMathFilter: h.getStableMathFilter || h.getCachedFilter,
    readCachedStableMathFilter: h.readCachedStableMathFilter || h.readCachedFilter
  });
}

module.exports = {
  FREQ_WINDOW,
  TRIO_DEFS,
  normalizeNumberList,
  trioSumTailFromNumbers,
  trioActualGetter,
  metricFieldsFromNumbers,
  calculateFreqHeweiKill,
  calculateStableHeweiKill,
  bindHeweiRuntimeFromHost,
  getModelStat,
  modelPerformanceSummary,
  MODEL_LONG_WINDOW
};
