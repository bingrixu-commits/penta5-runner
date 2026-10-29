"use strict";

/**
 * REAL hewei formulas extracted from penta5_backend_runner.js.
 */

const {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  MODEL_LOW_HIT_RATE,
  getModelStat,
  modelPerformanceSummary,
  stableWindowScore,
  stableWindowStage,
  wilsonLowerRate,
  rateVariance,
  emptyStabilityBlock: kitEmptyStabilityBlock
} = require("../../_shared/formula_kit");

const HEWEI_BLOCK_WINDOW = Number(process.env.HEWEI_BLOCK_WINDOW || 120);
const MODEL_RATE_WINDOW = Number(process.env.MODEL_RATE_WINDOW || 30);
const HEWEI_BLOCK_SIZE = Number(process.env.HEWEI_BLOCK_SIZE || 20);
const HEWEI_BLOCK_FLOOR = Number(process.env.HEWEI_BLOCK_FLOOR || 72);
const HEWEI_UNIFIED_BLOCK_FLOOR = Number(process.env.HEWEI_UNIFIED_BLOCK_FLOOR || 60);
const HEWEI_SWITCH_MARGIN = Number(process.env.HEWEI_SWITCH_MARGIN || 4);
const HEWEI_MIN_HOLD = Number(process.env.HEWEI_MIN_HOLD || 3);
const HEWEI_GUARD_RATE = Number(process.env.HEWEI_GUARD_RATE || 85);
const HEWEI_UNIFIED_GUARD_RATE = Number(process.env.HEWEI_UNIFIED_GUARD_RATE || 72);
const BUILD_STABLE_HEWEI_LRU_MAX = Number(process.env.BUILD_STABLE_HEWEI_LRU_MAX || 24);
const buildStableHeweiCandidatesLru = new Map();

function normalizeNumberList(numbers) {
  if (Array.isArray(numbers)) {
    return numbers.map((n) => (n === undefined || n === null ? "" : String(n))).filter((n) => n !== "");
  }
  if (typeof numbers === "string") return numbers.replace(/\D/g, "").split("").filter(Boolean);
  return [];
}



let _runtime = {
  getStable120Locks: () => ({}),
  setStable120Locks: null,
  getStableMathFilter: () => null,
  readCachedStableMathFilter: () => null
};

function bindRuntime(runtime) {
  _runtime = Object.assign({}, _runtime, runtime && typeof runtime === "object" ? runtime : {});
}

function stateShim() {
  return {
    get stable120Locks() {
      const locks = _runtime.getStable120Locks ? _runtime.getStable120Locks() : {};
      return locks && typeof locks === "object" ? locks : {};
    },
    set stable120Locks(value) {
      if (typeof _runtime.setStable120Locks === "function") _runtime.setStable120Locks(value);
    },
    get stableMathFilter() {
      return typeof _runtime.getStableMathFilter === "function" ? _runtime.getStableMathFilter() : null;
    }
  };
}

// Resolve extracted state access through the bound runtime instead of a host global.
const state = stateShim();

function emptyStabilityBlock(window, blockSize, floor) {
  return kitEmptyStabilityBlock(window, blockSize, floor);
}

function getStableMathExcludeDigitSet() {
  const filter = (typeof _runtime.getStableMathFilter === "function" && _runtime.getStableMathFilter())
    || (typeof _runtime.readCachedStableMathFilter === "function" && _runtime.readCachedStableMathFilter())
    || null;
  const digits = filter && Array.isArray(filter.exclude_digits) ? filter.exclude_digits : [];
  return new Set(digits.map((d) => String(d)).filter((d) => /^\d$/.test(d)));
}

function calculateMetrics(numbers) {
  const nums = normalizeNumberList(numbers).slice(0, 5);
  const n0 = Number(nums[0] || 0); // 球一
  const n1 = Number(nums[1] || 0); // 球二
  const n2 = Number(nums[2] || 0); // 球三
  const n3 = Number(nums[3] || 0); // 球四

  const totalSum = n0 + n1 + n2 + n3;

  return {
    totalSum: totalSum,
    totalHewei: String(totalSum % 10),

    // 新综合合尾口径
    wanQianBaiHewei: String((n0 + n1 + n2) % 10),
    qianBaiShiHewei: String((n1 + n2 + n3) % 10),
    wanQianBaiShiHewei: String((n0 + n1 + n2 + n3) % 10),
    // 千百个 / 千十个（独立三合尾）
    wanQianGeHewei: String((n0 + n1 + n3) % 10),
    wanBaiShiHewei: String((n0 + n2 + n3) % 10),

    // 保留旧字段名兼容旧页面，但计算口径严格限制在球一到球四。
    baiShiGeHewei: String((n1 + n2 + n3) % 10),
    qianBaiShiGeHewei: String(totalSum % 10),
    ball5ExcludedFromModel: true
  };
}

