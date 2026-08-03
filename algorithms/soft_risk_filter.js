"use strict";

/**
 * 软筛选 / 风险评估（Node 热路径）
 *
 * 与 algorithms/lottery_filter.py 的 soft_filter_combinations 口径对齐：
 * - 规则命中只记账 Risk Score +1，不 delete 组合
 * - Fuzzy Zone：0~1 low / 2 mid / >=3 high
 * - Safe Retention：至少保留前 20%~30%（默认 25%）得分最优组合
 *
 * 输入 filter 为 run_stable_filter / slimStableMathFilter 产物。
 */

const RISK_LOW_MAX = 1;
const RISK_MID = 2;
const RISK_HIGH_MIN = 3;
const SAFE_RETENTION_MIN = 0.2;
const SAFE_RETENTION_MAX = 0.3;
const SAFE_RETENTION_DEFAULT = 0.25;

const RISK_LEVEL_LABELS = {
  low: "低风险/强烈推荐",
  mid: "中风险/观察区",
  high: "高风险/建议剔除"
};

const DEFAULT_EXCLUDE_SPANS = [0, 1, 9];
const DEFAULT_EXCLUDE_REPEATS = ["三重", "四重"];
const DEFAULT_EXCLUDE_CONSECUTIVES = ["三连", "四连"];
const DEFAULT_EXCLUDE_PARITY = ["4单0双", "0单4双"];
const DEFAULT_EXCLUDE_SIZE = ["4大0小", "0大4小"];
const DEFAULT_EXCLUDE_SHAPES = ["全奇", "全偶", "全大", "全小", "全0路", "全1路", "全2路"];
const SUM_MIN_EXCLUSIVE = 6;
const SUM_MAX_EXCLUSIVE = 30;

function clampRetentionRatio(ratio) {
  const n = Number(ratio);
  if (!Number.isFinite(n)) return SAFE_RETENTION_DEFAULT;
  return Math.max(SAFE_RETENTION_MIN, Math.min(SAFE_RETENTION_MAX, n));
}

function riskLevelFromScore(score) {
  const s = Number(score) || 0;
  if (s <= RISK_LOW_MAX) return "low";
  if (s === RISK_MID) return "mid";
  return "high";
}

function normalizeDraw(drawLike) {
  if (Array.isArray(drawLike)) {
    return drawLike.slice(0, 4).map((d) => Number(d)).filter((d) => Number.isInteger(d) && d >= 0 && d <= 9);
  }
  if (drawLike && typeof drawLike === "object") {
    if (Array.isArray(drawLike.numbers)) return normalizeDraw(drawLike.numbers);
    const raw = [
      drawLike.n1 != null ? drawLike.n1 : drawLike.ball1,
      drawLike.n2 != null ? drawLike.n2 : drawLike.ball2,
      drawLike.n3 != null ? drawLike.n3 : drawLike.ball3,
      drawLike.n4 != null ? drawLike.n4 : drawLike.ball4
    ];
    return normalizeDraw(raw);
  }
  const digits = String(drawLike == null ? "" : drawLike).replace(/\D/g, "").slice(0, 4);
  return digits.split("").map((d) => Number(d));
}

function drawSpan(draw) {
  return Math.max.apply(null, draw) - Math.min.apply(null, draw);
}

function drawSum(draw) {
  return draw.reduce((a, b) => a + b, 0);
}

function drawShapes(draw) {
  const odd = draw.filter((d) => d % 2 === 1).length;
  const big = draw.filter((d) => d >= 5).length;
  const roads = new Set(draw.map((d) => d % 3));
  const shapes = [];
  if (odd === 4) shapes.push("全奇");
  if (odd === 0) shapes.push("全偶");
  if (big === 4) shapes.push("全大");
  if (big === 0) shapes.push("全小");
  if (roads.size === 1) shapes.push(`全${Array.from(roads)[0]}路`);
  return shapes;
}

function drawParityRatio(draw) {
  const odd = draw.filter((d) => d % 2 === 1).length;
  return `${odd}单${4 - odd}双`;
}

function drawSizeRatio(draw) {
  const big = draw.filter((d) => d >= 5).length;
  return `${big}大${4 - big}小`;
}

