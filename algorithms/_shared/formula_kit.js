"use strict";

/** Shared scoring / model-stat helpers used by isolated algorithm formulas. */

const DIGIT_POOL = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
const MODEL_LONG_WINDOW = Number(process.env.MODEL_LONG_WINDOW || process.env.MAX_HISTORY || 1000);
const MODEL_STABLE_WINDOW = Number(process.env.MODEL_STABLE_WINDOW || 1000);
const MODEL_LOW_HIT_RATE = Number(process.env.MODEL_LOW_HIT_RATE || 85);
const MODEL_MIN_SAMPLE = Number(process.env.MODEL_MIN_SAMPLE || 8);

const HIGH_BASELINE_SCORE_META = {
  spanAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  repeatAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  consecutiveAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  sumRangeAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.0, hint: "极端区间过滤" },
  sumAvoid: { scoreClass: "structural-rare-avoid", baseline: 97.0, hint: "单点和值过滤" },
  parityRatioAvoid: { scoreClass: "structural-rare-avoid", baseline: 93.0, hint: "极端奇偶比过滤" },
  sizeRatioAvoid: { scoreClass: "structural-rare-avoid", baseline: 93.0, hint: "极端大小比过滤" },
  anyTwoHeweiHit: { scoreClass: "wide-hit-baseline", baseline: 99.0, hint: "宽命中参考" },
  anyThreeHeweiHit: { scoreClass: "wide-hit-baseline", baseline: 95.0, hint: "宽命中参考" },
  twoDigitHit: { scoreClass: "pair-avoid-baseline", baseline: 90.0, hint: "除对数（非共现命中）" }
};

function highBaselineScoreMeta(key) {
  const clean = String(key || "").replace(/^extra\\./, "");
  return HIGH_BASELINE_SCORE_META[clean] || null;
}

function decorateTruthScoreFields(target, key, rate) {
  const meta = highBaselineScoreMeta(key);
  if (!target || typeof target !== "object") return target;
  if (meta) {
    target.scoreClass = meta.scoreClass;
    target.randomBaseline = meta.baseline;
    target.edgeOverBaseline = rate == null ? null : Number((Number(rate) - meta.baseline).toFixed(2));
    target.predictivePower = false;
    target.scoreHint = meta.hint;
    target.truthDisplay = "structural";
  } else {
    target.scoreClass = target.scoreClass || "predictive";
    target.predictivePower = target.predictivePower !== false;
    target.truthDisplay = target.truthDisplay || "predictive";
  }
  return target;
}

function getModelStat(performance, key) {
  const raw = performance && performance[key] ? performance[key] : { total: 0, match: 0, rate: null, low: false, lastSuccess: null };
  const out = Object.assign({ key }, raw);
  decorateTruthScoreFields(out, key, out.rate);
  return out;
}

function modelPerformanceSummary(performance, key) {
  const stat = getModelStat(performance, key);
  const meta = highBaselineScoreMeta(key);
  return {
    key,
    total: Number(stat.total || 0),
    match: Number(stat.match || 0),
    rate: stat.rate,
    low: !!stat.low,
    threshold: meta ? Number(meta.baseline) - 3 : MODEL_LOW_HIT_RATE,
    minSample: MODEL_MIN_SAMPLE,
    scoreClass: stat.scoreClass || "",
    randomBaseline: stat.randomBaseline,
    edgeOverBaseline: stat.edgeOverBaseline,
    predictivePower: stat.predictivePower !== false,
    scoreHint: stat.scoreHint || "",
    truthDisplay: stat.truthDisplay || "predictive"
  };
}

function stableWindowStage(total) {
  const n = Number(total || 0);
  if (n >= MODEL_STABLE_WINDOW) return "stable-1000";
  if (n >= 120) return "watch-120-1000";
  return "observe-under-120";
}

function stableWindowScore(r30, r60, r120, r300, rLong, missPenalty) {
  const total = Math.max(
    Number((rLong && rLong.total) || 0),
    Number((r300 && r300.total) || 0),
    Number((r120 && r120.total) || 0),
    Number((r60 && r60.total) || 0),
    Number((r30 && r30.total) || 0)
  );
  const stage = stableWindowStage(total);
  const v30 = Number((r30 && r30.rate) || 0);
  const v60 = Number((r60 && r60.rate) || v30);
  const v120 = Number((r120 && r120.rate) || v60);
  const v300 = Number((r300 && r300.rate) || v120);
  const vLong = Number((rLong && rLong.rate) || v300);
  const miss30 = Number((r30 && r30.missStreak) || 0);
  const maxMiss120 = Number((r120 && r120.maxMissStreak) || (r120 && r120.missStreak) || 0);
  const maxMiss300 = Number((r300 && r300.maxMissStreak) || (r300 && r300.missStreak) || 0);
  const maxMissLong = Number((rLong && rLong.maxMissStreak) || (rLong && rLong.missStreak) || 0);
  const longMiss = Math.max(maxMiss300, maxMissLong);
  let score;
  if (stage === "stable-1000") {
    score = v30 * 0.04 + v60 * 0.08 + v120 * 0.18 + v300 * 0.35 + vLong * 0.35;
  } else if (stage === "watch-120-1000") {
    score = v30 * 0.08 + v60 * 0.16 + v120 * 0.46 + v300 * 0.30;
  } else {
    score = v30 * 0.18 + v60 * 0.34 + v120 * 0.48;
  }
  score -= miss30 * Number(missPenalty || 0);
  score -= Math.max(0, maxMiss120 - 3) * 0.42;
  score -= Math.max(0, longMiss - 5) * 0.24;
  score -= Math.max(0, v120 - v30 - 8) * 0.16;
  score -= Math.max(0, v300 - v60 - 6) * 0.10;
  return { score: Number(score.toFixed(4)), stage };
}

function wilsonLowerRate(hit, total, z) {
  const n = Number(total || 0);
  if (!n) return 0;
  const successes = Math.max(0, Math.min(n, Number(hit || 0)));
  const zz = Number(z || 1.44);
  const phat = successes / n;
  const denom = 1 + (zz * zz) / n;
  const center = phat + (zz * zz) / (2 * n);
  const margin = zz * Math.sqrt((phat * (1 - phat) + (zz * zz) / (4 * n)) / n);
  return Math.max(0, Math.min(100, ((center - margin) / denom) * 100));
}

function rateVariance(rates) {
  const values = (Array.isArray(rates) ? rates : []).map(Number).filter((n) => Number.isFinite(n));
  if (!values.length) return 0;
  const avg = values.reduce((sum, n) => sum + n, 0) / values.length;
  const variance = values.reduce((sum, n) => sum + Math.pow(n - avg, 2), 0) / values.length;
  return Math.sqrt(variance);
}

function emptyStabilityBlock(window, blockSize, floor) {
  return {
    window,
    blockSize,
    blockCount: 0,
    minRate: null,
    avgRate: null,
    maxRate: null,
    variance: null,
    weakBlocks: 0,
    floor,
    blocks: []
  };
}

module.exports = {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  MODEL_LOW_HIT_RATE,
  MODEL_MIN_SAMPLE,
  highBaselineScoreMeta,
  getModelStat,
  modelPerformanceSummary,
  stableWindowStage,
  stableWindowScore,
  wilsonLowerRate,
  rateVariance,
  emptyStabilityBlock
};