function getCompositeHeweiGroup(record) {
  // 合尾一律按开奖号码现算，避免 RDS/代理 metrics 脏数据污染结算
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  const fallback = calculateMetrics(nums);
  return [
    String(fallback.wanQianBaiHewei),
    String(fallback.qianBaiShiHewei),
    String(fallback.wanQianBaiShiHewei)
  ];
}

function normalizeActualValues(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => item === undefined || item === null ? "" : String(item))
      .filter((item) => item !== "");
  }
  if (value === undefined || value === null || value === "") return [];
  return [String(value)];
}

function preprocessHeweiActualSeries(records, actualGetter, limit) {
  const rows = (Array.isArray(records) ? records : []).filter(Boolean).slice(0, Math.max(1, Number(limit || MODEL_LONG_WINDOW)));
  return rows.map((record) => normalizeActualValues(actualGetter(record)));
}

function avoidDigitRateMultiWindowFromSeries(series, digit, limits, streakLimit) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const target = String(digit);
  const maxStreak = streakLimit === undefined ? 8 : Number(streakLimit || 0);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, missStreak: 0, maxMissStreak: 0, rate: 0, _currentRun: 0, _leading: true };
  });
  const rows = Array.isArray(series) ? series : [];
  for (let i = 0; i < rows.length; i += 1) {
    const values = rows[i];
    if (!values || !values.length) continue;
    const ok = values.indexOf(target) < 0;
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
    result.rate = result.total ? result.hit * 100 / result.total : 0;
    delete result._currentRun;
    delete result._leading;
  });
  return results;
}

function avoidDigitRateRows(records, digit, actualGetter, limit, streakLimit) {
  const key = Math.max(1, Number(limit || MODEL_RATE_WINDOW));
  const series = preprocessHeweiActualSeries(records, actualGetter, key);
  const results = avoidDigitRateMultiWindowFromSeries(series, digit, [key], streakLimit);
  return results[key] || { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0 };
}

function avoidDigitBlockStats(records, digit, actualGetter, options) {
  const opts = options && typeof options === "object" ? options : {};
  const window = Number(opts.window || HEWEI_BLOCK_WINDOW);
  const blockSize = Math.max(1, Number(opts.blockSize || HEWEI_BLOCK_SIZE));
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const rows = (Array.isArray(records) ? records : []).slice(0, window);
  const blocks = [];
  for (let start = 0; start < rows.length; start += blockSize) {
    const blockRows = rows.slice(start, start + blockSize);
    if (!blockRows.length) continue;
    const result = avoidDigitRateRows(blockRows, digit, actualGetter, blockRows.length, 0);
    if (result.total > 0) {
      blocks.push({
        index: blocks.length + 1,
        total: result.total,
        hit: result.hit,
        fail: result.total - result.hit,
        rate: Number(result.rate.toFixed(2))
      });
    }
  }
  if (!blocks.length) return emptyStabilityBlock(window, blockSize, floor);
  const rates = blocks.map((block) => Number(block.rate || 0));
  const avg = rates.reduce((sum, n) => sum + n, 0) / rates.length;
  return {
    window,
    blockSize,
    blockCount: blocks.length,
    minRate: Math.min(...rates),
    avgRate: Number(avg.toFixed(2)),
    maxRate: Math.max(...rates),
    variance: Number(rateVariance(rates).toFixed(2)),
    weakBlocks: blocks.filter((block) => Number(block.rate || 0) < floor).length,
    floor,
    blocks
  };
}