function drawRepeatShape(draw) {
  const counts = {};
  draw.forEach((d) => {
    counts[d] = (counts[d] || 0) + 1;
  });
  const values = Object.keys(counts).map((k) => counts[k]).sort((a, b) => b - a);
  if (!values.length) return "无重";
  if (values[0] >= 4) return "四重";
  if (values[0] >= 3) return "三重";
  const pairs = values.filter((c) => c === 2).length;
  if (pairs >= 2) return "双重";
  if (pairs === 1) return "一对";
  return "无重";
}

function drawMaxConsecutiveRun(draw) {
  const sorted = Array.from(new Set(draw)).sort((a, b) => a - b);
  if (!sorted.length) return 0;
  let best = 1;
  let cur = 1;
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i] === sorted[i - 1] + 1) cur += 1;
    else cur = 1;
    if (cur > best) best = cur;
  }
  return best;
}

function drawConsecutiveShape(draw) {
  const run = drawMaxConsecutiveRun(draw);
  if (run >= 4) return "四连";
  if (run >= 3) return "三连";
  if (run >= 2) return "二连";
  return "无连";
}

function repeatHit(shape, excluded) {
  if (excluded.indexOf(shape) >= 0) return true;
  if (excluded.indexOf("重号") >= 0 && shape !== "" && shape !== "无重") return true;
  return false;
}

function consecutiveHit(shape, excluded) {
  if (excluded.indexOf(shape) >= 0) return true;
  if (excluded.indexOf("连号") >= 0 && shape !== "" && shape !== "无连") return true;
  return false;
}

function listOr(filter, key, fallback) {
  if (filter && Array.isArray(filter[key]) && filter[key].length) return filter[key].map(String);
  const fw = filter && filter.framework && typeof filter.framework === "object" ? filter.framework : null;
  if (fw) {
    const map = {
      exclude_digits: "digitAvoid",
      exclude_shapes: "shapeAvoid",
      exclude_spans: "spanAvoid",
      exclude_repeats: "repeatAvoid",
      exclude_consecutives: "consecutiveAvoid",
      exclude_parity_ratios: "parityRatioAvoid",
      exclude_size_ratios: "sizeRatioAvoid"
    };
    const fwKey = map[key];
    if (fwKey && Array.isArray(fw[fwKey]) && fw[fwKey].length) return fw[fwKey].map(String);
  }
  return (fallback || []).map(String);
}

/** 千/百/十/个 位杀 → { wan,qian,bai,shi } 各一位数字（软加分，非硬删） */
const POSITION_KEYS = ["wan", "qian", "bai", "shi"];
const POSITION_LABELS = { wan: "千位", qian: "百位", bai: "十位", shi: "个位" };

function normalizePositionKills(filter) {
  const out = { wan: "", qian: "", bai: "", shi: "" };
  if (!filter || typeof filter !== "object") return out;
  const raw =
    (filter.position_kills && typeof filter.position_kills === "object" && filter.position_kills)
    || (filter.positionKills && typeof filter.positionKills === "object" && filter.positionKills)
    || (filter.exclude_positions && typeof filter.exclude_positions === "object" && filter.exclude_positions)
    || (filter.framework && filter.framework.positionAvoid && typeof filter.framework.positionAvoid === "object"
      ? filter.framework.positionAvoid
      : null)
    || null;
  if (!raw) return out;
  POSITION_KEYS.forEach((key, idx) => {
    let entry = raw[key];
    if (entry == null && Array.isArray(raw)) entry = raw[idx];
    if (entry == null && raw[String(idx)] != null) entry = raw[String(idx)];
    if (entry && typeof entry === "object") {
      entry = entry.selectedDigit != null ? entry.selectedDigit
        : (entry.recommendDigit != null ? entry.recommendDigit
          : (entry.main != null ? entry.main
            : (entry.killDigits && entry.killDigits[0])));
    }
    const d = String(entry == null ? "" : entry).replace(/\D/g, "").slice(0, 1);
    out[key] = /^\d$/.test(d) ? d : "";
  });
  return out;
}

function mergePositionKillsIntoFilter(filter, positionKills) {
  const base = filter && typeof filter === "object" ? Object.assign({}, filter) : {};
  const normalized = normalizePositionKills({ position_kills: positionKills || base.position_kills || base.positionKills });
  base.position_kills = normalized;
  base.positionKills = normalized;
  if (!base.framework || typeof base.framework !== "object") base.framework = {};
  else base.framework = Object.assign({}, base.framework);
  base.framework.positionAvoid = normalized;
  return base;
}

/**
 * 单注风险打分（不删除）。
 * @param {string|number[]|object} drawLike
 * @param {object|null} filter run_stable_filter / slim 结果
 */
function scoreCombination(drawLike, filter) {
  const draw = normalizeDraw(drawLike);
  const original = typeof drawLike === "string" ? drawLike : draw.join("");
  const normalized = draw.length === 4 ? draw.join("") : String(original).replace(/\D/g, "").slice(0, 4);

  if (draw.length < 4) {
    return {
      original,
      normalized,
      draw,
      riskScore: 99,
      riskLevel: "high",
      riskLabel: RISK_LEVEL_LABELS.high,
      triggeredRules: ["invalidDraw"],
      rejectedBy: ["invalidDraw"],
      passed: false,
      recommend: false,
      watch: false,
      suggestDrop: true,
      excluded: true,
      mode: "soft_risk"
    };
  }

  const res = filter && typeof filter === "object" ? filter : {};
  const excludedDigits = new Set(
    listOr(res, "exclude_digits", []).filter((d) => /^\d$/.test(d)).map((d) => Number(d))
  );
  const hitDigits = draw.filter((d) => excludedDigits.has(d));
  const shapes = drawShapes(draw);
  const excludeShapes = listOr(res, "exclude_shapes", DEFAULT_EXCLUDE_SHAPES);
  const shapeHit = shapes.filter((s) => excludeShapes.indexOf(s) >= 0);
  const span = drawSpan(draw);
  const spanList = listOr(res, "exclude_spans", DEFAULT_EXCLUDE_SPANS).map(Number).filter(Number.isFinite);
  const spanHit = spanList.indexOf(span) >= 0;
  const total = drawSum(draw);
  const sumHit = total < SUM_MIN_EXCLUSIVE || total > SUM_MAX_EXCLUSIVE;
  const parity = drawParityRatio(draw);
  const size = drawSizeRatio(draw);
  const repeat = drawRepeatShape(draw);
  const consecutive = drawConsecutiveShape(draw);
  const parityHit = listOr(res, "exclude_parity_ratios", DEFAULT_EXCLUDE_PARITY).indexOf(parity) >= 0;
  const sizeHit = listOr(res, "exclude_size_ratios", DEFAULT_EXCLUDE_SIZE).indexOf(size) >= 0;
  const repeatMatched = repeatHit(repeat, listOr(res, "exclude_repeats", DEFAULT_EXCLUDE_REPEATS));
  const consecutiveMatched = consecutiveHit(
    consecutive,
    listOr(res, "exclude_consecutives", DEFAULT_EXCLUDE_CONSECUTIVES)
  );

  // 位杀软加分：每位命中 +1（千/百/十/个），不硬删
  const positionKills = normalizePositionKills(res);
  const hitPositions = [];
  POSITION_KEYS.forEach((key, idx) => {
    const killDigit = positionKills[key];
    if (killDigit && String(draw[idx]) === killDigit) {
      hitPositions.push(key);
    }
  });

  const triggeredRules = [];
  if (hitDigits.length) triggeredRules.push("digitAvoid");
  if (shapeHit.length) triggeredRules.push("shapeAvoid");
  if (spanHit) triggeredRules.push("spanAvoid");
  if (sumHit) triggeredRules.push("sumRangeAvoid");
  if (parityHit) triggeredRules.push("parityRatioAvoid");
  if (sizeHit) triggeredRules.push("sizeRatioAvoid");
  if (repeatMatched) triggeredRules.push("repeatAvoid");
  if (consecutiveMatched) triggeredRules.push("consecutiveAvoid");
  hitPositions.forEach((key) => {
    triggeredRules.push(`positionAvoid.${key}`);
  });

  const riskScore = triggeredRules.length;
  const riskLevel = riskLevelFromScore(riskScore);

  return {
    original,
    normalized,
    draw,
    riskScore,
    riskLevel,
    riskLabel: RISK_LEVEL_LABELS[riskLevel],
    triggeredRules,
    rejectedBy: triggeredRules.slice(),
    passed: riskLevel !== "high",
    recommend: riskLevel === "low",
    watch: riskLevel === "mid",
    suggestDrop: riskLevel === "high",
    // 兼容旧硬杀字段：任一命中即为 excluded
    excluded: triggeredRules.length > 0,
    hitDigits,
    hitShapes: shapeHit,
    hitPositions,
    positionKills,
    span,
    spanHit,
    sum: total,
    sumHit,
    parityRatio: parity,
    parityHit,
    sizeRatio: size,
    sizeHit,
    repeatShape: repeat,
    repeatHit: repeatMatched,
    consecutiveShape: consecutive,
    consecutiveHit: consecutiveMatched,
    mode: "soft_risk"
  };
}