function sortStableDigitCandidates(a, b) {
  if (b.score !== a.score) return b.score - a.score;
  if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
  if ((b.block && b.block.minRate || 0) !== (a.block && a.block.minRate || 0)) return (b.block && b.block.minRate || 0) - (a.block && a.block.minRate || 0);
  if (b.r300.rate !== a.r300.rate) return b.r300.rate - a.r300.rate;
  if (b.rLong.rate !== a.rLong.rate) return b.rLong.rate - a.rLong.rate;
  if (b.r120.rate !== a.r120.rate) return b.r120.rate - a.r120.rate;
  if (b.r60.rate !== a.r60.rate) return b.r60.rate - a.r60.rate;
  if (b.r30.rate !== a.r30.rate) return b.r30.rate - a.r30.rate;
  return Number(a.digit) - Number(b.digit);
}

function buildStableHeweiCandidatesCacheKey(records, getterId, options) {
  const rows = Array.isArray(records) ? records : [];
  const first = rows[0];
  const opts = options && typeof options === "object" ? options : {};
  const recentPeriods = rows.slice(0, 3).map((row) => (row && row.period != null ? String(row.period) : "")).join(",");
  const excludeSig = Array.isArray(opts.excludeDigits) ? opts.excludeDigits.slice().sort().join("") : "";
  return [
    String(getterId || "default"),
    rows.length,
    first && first.uid ? first.uid : "",
    first && Array.isArray(first.numbers) ? first.numbers.slice(0, 5).join("") : "",
    recentPeriods,
    excludeSig,
    Number(opts.floor || HEWEI_BLOCK_FLOOR),
    Number(opts.blockWindow || HEWEI_BLOCK_WINDOW),
    Number(opts.blockSize || HEWEI_BLOCK_SIZE)
  ].join("|");
}

function buildStableHeweiCandidatesCore(records, actualGetter, options) {
  const opts = options && typeof options === "object" ? options : {};
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const missPenalty = Number(opts.missPenalty || 2.5);
  const excludeSet = new Set(
    (Array.isArray(opts.excludeDigits) ? opts.excludeDigits : [])
      .map((digit) => String(digit))
      .filter((digit) => /^\d$/.test(digit))
  );
  const heweiWindows = [30, 60, 120, MODEL_STABLE_WINDOW, MODEL_LONG_WINDOW];
  const series = preprocessHeweiActualSeries(records, actualGetter, MODEL_LONG_WINDOW);
  const candidates = DIGIT_POOL.map((digit) => {
    const digitText = String(digit);
    const rateWindows = avoidDigitRateMultiWindowFromSeries(series, digit, heweiWindows);
    const rLong = rateWindows[MODEL_LONG_WINDOW];
    const r300 = rateWindows[MODEL_STABLE_WINDOW];
    const r120 = rateWindows[120];
    const r60 = rateWindows[60];
    const r30 = rateWindows[30];
    const weighted = stableWindowScore(r30, r60, r120, r300, rLong, missPenalty);
    const block = avoidDigitBlockStats(records, digit, actualGetter, {
      floor,
      window: opts.blockWindow || HEWEI_BLOCK_WINDOW,
      blockSize: opts.blockSize || HEWEI_BLOCK_SIZE
    });
    const lower120 = wilsonLowerRate(r120.hit, r120.total);
    const lower300 = wilsonLowerRate(r300.hit, r300.total);
    const recentPenalty = Number(r30.missStreak || 0) * 1.45;
    const weakPenalty = Number(block.weakBlocks || 0) * 2.2;
    const variancePenalty = Number(block.variance || 0) * 0.08;
    const stableMathPenalty = excludeSet.has(digitText) ? 120 : 0;
    const score = (
      weighted.score * 0.55 +
      lower120 * 0.13 +
      lower300 * 0.08 +
      Number(block.minRate || 0) * 0.12 +
      Number(block.avgRate || 0) * 0.08 -
      recentPenalty -
      weakPenalty -
      variancePenalty -
      stableMathPenalty
    );
    return {
      digit: digitText,
      rLong,
      r300,
      r120,
      r60,
      r30,
      score: Number(score.toFixed(4)),
      stage: weighted.stage,
      block,
      lower120: Number(lower120.toFixed(2)),
      lower300: Number(lower300.toFixed(2)),
      repairFlags: {
        recentMiss: Number(r30.missStreak || 0),
        weakBlocks: Number(block.weakBlocks || 0),
        blockMinRate: Number(block.minRate || 0),
        lower120: Number(lower120.toFixed(2)),
        stableMathExcluded: stableMathPenalty > 0
      }
    };
  });
  candidates.sort(sortStableDigitCandidates);
  return candidates;
}