/**
 * 批量软筛选：Risk Scoring + Fuzzy Zone + Safe Retention
 */
function softFilterCombinations(draws, filter, options) {
  const opts = options && typeof options === "object" ? options : {};
  const ratio = clampRetentionRatio(opts.retentionRatio);
  const highRiskThreshold = Number.isFinite(Number(opts.highRiskThreshold))
    ? Number(opts.highRiskThreshold)
    : RISK_HIGH_MIN;
  const keepMid = opts.keepMid !== false;

  const scored = (Array.isArray(draws) ? draws : []).map((draw) => scoreCombination(draw, filter));
  scored.sort((a, b) => {
    if (a.riskScore !== b.riskScore) return a.riskScore - b.riskScore;
    return String(a.normalized).localeCompare(String(b.normalized));
  });

  const total = scored.length;
  const minKeep = total === 0 ? 0 : Math.max(1, Math.ceil(total * ratio));
  const retained = [];
  const dropped = [];
  const forceRetained = [];
  const ranked = [];

  scored.forEach((row, index) => {
    const annotated = Object.assign({}, row, { rank: index + 1 });
    const score = annotated.riskScore;
    const level = annotated.riskLevel;

    if (score < highRiskThreshold) {
      if (level === "mid" && !keepMid) {
        if (retained.length < minKeep) {
          annotated.retentionForced = true;
          annotated.decision = "force_retain";
          retained.push(annotated);
          forceRetained.push(annotated);
        } else {
          annotated.retentionForced = false;
          annotated.decision = "drop_mid";
          dropped.push(annotated);
        }
      } else {
        annotated.retentionForced = false;
        annotated.decision = "retain";
        retained.push(annotated);
      }
    } else if (retained.length < minKeep) {
      annotated.retentionForced = true;
      annotated.decision = "force_retain";
      annotated.passed = true;
      retained.push(annotated);
      forceRetained.push(annotated);
    } else {
      annotated.retentionForced = false;
      annotated.decision = "drop_high";
      dropped.push(annotated);
    }
    ranked.push(annotated);
  });

  const byLevel = {
    low: ranked.filter((r) => r.riskLevel === "low"),
    mid: ranked.filter((r) => r.riskLevel === "mid"),
    high: ranked.filter((r) => r.riskLevel === "high")
  };

  return {
    mode: "soft_risk",
    total,
    retentionRatio: ratio,
    minKeep,
    highRiskThreshold,
    keepMid,
    fuzzyZone: {
      low: `0~${RISK_LOW_MAX}`,
      mid: String(RISK_MID),
      high: `>=${RISK_HIGH_MIN}`
    },
    counts: {
      low: byLevel.low.length,
      mid: byLevel.mid.length,
      high: byLevel.high.length,
      retained: retained.length,
      dropped: dropped.length,
      forceRetained: forceRetained.length
    },
    ranked,
    retained,
    dropped,
    forceRetained,
    byLevel
  };
}

/** 0000..9999 全量四码池 */
function buildFullFourDigitPool() {
  const pool = new Array(10000);
  for (let i = 0; i < 10000; i += 1) {
    pool[i] = String(i).padStart(4, "0");
  }
  return pool;
}

/**
 * 全量池软筛选 → 按 Risk Score 升序的推荐号码列表
 * @param {object|null} filter
 * @param {object} [options] retentionRatio, positionKills, keepRanked, codeLimit
 */