function buildStableHeweiCandidates(records, actualGetter, options) {
  const opts = Object.assign({}, options && typeof options === "object" ? options : {});
  if (!Array.isArray(opts.excludeDigits) || !opts.excludeDigits.length) {
    const stableExclude = getStableMathExcludeDigitSet();
    if (stableExclude.size) opts.excludeDigits = Array.from(stableExclude);
  }
  const cacheKey = buildStableHeweiCandidatesCacheKey(records, opts.getterId || "default", opts);
  if (buildStableHeweiCandidatesLru.has(cacheKey)) {
    return buildStableHeweiCandidatesLru.get(cacheKey);
  }
  const candidates = buildStableHeweiCandidatesCore(records, actualGetter, opts);
  buildStableHeweiCandidatesLru.set(cacheKey, candidates);
  if (buildStableHeweiCandidatesLru.size > BUILD_STABLE_HEWEI_LRU_MAX) {
    const oldest = buildStableHeweiCandidatesLru.keys().next().value;
    buildStableHeweiCandidatesLru.delete(oldest);
  }
  return candidates;
}

function heweiCandidateWarning(candidate, options) {
  if (!candidate) return true;
  const opts = options && typeof options === "object" ? options : {};
  const guardRate = Number(opts.guardRate || HEWEI_GUARD_RATE);
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const lowerFloor = Number(opts.lowerFloor || Math.max(54, guardRate - 13));
  const block = candidate.block || emptyStabilityBlock(HEWEI_BLOCK_WINDOW, HEWEI_BLOCK_SIZE, floor);
  return Number(candidate.r30 && candidate.r30.missStreak || 0) >= 2 ||
    Number(candidate.r30 && candidate.r30.rate || 0) < guardRate ||
    Number(candidate.r60 && candidate.r60.rate || 0) < guardRate - 5 ||
    Number(candidate.r120 && candidate.r120.rate || 0) < guardRate - 8 ||
    Number(candidate.lower120 || 0) < lowerFloor ||
    Number(block.minRate || 0) < floor ||
    Number(block.weakBlocks || 0) > 0;
}

function selectStableHeweiCandidate(candidates, lockKey, performance, performanceKey, options) {
  const opts = options && typeof options === "object" ? options : {};
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const guardRate = Number(opts.guardRate || HEWEI_GUARD_RATE);
  const lowerFloor = Number(opts.lowerFloor || Math.max(54, guardRate - 13));
  const emptyBlock = emptyStabilityBlock(HEWEI_BLOCK_WINDOW, HEWEI_BLOCK_SIZE, floor);
  const best = candidates[0] || {
    digit: "--",
    score: 0,
    rLong: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r300: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r120: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r60: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r30: { total: 0, hit: 0, rate: null, missStreak: 0 },
    score: 0,
    stage: stableWindowStage(0),
    block: emptyBlock,
    lower120: null,
    lower300: null,
    repairFlags: {},
    truthAvailable: false
  };
  let selected = best;
  let reason = "best-stability-window";
  const perf = getModelStat(performance, performanceKey);
  const perfFloor = Number(opts.performanceFloor || Math.max(68, guardRate - 7));
  const weakPerf = perf.total >= 8 && Number(perf.rate || 0) < perfFloor;

  if (typeof state !== "undefined" && state) {
    if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
  }
  const locks = (typeof state !== "undefined" && state && state.stable120Locks) ? state.stable120Locks : {};
  const lock = locks[lockKey] || {};
  const oldDigit = lock.digit ? String(lock.digit) : "";
  if (oldDigit) {
    const old = candidates.find((item) => item.digit === oldDigit);
    if (old) {
      const oldHold = Number(lock.hold || 0);
      const oldWarning = heweiCandidateWarning(old, { guardRate, floor, lowerFloor });
      const bestClearlyBetter = best.score > old.score + HEWEI_SWITCH_MARGIN;
      if (!weakPerf && oldHold > 0 && oldHold < HEWEI_MIN_HOLD && !oldWarning && best.score <= old.score + HEWEI_SWITCH_MARGIN * 1.5) {
        selected = old;
        reason = "keep-min-hold-stable-hewei";
      } else if (!weakPerf && !oldWarning && !bestClearlyBetter) {
        selected = old;
        reason = "keep-locked-stable-hewei";
      } else if (!weakPerf && old.r120.rate >= best.r120.rate - 2 && old.r30.missStreak < 2 && Number(old.block && old.block.minRate || 0) >= floor && best.score <= old.score + HEWEI_SWITCH_MARGIN * 1.25) {
        selected = old;
        reason = "keep-close-stable-hewei";
      } else {
        reason = weakPerf ? "weak-hewei-switch" : "warning-switch-hewei-block";
      }
    }
  }

  const selectedWarning = heweiCandidateWarning(selected, { guardRate, floor, lowerFloor });
  const guarded = candidates
    .filter((item) => item &&
      item.digit !== "--" &&
      item.r30.rate >= guardRate &&
      item.r60.rate >= guardRate - 5 &&
      item.r120.rate >= guardRate - 8 &&
      item.r30.missStreak < 2 &&
      Number(item.lower120 || 0) >= lowerFloor &&
      item.block &&
      Number(item.block.minRate || 0) >= floor &&
      Number(item.block.weakBlocks || 0) === 0)
    .sort(sortStableDigitCandidates)[0];
  if (selectedWarning && guarded && guarded.digit !== selected.digit) {
    selected = guarded;
    reason = "hewei-stability-guard";
  }

  const backup = candidates.find((item) => item.digit !== selected.digit) || best;
  const oldValue = oldDigit || "";
  const hold = oldValue && oldValue === selected.digit ? Number(lock.hold || 0) + 1 : 1;
  if (locks && lockKey) {
    locks[lockKey] = {
      digit: selected.digit,
      hold,
      score: Number((selected.score || 0).toFixed(4)),
      rateLong: Number(selected.rLong.rate.toFixed(2)),
      rate300: Number(selected.r300.rate.toFixed(2)),
      rate120: Number(selected.r120.rate.toFixed(2)),
      rate60: Number(selected.r60.rate.toFixed(2)),
      rate30: Number(selected.r30.rate.toFixed(2)),
      lower120: Number((selected.lower120 || 0).toFixed(2)),
      lower300: Number((selected.lower300 || 0).toFixed(2)),
      blockMinRate: Number(((selected.block && selected.block.minRate) || 0).toFixed(2)),
      blockAvgRate: Number(((selected.block && selected.block.avgRate) || 0).toFixed(2)),
      blockWeakBlocks: Number((selected.block && selected.block.weakBlocks) || 0),
      blockVariance: Number(((selected.block && selected.block.variance) || 0).toFixed(2)),
      missStreak30: selected.r30.missStreak,
      stage: selected.stage,
      reason,
      updatedAt: (new Date()).toISOString()
    };
  }
  return { selected, backup, reason, weakPerf };
}

function stableHeweiDetail(candidates) {
  return (Array.isArray(candidates) ? candidates : []).slice(0, 10).map((item) => ({
    digit: item.digit,
    rateLong: Number(item.rLong.rate.toFixed(2)),
    rate300: Number(item.r300.rate.toFixed(2)),
    rate120: Number(item.r120.rate.toFixed(2)),
    rate60: Number(item.r60.rate.toFixed(2)),
    rate30: Number(item.r30.rate.toFixed(2)),
    lower120: Number((item.lower120 || 0).toFixed(2)),
    lower300: Number((item.lower300 || 0).toFixed(2)),
    blockMinRate: Number(((item.block && item.block.minRate) || 0).toFixed(2)),
    blockAvgRate: Number(((item.block && item.block.avgRate) || 0).toFixed(2)),
    blockWeakBlocks: Number((item.block && item.block.weakBlocks) || 0),
    blockVariance: Number(((item.block && item.block.variance) || 0).toFixed(2)),
    missStreak30: item.r30.missStreak,
    score: item.score,
    stage: item.stage
  }));
}

function stableHeweiStability(candidate, options) {
  const opts = options && typeof options === "object" ? options : {};
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  return {
    lower120: Number((candidate && candidate.lower120 || 0).toFixed(2)),
    lower300: Number((candidate && candidate.lower300 || 0).toFixed(2)),
    block: (candidate && candidate.block) || emptyStabilityBlock(HEWEI_BLOCK_WINDOW, HEWEI_BLOCK_SIZE, floor),
    repairFlags: (candidate && candidate.repairFlags) || {},
    minHold: HEWEI_MIN_HOLD,
    switchMargin: HEWEI_SWITCH_MARGIN,
    guardRate: Number(opts.guardRate || HEWEI_GUARD_RATE)
  };
}