function softRecommendFromPool(filter, options) {
  const opts = options && typeof options === "object" ? options : {};
  const enriched = mergePositionKillsIntoFilter(filter, opts.positionKills);
  const pool = Array.isArray(opts.draws) && opts.draws.length
    ? opts.draws
    : buildFullFourDigitPool();
  const batch = softFilterCombinations(pool, enriched, opts);
  const lowCodes = batch.byLevel.low.map((r) => r.normalized);
  const midCodes = batch.byLevel.mid.map((r) => r.normalized);
  const recommendCodes = batch.retained
    .slice()
    .sort((a, b) => {
      if (a.riskScore !== b.riskScore) return a.riskScore - b.riskScore;
      return String(a.normalized).localeCompare(String(b.normalized));
    })
    .map((r) => ({
      code: r.normalized,
      riskScore: r.riskScore,
      riskLevel: r.riskLevel,
      triggeredRules: r.triggeredRules
    }));

  const codeLimit = Number.isFinite(Number(opts.codeLimit)) ? Math.max(0, Number(opts.codeLimit)) : 0;
  const trimCodes = (arr) => (codeLimit > 0 ? arr.slice(0, codeLimit) : arr);

  return {
    mode: "soft_risk",
    poolSize: batch.total,
    retainedCount: batch.counts.retained,
    droppedCount: batch.counts.dropped,
    retentionRatio: batch.retentionRatio,
    positionKills: normalizePositionKills(enriched),
    counts: batch.counts,
    fuzzyZone: batch.fuzzyZone,
    // 最终精简推荐列表（风险分升序）
    recommendCodes: codeLimit > 0 ? recommendCodes.slice(0, codeLimit) : recommendCodes,
    lowRiskCodes: trimCodes(lowCodes),
    midRiskCodes: trimCodes(midCodes),
    lowRiskTotal: lowCodes.length,
    midRiskTotal: midCodes.length,
    highRiskTotal: batch.counts.high,
    comparison: {
      before: batch.total,
      after: batch.counts.retained,
      text: `从 ${batch.total} 注安全缩减至 ${batch.counts.retained} 注`
    },
    // 完整 ranked 默认不带（防爆内存）；需要时 opts.keepRanked=true
    ranked: opts.keepRanked ? batch.ranked : undefined,
    retained: opts.keepRanked ? batch.retained : undefined
  };
}

/** 写入 tip / slim JSON 的摘要（不含上万条 ranked） */
function slimSoftRecommend(softRecommend, options) {
  if (!softRecommend || typeof softRecommend !== "object") return null;
  const opts = options || {};
  const maxLow = Math.max(0, Number(opts.maxLow != null ? opts.maxLow : 200));
  const maxMid = Math.max(0, Number(opts.maxMid != null ? opts.maxMid : 100));
  const maxRec = Math.max(0, Number(opts.maxRecommend != null ? opts.maxRecommend : 300));
  return {
    mode: "soft_risk",
    poolSize: softRecommend.poolSize,
    retainedCount: softRecommend.retainedCount,
    droppedCount: softRecommend.droppedCount,
    retentionRatio: softRecommend.retentionRatio,
    positionKills: softRecommend.positionKills || null,
    counts: softRecommend.counts || null,
    fuzzyZone: softRecommend.fuzzyZone || null,
    comparison: softRecommend.comparison || null,
    lowRiskTotal: softRecommend.lowRiskTotal,
    midRiskTotal: softRecommend.midRiskTotal,
    highRiskTotal: softRecommend.highRiskTotal,
    lowRiskCodes: Array.isArray(softRecommend.lowRiskCodes)
      ? softRecommend.lowRiskCodes.slice(0, maxLow)
      : [],
    midRiskCodes: Array.isArray(softRecommend.midRiskCodes)
      ? softRecommend.midRiskCodes.slice(0, maxMid)
      : [],
    recommendCodes: Array.isArray(softRecommend.recommendCodes)
      ? softRecommend.recommendCodes.slice(0, maxRec)
      : []
  };
}

function formatCodeBlock(title, codes, total, printLimit) {
  const limit = Math.max(1, Number(printLimit) || 80);
  const list = Array.isArray(codes) ? codes : [];
  const shown = list.slice(0, limit);
  const lines = [
    title,
    `  合计 ${total != null ? total : list.length} 注` + (list.length > limit ? `（控制台仅展示前 ${limit} 注）` : ""),
    shown.length ? `  ${shown.join(" ")}` : "  （无）"
  ];
  return lines.join("\n");
}