function heweiPrimaryDigit(obj, preferField) {
  if (!obj || typeof obj !== "object") return "";
  if (preferField && obj[preferField] != null && obj[preferField] !== "") return String(obj[preferField]);
  if (obj.killHewei != null && obj.killHewei !== "") return String(obj.killHewei);
  if (obj.killDigit != null && obj.killDigit !== "") return String(obj.killDigit);
  if (obj.primaryDigit != null && obj.primaryDigit !== "") return String(obj.primaryDigit);
  return "";
}

function pickAlternateHeweiDigit(heweiResult, avoidDigits, preferField) {
  if (!heweiResult || typeof heweiResult !== "object") return null;
  const avoid = avoidDigits instanceof Set ? avoidDigits : new Set((avoidDigits || []).map(String));
  const current = heweiPrimaryDigit(heweiResult, preferField);
  if (current && !avoid.has(current)) return null;
  const detail = Array.isArray(heweiResult.detail) ? heweiResult.detail : [];
  for (let i = 0; i < detail.length; i += 1) {
    const digit = String(detail[i].digit || "");
    if (/^\d$/.test(digit) && !avoid.has(digit)) return digit;
  }
  const backup = String(heweiResult.backupDigit || "");
  if (/^\d$/.test(backup) && !avoid.has(backup)) return backup;
  for (let i = 0; i <= 9; i += 1) {
    const digit = String(i);
    if (!avoid.has(digit)) return digit;
  }
  return current || null;
}

function applyHeweiConfidenceFields(selected, picked, options) {
  const opts = options && typeof options === "object" ? options : {};
  const guardRate = Number(opts.guardRate || HEWEI_GUARD_RATE);
  const recommendable = Number(selected.r30 && selected.r30.rate || 0) >= MODEL_LOW_HIT_RATE
    || Number(selected.r60 && selected.r60.rate || 0) >= MODEL_LOW_HIT_RATE
    || Number(selected.r120 && selected.r120.rate || 0) >= guardRate - 8;
  const confidence = recommendable ? "normal" : "low";
  let reason = picked && picked.reason ? picked.reason : "best-stability-window";
  if (!recommendable && reason === "best-stability-window") reason = "low-confidence-hewei";
  return { recommendable, confidence, reason };
}

function reconcileHeweiKillBundle(bundle) {
  const total = bundle && bundle.totalHeweiKill;
  const unified = bundle && bundle.threeFourKill;
  const independent = bundle && bundle.independentHeweiKills;
  const notes = [];
  if (!total || !independent || typeof independent !== "object") {
    return { notes };
  }

  const wqbs = independent.wanQianBaiShi;
  const totalDigit = heweiPrimaryDigit(total, "killHewei");
  if (wqbs && /^\d$/.test(totalDigit) && heweiPrimaryDigit(wqbs, "killDigit") !== totalDigit) {
    wqbs.killDigit = totalDigit;
    wqbs.primaryDigit = total.primaryDigit || totalDigit;
    wqbs.backupDigit = total.backupDigit || wqbs.backupDigit;
    wqbs.reason = `aligned-with-total-hewei:${String(total.reason || "sync")}`;
    notes.push(`wanQianBaiShi->${totalDigit}`);
    if (typeof state !== "undefined" && state) {
      if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
      const totalLock = state.stable120Locks["v43:hewei:total"];
      if (totalLock) state.stable120Locks["v43:hewei:wanQianBaiShi"] = Object.assign({}, totalLock);
    }
  }

  const used = new Set();
  const slots = [
    {
      name: "total",
      result: total,
      preferField: "killHewei",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(total, "killHewei"),
      setDigit: (digit, backup) => {
        total.killHewei = digit;
        total.primaryDigit = digit;
        if (backup) total.backupDigit = backup;
      }
    },
    {
      name: "unified",
      result: unified,
      preferField: "killDigit",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(unified, "killDigit"),
      setDigit: (digit, backup) => {
        if (!unified) return;
        unified.killDigit = digit;
        unified.primaryDigit = digit;
        if (backup) unified.backupDigit = backup;
      }
    },
    {
      name: "wanQianBai",
      result: independent.wanQianBai,
      preferField: "killDigit",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(independent.wanQianBai, "killDigit"),
      setDigit: (digit, backup) => {
        independent.wanQianBai.killDigit = digit;
        independent.wanQianBai.primaryDigit = digit;
        if (backup) independent.wanQianBai.backupDigit = backup;
      }
    },
    {
      name: "qianBaiShi",
      result: independent.qianBaiShi,
      preferField: "killDigit",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(independent.qianBaiShi, "killDigit"),
      setDigit: (digit, backup) => {
        independent.qianBaiShi.killDigit = digit;
        independent.qianBaiShi.primaryDigit = digit;
        if (backup) independent.qianBaiShi.backupDigit = backup;
      }
    }
  ];

  slots.forEach((slot) => {
    if (!slot.result) return;
    let digit = slot.getDigit();
    if (!/^\d$/.test(digit)) return;
    if (!used.has(digit)) {
      used.add(digit);
      return;
    }
    const alt = pickAlternateHeweiDigit(slot.result, used, slot.preferField);
    if (alt && alt !== digit) {
      slot.setDigit(alt, slot.result[slot.backupField]);
      slot.result.reason = `dedup-shift:${digit}->${alt}`;
      notes.push(`${slot.name}:${digit}->${alt}`);
      digit = alt;
    }
    used.add(digit);
  });

  return { notes };
}