/** 控制台报告：低/中风险 + 缩减对比 */
function formatSoftRecommendConsole(softRecommend, options) {
  const opts = options || {};
  const printLimit = Math.max(1, Number(opts.printLimit || 80));
  if (!softRecommend || typeof softRecommend !== "object") {
    return "[soft-risk] 无软筛选推荐结果";
  }
  const before = softRecommend.poolSize != null
    ? softRecommend.poolSize
    : (softRecommend.comparison && softRecommend.comparison.before);
  const after = softRecommend.retainedCount != null
    ? softRecommend.retainedCount
    : (softRecommend.comparison && softRecommend.comparison.after);
  const pk = softRecommend.positionKills || {};
  const pkText = POSITION_KEYS.map((k) => `${POSITION_LABELS[k]}=${pk[k] || "-"}`).join(" ");
  const lines = [
    "========== Soft Risk Scoring 选号输出 ==========",
    `位杀软加分：${pkText}`,
    `过滤对比：从 ${before} 注安全缩减至 ${after} 注` +
      (softRecommend.droppedCount != null ? `（剔除 ${softRecommend.droppedCount}）` : ""),
    `分层统计：低风险=${softRecommend.lowRiskTotal != null ? softRecommend.lowRiskTotal : (softRecommend.counts && softRecommend.counts.low)}` +
      ` 中风险=${softRecommend.midRiskTotal != null ? softRecommend.midRiskTotal : (softRecommend.counts && softRecommend.counts.mid)}` +
      ` 高风险=${softRecommend.highRiskTotal != null ? softRecommend.highRiskTotal : (softRecommend.counts && softRecommend.counts.high)}`,
    formatCodeBlock(
      "【低风险推荐号码】（0-1分，建议重点关注）",
      softRecommend.lowRiskCodes,
      softRecommend.lowRiskTotal,
      printLimit
    ),
    formatCodeBlock(
      "【中风险观察号码】（2分，保留人工参考）",
      softRecommend.midRiskCodes,
      softRecommend.midRiskTotal,
      printLimit
    ),
    "================================================"
  ];
  return lines.join("\n");
}

function printSoftRecommendConsole(softRecommend, options) {
  const text = formatSoftRecommendConsole(softRecommend, options);
  // eslint-disable-next-line no-console
  console.log(text);
  return text;
}

/** 可挂到 slim filter 上的软筛选元数据（面板/下游识别） */
function softRiskMeta() {
  return {
    mode: "soft_risk",
    fuzzyZone: {
      low: `0~${RISK_LOW_MAX}`,
      mid: String(RISK_MID),
      high: `>=${RISK_HIGH_MIN}`
    },
    retentionDefault: SAFE_RETENTION_DEFAULT,
    retentionMin: SAFE_RETENTION_MIN,
    retentionMax: SAFE_RETENTION_MAX,
    note: "组合筛选走 Risk Scoring；exclude_* / 位杀 为建议维度，非绝对删号"
  };
}

/**
 * 单码风险增量：命中更稳排除单码时 +1（供合尾/杀号选码软化硬 avoid）
 */
function digitRiskBonus(digit, filter) {
  const d = String(digit);
  if (!/^\d$/.test(d)) return 0;
  const excluded = listOr(filter, "exclude_digits", []);
  return excluded.indexOf(d) >= 0 ? 1 : 0;
}

/** 位杀命中加分：指定位置数字命中 position kill 时 +1 */
function positionRiskBonus(digit, positionKey, filter) {
  const d = String(digit);
  if (!/^\d$/.test(d)) return 0;
  const pk = normalizePositionKills(filter);
  const key = String(positionKey || "");
  if (!pk[key]) return 0;
  return pk[key] === d ? 1 : 0;
}

module.exports = {
  RISK_LOW_MAX,
  RISK_MID,
  RISK_HIGH_MIN,
  SAFE_RETENTION_DEFAULT,
  RISK_LEVEL_LABELS,
  POSITION_KEYS,
  POSITION_LABELS,
  riskLevelFromScore,
  normalizeDraw,
  normalizePositionKills,
  mergePositionKillsIntoFilter,
  scoreCombination,
  softFilterCombinations,
  buildFullFourDigitPool,
  softRecommendFromPool,
  slimSoftRecommend,
  formatSoftRecommendConsole,
  printSoftRecommendConsole,
  softRiskMeta,
  digitRiskBonus,
  positionRiskBonus,
  clampRetentionRatio
};