function calculateTotalHeweiKill(history, performance) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers) && record.numbers.length >= 4) : [];
  function actual(record) {
    return String(calculateMetrics(record.numbers).totalHewei);
  }
  const candidates = buildStableHeweiCandidates(safeHistory, actual, {
    floor: HEWEI_BLOCK_FLOOR,
    guardRate: HEWEI_GUARD_RATE,
    missPenalty: 2.5,
    getterId: "hewei:total"
  });
  const picked = selectStableHeweiCandidate(candidates, "v43:hewei:total", performance, "hewei.total", {
    floor: HEWEI_BLOCK_FLOOR,
    guardRate: HEWEI_GUARD_RATE,
    lowerFloor: 72,
    performanceFloor: 78
  });
  const selected = picked.selected;
  const backup = picked.backup;
  const confidenceMeta = applyHeweiConfidenceFields(selected, picked, {
    guardRate: HEWEI_GUARD_RATE
  });
  return {
    killHewei: selected.digit,
    primaryDigit: selected.digit,
    backupDigit: backup.digit,
    algorithm: "v43-total-hewei-stability-blocks",
    performance: modelPerformanceSummary(performance, "hewei.total"),
    detail: stableHeweiDetail(candidates),
    stage: selected.stage,
    stability: stableHeweiStability(selected, { floor: HEWEI_BLOCK_FLOOR, guardRate: HEWEI_GUARD_RATE }),
    confidence: confidenceMeta.confidence,
    recommendable: confidenceMeta.recommendable,
    reason: confidenceMeta.reason
  };
}

function calculateThreeFourHeweiKillDigit(history, performance) {
  const safeHistory = Array.isArray(history) ? history.filter(Boolean) : [];
  function group(record) { const g = getCompositeHeweiGroup(record); return Array.isArray(g) ? g.map(String) : []; }
  const candidates = buildStableHeweiCandidates(safeHistory, group, {
    floor: HEWEI_UNIFIED_BLOCK_FLOOR,
    guardRate: HEWEI_UNIFIED_GUARD_RATE,
    missPenalty: 2.5,
    getterId: "hewei:unified"
  });
  const picked = selectStableHeweiCandidate(candidates, "v43:hewei:unified", performance, "hewei.unified", {
    floor: HEWEI_UNIFIED_BLOCK_FLOOR,
    guardRate: HEWEI_UNIFIED_GUARD_RATE,
    lowerFloor: 56,
    performanceFloor: 65
  });
  const selected = picked.selected;
  const backup = picked.backup;
  const confidenceMeta = applyHeweiConfidenceFields(selected, picked, {
    guardRate: HEWEI_UNIFIED_GUARD_RATE
  });
  const reason = !confidenceMeta.recommendable && picked.reason === "best-stability-window"
    ? "low-confidence-unified-hewei"
    : confidenceMeta.reason;
  return {
    killDigit: selected.digit,
    primaryDigit: selected.digit,
    backupDigit: backup.digit,
    algorithm: "v43-unified-hewei-stability-blocks",
    performance: modelPerformanceSummary(performance, "hewei.unified"),
    detail: stableHeweiDetail(candidates),
    currentCompositeHeweiGroup: safeHistory[0] ? group(safeHistory[0]) : [],
    confidence: confidenceMeta.confidence,
    recommendable: confidenceMeta.recommendable,
    stage: selected.stage,
    stability: stableHeweiStability(selected, { floor: HEWEI_UNIFIED_BLOCK_FLOOR, guardRate: HEWEI_UNIFIED_GUARD_RATE }),
    reason
  };
}

function calculateIndependentHeweiKills(history, performance) {
  const safeHistory = Array.isArray(history) ? history.filter(Boolean) : [];
  function group(record) { const g = getCompositeHeweiGroup(record); return Array.isArray(g) ? g.map(String) : []; }
  function calculateOne(index, label, key) {
    const actual = (record) => {
      const g = group(record);
      return !g.length || g[index] === undefined ? "" : String(g[index]);
    };
    const candidates = buildStableHeweiCandidates(safeHistory, actual, {
      floor: HEWEI_BLOCK_FLOOR,
      guardRate: HEWEI_GUARD_RATE,
      missPenalty: 2.5,
      getterId: `hewei:${key}`
    });
    const picked = selectStableHeweiCandidate(candidates, `v43:hewei:${key}`, performance, `hewei.${key}`, {
      floor: HEWEI_BLOCK_FLOOR,
      guardRate: HEWEI_GUARD_RATE,
      lowerFloor: 72,
      performanceFloor: 78
    });
    const selected = picked.selected;
    const backup = picked.backup;
    const confidenceMeta = applyHeweiConfidenceFields(selected, picked, {
      guardRate: HEWEI_GUARD_RATE
    });
    return {
      label,
      key,
      killDigit: selected.digit,
      primaryDigit: selected.digit,
      backupDigit: backup.digit,
      algorithm: `v43-independent-hewei-${key}-stability-blocks`,
      performance: modelPerformanceSummary(performance, `hewei.${key}`),
      detail: stableHeweiDetail(candidates),
      stage: selected.stage,
      stability: stableHeweiStability(selected, { floor: HEWEI_BLOCK_FLOOR, guardRate: HEWEI_GUARD_RATE }),
      confidence: confidenceMeta.confidence,
      recommendable: confidenceMeta.recommendable,
      reason: confidenceMeta.reason
    };
  }
  const qianBaiGeStable = require("../../hewei-qian-bai-ge-stable/formula");
  const qianBaiGeFreq = require("../../hewei-qian-bai-ge-freq/formula");
  const qianShiGeStable = require("../../hewei-qian-shi-ge-stable/formula");
  const qianShiGeFreq = require("../../hewei-qian-shi-ge-freq/formula");
  return {
    wanQianBai: calculateOne(0, "千百十合尾", "wanQianBai"),
    qianBaiShi: calculateOne(1, "百十个合尾", "qianBaiShi"),
    wanQianBaiShi: calculateOne(2, "千百十个合尾", "wanQianBaiShi"),
    wanQianGe: qianBaiGeStable.calculate(safeHistory, performance),
    wanQianGeFreq: qianBaiGeFreq.calculate(safeHistory, performance),
    wanBaiShi: qianShiGeStable.calculate(safeHistory, performance),
    wanBaiShiFreq: qianShiGeFreq.calculate(safeHistory, performance)
  };
}


function clearBuildStableHeweiCandidatesCache() {
  try { buildStableHeweiCandidatesLru.clear(); } catch (_) {}
}

module.exports = {
  bindRuntime,
  calculateMetrics,
  getCompositeHeweiGroup,
  calculateTotalHeweiKill,
  calculateThreeFourHeweiKillDigit,
  calculateIndependentHeweiKills,
  reconcileHeweiKillBundle,
  buildStableHeweiCandidates,
  selectStableHeweiCandidate,
  getStableMathExcludeDigitSet,
  clearBuildStableHeweiCandidatesCache
};
