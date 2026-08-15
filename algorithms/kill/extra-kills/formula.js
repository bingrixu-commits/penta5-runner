"use strict";
const kit = require("../../_shared/formula_kit");
const { DIGIT_POOL, MODEL_LONG_WINDOW, MODEL_STABLE_WINDOW, MODEL_LOW_HIT_RATE, MODEL_MIN_SAMPLE, getModelStat, modelPerformanceSummary, stableWindowScore, wilsonLowerRate } = kit;
const heweiFormula = require("../../hewei/formula");
const ANY_TWO_HEWEI_SOURCE_DIGITS = "02468";
const ANY_TWO_HEWEI_CODE_SIZE = 4;
const MODEL_RATE_WINDOW = Number(process.env.MODEL_RATE_WINDOW || 30);
// anyTwoHeweiHit / anyThreeHeweiHit 基线为 10^4 前四位全枚举的精确值（所有候选码同质）：
// 二数合(02468排1码，6个两位和尾任一命中)=98.08%；三数合(5码，4个三位和尾任一命中)=93.75%。
const HIGH_BASELINE_SCORE_META = { spanAvoid:{scoreClass:"structural-rare-avoid",baseline:99.5,hint:"极罕形态过滤"}, repeatAvoid:{scoreClass:"structural-rare-avoid",baseline:99.5,hint:"极罕形态过滤"}, consecutiveAvoid:{scoreClass:"structural-rare-avoid",baseline:99.5,hint:"极罕形态过滤"}, sumRangeAvoid:{scoreClass:"structural-rare-avoid",baseline:99.0,hint:"极端区间过滤"}, sumAvoid:{scoreClass:"structural-rare-avoid",baseline:97.0,hint:"单点和值过滤"}, parityRatioAvoid:{scoreClass:"structural-rare-avoid",baseline:93.0,hint:"极端奇偶比过滤"}, sizeRatioAvoid:{scoreClass:"structural-rare-avoid",baseline:93.0,hint:"极端大小比过滤"}, anyTwoHeweiHit:{scoreClass:"wide-hit-baseline",baseline:98.08,hint:"宽命中参考"}, anyThreeHeweiHit:{scoreClass:"wide-hit-baseline",baseline:93.75,hint:"宽命中参考"}, twoDigitHit:{scoreClass:"pair-avoid-baseline",baseline:90.0,hint:"除对数（非共现命中）"} };
let _runtime = { getState: () => ({}), getStableMathExcludeDigitSet: () => new Set() };
function bindRuntime(runtime) { _runtime = Object.assign({}, _runtime, runtime || {}); }
function uniqueDigits(value) {
  const out = [];
  String(value === undefined || value === null ? "" : value).replace(/\D/g, "").split("").forEach((digit) => {
    if (digit !== "" && !out.includes(digit)) out.push(digit);
  });
  return out;
}

function digitSetCandidates(size) {
  const digits = DIGIT_POOL.slice();
  const out = [];
  function walk(start, picked) {
    if (picked.length === size) {
      out.push(picked.join(""));
      return;
    }
    for (let index = start; index < digits.length; index += 1) {
      picked.push(digits[index]);
      walk(index + 1, picked);
      picked.pop();
    }
  }
  walk(0, []);
  return out;
}

function isUniformParityDigitSet(value) {
  const digits = uniqueDigits(value);
  if (digits.length < 2) return false;
  let seenOdd = false;
  let seenEven = false;
  for (let index = 0; index < digits.length; index += 1) {
    if (Number(digits[index]) % 2 === 0) seenEven = true;
    else seenOdd = true;
    if (seenOdd && seenEven) return false;
  }
  return seenOdd || seenEven;
}

function getState() { return (typeof _runtime.getState === "function" ? _runtime.getState() : {}) || {}; }
function getStableMathExcludeDigitSet() { return typeof _runtime.getStableMathExcludeDigitSet === "function" ? _runtime.getStableMathExcludeDigitSet() : heweiFormula.getStableMathExcludeDigitSet(); }
function calculateMetrics(numbers) { return heweiFormula.calculateMetrics(numbers); }
function getCompositeHeweiGroup(record) { return heweiFormula.getCompositeHeweiGroup(record); }
function normalizeNumberList(numbers) {
  if (Array.isArray(numbers)) return numbers.map((n)=>n==null?"":String(n)).filter(Boolean);
  if (typeof numbers === "string") return numbers.replace(/\D/g,"").split("").filter(Boolean);
  return [];
}
function firstFourNumbers(record) {
  return (record && Array.isArray(record.numbers) ? record.numbers : []).slice(0, 4).map(String);
}

function shouldUseBackupAlgorithm(performance, key) {
  // 高基线模型不按「原始高通过率=强」切主备；三数共现已改为避开，走普通避开阈值
  if (isHighBaselineScoreKey(key)) return false;
  const stat = getModelStat(performance, key);
  return !!stat.low;
}

function pickByRank(scored, useBackup, modelKey, options) {
  const state = getState();
  const list = Array.isArray(scored) ? scored.filter(Boolean) : [];
  const primary = list[0] || null;
  if (!primary) return { selected: null, primary: null, backup: null, rank: 0, poolSize: 0, stable: true, reason: "empty" };

  const valueOf = (item) => {
    if (!item) return "";
    const keys = ["digit", "value", "sum", "shape", "key", "code", "pair", "label"];
    for (const key of keys) {
      if (item[key] !== undefined && item[key] !== null) return String(item[key]);
    }
    if (Array.isArray(item.digits)) return item.digits.map(String).join("");
    if (Array.isArray(item.killDigits)) return item.killDigits.map(String).join("");
    try { return JSON.stringify(item); } catch (err) { return String(item); }
  };

  const scoreOf = (item) => {
    if (!item) return 0;
    const raw = item.score !== undefined ? item.score : (item.rate !== undefined ? item.rate : (item.weight !== undefined ? item.weight : 0));
    const num = Number(raw);
    return Number.isFinite(num) ? num : 0;
  };

  const distinct = [];
  const seen = {};
  list.forEach((item) => {
    const value = valueOf(item);
    if (value && value !== "--" && !seen[value]) {
      seen[value] = true;
      distinct.push(item);
    }
  });
  if (!distinct.length) distinct.push(primary);

  const cfg = Object.assign({ poolSize: 5, keepGap: 12, unlockGap: 18, backupKeepGap: 6, minHold: 8 }, options || {});
  const poolSize = Math.max(1, Math.min(distinct.length, cfg.poolSize || 5));
  const pool = distinct.slice(0, poolSize);
  const primaryValue = valueOf(primary);
  const backup = distinct.find((item) => valueOf(item) !== primaryValue) || primary;

  let caller = "unknown";
  try {
    caller = String((new Error()).stack || "").split("\n").slice(2, 6).join("|").replace(/\s+/g, " ").slice(0, 220);
  } catch (err) {}
  const keyBase = String(modelKey || caller);
  const side = useBackup ? "backup" : "main";
  // 锁 key 只认模型+主备侧；旧版把候选池签名拼进 key，池子一抖就丢锁，对数每期乱换
  const lockKey = `v28:${keyBase}:${side}`;
  const latest = (typeof state !== "undefined" && state && state.latestRecord) || (typeof state !== "undefined" && state && Array.isArray(state.history) && state.history[0]) || {};
  const period = String(latest.period || latest.uid || "");

  if (typeof state !== "undefined" && state) {
    if (!state.modelSelectionLocks || typeof state.modelSelectionLocks !== "object") state.modelSelectionLocks = {};
  }
  const locks = (typeof state !== "undefined" && state && state.modelSelectionLocks) ? state.modelSelectionLocks : {};
  let lock = locks[lockKey] || {};
  if (!lock.value) {
    // 从旧版「池签名锁」里回收最近一条，避免升级瞬间全员清零
    const prefix = `v28:${keyBase}:`;
    const suffix = `:${side}`;
    let recovered = null;
    Object.keys(locks).forEach((key) => {
      if (key === lockKey) return;
      if (!key.startsWith(prefix) || !key.endsWith(suffix)) return;
      const candidate = locks[key];
      if (!candidate || !candidate.value) return;
      if (!recovered || String(candidate.updatedAt || "") > String(recovered.updatedAt || "")) recovered = candidate;
    });
    if (recovered) lock = recovered;
  }

  const oldValue = lock.value ? String(lock.value) : "";
  const oldItem = oldValue ? (pool.find((item) => valueOf(item) === oldValue) || distinct.find((item) => valueOf(item) === oldValue)) : null;
  const oldScore = oldItem ? scoreOf(oldItem) : -Infinity;
  const primaryScore = scoreOf(primary);
  const backupScore = scoreOf(backup);
  const holdCount = Number(lock.holdCount || 0);

  let selected = primary;
  let reason = "primary-best";

  if (useBackup) {
    if (oldItem && holdCount < cfg.minHold && (backupScore - oldScore) <= cfg.unlockGap) {
      selected = oldItem;
      reason = "low-hit-min-hold";
    } else if (oldItem && (backupScore - oldScore) <= cfg.backupKeepGap) {
      selected = oldItem;
      reason = "low-hit-keep-current-backup";
    } else {
      selected = backup;
      reason = oldItem ? "low-hit-switch-backup" : "low-hit-initial-backup";
    }
  } else if (oldItem && holdCount < cfg.minHold && (primaryScore - oldScore) <= cfg.unlockGap) {
    selected = oldItem;
    reason = "min-hold-lock";
  } else if (oldItem && (primaryScore - oldScore) <= cfg.keepGap) {
    selected = oldItem;
    reason = "stable-lock";
  } else {
    selected = primary;
    reason = oldItem ? "primary-clearly-better" : "primary-initial";
  }

  const selectedValue = valueOf(selected);
  const newHoldCount = oldValue && oldValue === selectedValue ? holdCount + 1 : 1;
  if (locks && lockKey) {
    locks[lockKey] = {
      value: selectedValue,
      period,
      holdCount: newHoldCount,
      reason,
      primaryValue,
      primaryScore,
      selectedScore: scoreOf(selected),
      updatedAt: (new Date()).toISOString()
    };
    // 清掉同模型旧版池签名锁，避免占满 160 配额把有效锁挤掉
    Object.keys(locks).forEach((key) => {
      if (key === lockKey) return;
      if (key.startsWith(`v28:${keyBase}:`) && (key.endsWith(":main") || key.endsWith(":backup"))) {
        delete locks[key];
      }
    });
    const keys = Object.keys(locks);
    if (keys.length > 160) {
      keys.slice(0, keys.length - 160).forEach((key) => { delete locks[key]; });
    }
  }

  const rank = Math.max(0, list.findIndex((item) => valueOf(item) === selectedValue));
  return {
    selected,
    primary,
    backup,
    rank,
    poolSize,
    stable: true,
    reason,
    holdCount: newHoldCount,
    scoreGap: Number((primaryScore - scoreOf(selected)).toFixed ? (primaryScore - scoreOf(selected)).toFixed(2) : (primaryScore - scoreOf(selected)))
  };
}

function highBaselineScoreMeta(key) {
  const clean = String(key || "").replace(/^extra\./, "");
  return HIGH_BASELINE_SCORE_META[clean] || null;
}

function isHighBaselineScoreKey(key) {
  return !!highBaselineScoreMeta(key);
}

function scoreModelCandidate(history, candidate, matcher, prior, hitMode) {
  const stats = candidateSuccessStatsMultiWindow(history, candidate, matcher, [30, 60, 120, MODEL_STABLE_WINDOW, MODEL_LONG_WINDOW], hitMode);
  const s30 = stats[30];
  const s60 = stats[60];
  const s120 = stats[120];
  const s300 = stats[MODEL_STABLE_WINDOW];
  const sLong = stats[MODEL_LONG_WINDOW];
  const weighted = stableWindowScore(s30, s60, s120, s300, sLong, hitMode ? 0.9 : 1.8);
  const lower120 = s120.total ? wilsonLowerRate(s120.hit, s120.total) : 0;
  const lowerLong = sLong.total ? wilsonLowerRate(sLong.hit, sLong.total) : 0;
  const recentRecoverBonus = Math.max(0, Number(s30.rate || 0) - Number(s120.rate || 0) - 3) * 0.08;
  const missPenalty = Math.max(0, Number(s120.maxMissStreak || 0) - 3) * 0.35 +
    Math.max(0, Number(sLong.maxMissStreak || 0) - 5) * 0.22;
  const score = weighted.score * 0.78 +
    Number(lower120 || 0) * 0.10 +
    Number(lowerLong || 0) * 0.07 +
    Math.min(Number(s60.rate || 0), Number(s120.rate || 0)) * 0.05 +
    recentRecoverBonus -
    missPenalty;
  return {
    s30,
    s60,
    s120,
    s300,
    sLong,
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    lower120: Number(lower120.toFixed(2)),
    lowerLong: Number(lowerLong.toFixed(2))
  };
}

function scoreAvoidCandidates(history, candidates, matcher, prior) {
  const scored = candidates.map((candidate) => {
    const hard = scoreModelCandidate(history, candidate, matcher, prior, false);
    const longHits = Math.max(0, Number((hard.sLong && hard.sLong.total) || 0) - Number((hard.sLong && hard.sLong.hit) || 0));
    const hasLongSample = Number(hard.sLong && hard.sLong.total || 0) > 0;
    const appearRate = hasLongSample ? 100 - Number(hard.sLong.rate) : null;
    return {
      value: String(candidate),
      sampleTotal: Number(hard.sLong && hard.sLong.total || 0),
      longHits,
      estimatedAppearRate: appearRate === null ? null : Number(appearRate.toFixed(2)),
      score: hard.score,
      rate30: hard.s30.total ? Number(hard.s30.rate.toFixed(2)) : null,
      rate60: hard.s60.total ? Number(hard.s60.rate.toFixed(2)) : null,
      rate120: hard.s120.total ? Number(hard.s120.rate.toFixed(2)) : null,
      rate300: hard.s300.total ? Number(hard.s300.rate.toFixed(2)) : null,
      maxMiss120: Number(hard.s120.maxMissStreak || 0),
      maxMissLong: Number(hard.sLong.maxMissStreak || 0),
      lower120: hard.lower120,
      stage: hard.stage
    };
  });
  scored.sort((a, b) => b.score !== a.score ? b.score - a.score : a.maxMissLong !== b.maxMissLong ? a.maxMissLong - b.maxMissLong : a.longHits !== b.longHits ? a.longHits - b.longHits : compareCandidateValue(a, b));
  return scored;
}

function scoreHitCandidates(history, candidates, matcher, prior) {
  const scored = candidates.map((candidate) => {
    const hard = scoreModelCandidate(history, candidate, matcher, prior, true);
    const longHits = Number((hard.sLong && hard.sLong.hit) || 0);
    return {
      value: String(candidate),
      sampleTotal: Number(hard.sLong && hard.sLong.total || 0),
      longHits,
      estimatedAppearRate: hard.sLong.total ? Number(hard.sLong.rate.toFixed(2)) : null,
      score: hard.score,
      rate30: hard.s30.total ? Number(hard.s30.rate.toFixed(2)) : null,
      rate60: hard.s60.total ? Number(hard.s60.rate.toFixed(2)) : null,
      rate120: hard.s120.total ? Number(hard.s120.rate.toFixed(2)) : null,
      rate300: hard.s300.total ? Number(hard.s300.rate.toFixed(2)) : null,
      maxMiss120: Number(hard.s120.maxMissStreak || 0),
      maxMissLong: Number(hard.sLong.maxMissStreak || 0),
      missStreak30: Number(hard.s30.missStreak || 0),
      lower120: hard.lower120,
      stage: hard.stage
    };
  });
  scored.sort((a, b) => b.score !== a.score ? b.score - a.score : a.maxMissLong !== b.maxMissLong ? a.maxMissLong - b.maxMissLong : a.longHits !== b.longHits ? b.longHits - a.longHits : compareCandidateValue(a, b));
  return scored;
}

function candidateSuccessStatsMultiWindow(history, candidate, matcher, limits, hitMode) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const maxLimit = Math.max(...lims);
  const rows = (Array.isArray(history) ? history : []).slice(0, maxLimit);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, missStreak: 0, maxMissStreak: 0, rate: null, _leading: true, _currentRun: 0 };
  });
  rows.forEach((record, index) => {
    let matched = false;
    try {
      matched = !!matcher(record, candidate);
    } catch (err) {
      matched = false;
    }
    const ok = hitMode ? matched : !matched;
    lims.forEach((limit) => {
      if (index >= limit) return;
      const stat = results[limit];
      stat.total += 1;
      if (ok) {
        stat.hit += 1;
        stat._currentRun = 0;
        stat._leading = false;
      } else {
        stat._currentRun += 1;
        if (stat._currentRun > stat.maxMissStreak) stat.maxMissStreak = stat._currentRun;
        if (stat._leading) stat.missStreak += 1;
      }
    });
  });
  lims.forEach((limit) => {
    const stat = results[limit];
    stat.rate = stat.total ? stat.hit * 100 / stat.total : null;
    delete stat._leading;
    delete stat._currentRun;
  });
  return results;
}

function compareCandidateValue(left, right) {
  const a = String(left && left.value !== undefined ? left.value : left);
  const b = String(right && right.value !== undefined ? right.value : right);
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return a.localeCompare(b, "zh-CN");
}

function threeDigitCooccurs(record, candidate) {
  const actual = firstFourNumbers(record);
  return String(candidate).split("").every((digit) => actual.includes(String(digit)));
}

function firstFourSum(record) {
  return firstFourNumbers(record).reduce((sum, n) => sum + Number(n || 0), 0);
}

function firstFourSumInRange(record, value) {
  const range = normalizeNumberRange(value);
  if (!range) return false;
  const sum = firstFourSum(record);
  return sum >= range.start && sum <= range.end;
}

function extraStructureMatched(record, key, candidate) {
  const value = String(candidate || "");
  if (key === "sumRangeAvoid") return firstFourSumInRange(record, value);
  if (key === "spanAvoid") return firstFourSpan(record) !== null && String(firstFourSpan(record)) === value;
  if (key === "repeatAvoid") {
    if (value === "重号") return firstFourHasRepeat(record);
    return firstFourRepeatShape(record) === value;
  }
  if (key === "consecutiveAvoid") {
    if (value === "连号") return firstFourHasConsecutive(record);
    return firstFourConsecutiveShape(record) === value;
  }
  if (key === "parityRatioAvoid") return firstFourParityRatio(record) === value;
  if (key === "sizeRatioAvoid") return firstFourSizeRatio(record) === value;
  return false;
}

function heweiCombinationTailMaskFromNumbers(numbers, size) {
  const actual = (Array.isArray(numbers) ? numbers : []).map((value) => Number(value));
  const need = Math.max(1, Number(size || 1));
  if (actual.length < need || actual.some((value) => !Number.isFinite(value))) return 0;
  let mask = 0;
  function walk(start, picked, sum) {
    if (picked === need) {
      mask |= (1 << (Math.abs(sum) % 10));
      return;
    }
    for (let index = start; index < actual.length; index += 1) {
      walk(index + 1, picked + 1, sum + actual[index]);
    }
  }
  walk(0, 0, 0);
  return mask;
}

function anyHeweiCombinationHitOnNumbers(numbers, candidate, size, minMatched) {
  const selected = uniqueDigits(candidate).slice(0, 10);
  const required = Math.max(1, Number(minMatched || 1));
  if (selected.length < required) return false;
  const tailMask = heweiCombinationTailMaskFromNumbers(numbers, size);
  if (!tailMask) return false;
  let matched = 0;
  for (let index = 0; index < selected.length; index += 1) {
    const digit = Number(String(selected[index]));
    if (Number.isFinite(digit) && digit >= 0 && digit <= 9 && (tailMask & (1 << digit))) matched += 1;
  }
  return matched >= required;
}

function anyHeweiCombinationHit(record, candidate, size, minMatched) {
  return anyHeweiCombinationHitOnNumbers(firstFourNumbers(record), candidate, size, minMatched);
}

function sumAvoidCandidates() {
  const out = [];
  for (let i = 10; i <= 26; i++) out.push(String(i));
  return out;
}

function sumRangeAvoidCandidates() {
  const out = [];
  for (let width = 3; width <= 7; width += 1) {
    for (let start = 0; start <= 36 - width + 1; start += 1) {
      const end = start + width - 1;
      out.push(`${start}-${end}`);
    }
  }
  return out;
}

function spanAvoidCandidates() {
  const out = [];
  for (let i = 0; i <= 9; i += 1) out.push(String(i));
  return out;
}

function repeatAvoidCandidates() {
  return ["重号", "一对", "双重", "三重", "四重"];
}

function consecutiveAvoidCandidates() {
  return ["连号", "二连", "三连", "四连"];
}

function parityRatioCandidates() {
  return ["0单4双", "1单3双", "2单2双", "3单1双", "4单0双"];
}

function sizeRatioCandidates() {
  return ["0大4小", "1大3小", "2大2小", "3大1小", "4大0小"];
}

function threeDigitAvoidCandidates() {
  const out = [];
  for (let a = 0; a < 8; a++) {
    for (let b = a + 1; b < 9; b++) {
      for (let c = b + 1; c < 10; c++) out.push(`${a}${b}${c}`);
    }
  }
  return out;
}

function twoDigitAvoidCandidates() {
  const out = [];
  for (let a = 0; a <= 8; a++) {
    for (let b = a + 1; b <= 9; b++) {
      out.push(`${a}${b}`);
    }
  }
  return out;
}

function anyTwoHeweiCandidates() {
  return digitSetCandidatesFromDigits(ANY_TWO_HEWEI_SOURCE_DIGITS, ANY_TWO_HEWEI_CODE_SIZE);
}

function isAnyTwoHeweiCode(value) {
  const code = String(value || "").replace(/\D/g, "");
  if (code.length !== ANY_TWO_HEWEI_CODE_SIZE) return false;
  if (new Set(code.split("")).size !== ANY_TWO_HEWEI_CODE_SIZE) return false;
  return code.split("").every((digit) => ANY_TWO_HEWEI_SOURCE_DIGITS.includes(digit));
}

function digitSetCandidatesMixedParity(size) {
  return digitSetCandidates(size).filter((code) => !isUniformParityDigitSet(code));
}

function digitSetCandidatesFromDigits(sourceDigits, size) {
  const digits = uniqueDigits(Array.isArray(sourceDigits) ? sourceDigits.join("") : sourceDigits);
  const out = [];
  function walk(start, picked) {
    if (picked.length === size) {
      out.push(picked.join(""));
      return;
    }
    for (let index = start; index < digits.length; index += 1) {
      picked.push(digits[index]);
      walk(index + 1, picked);
      picked.pop();
    }
  }
  walk(0, []);
  return out;
}

function calculateExtraAvoidKills(history, performance) {
  const state = getState();
  function stableDynamicIndex(records, size) {
    if (!size) return 0;
    const latest = records && records[0] ? records[0] : {};
    const periodTail = Number(String(latest.period || latest.uid || "0").replace(/\D/g, "").slice(-4) || 0);
    const nums = Array.isArray(latest.numbers) ? latest.numbers : [];
    const numberSeed = nums.reduce((sum, n, index) => sum + Number(n || 0) * (index + 3), 0);
    return Math.abs(periodTail + numberSeed) % size;
  }

  function calculateOne(key, label, candidates, matcher, prior, mode, options) {
    const opts = options && typeof options === "object" ? options : {};
    const hitMode = mode === "hit";
    const blockedValues = new Set((Array.isArray(opts.excludeValues) ? opts.excludeValues : []).map((value) => String(value)));
    const preferDigits = new Set((Array.isArray(opts.preferDigits) ? opts.preferDigits : []).map((digit) => String(digit)).filter((digit) => /^\d$/.test(digit)));
    const overlapCount = (value) => {
      if (!preferDigits.size) return 0;
      const text = String(value == null ? "" : value).replace(/\D/g, "");
      if (!text) return 0;
      let n = 0;
      const seen = new Set();
      for (const ch of text) {
        if (seen.has(ch)) continue;
        seen.add(ch);
        if (preferDigits.has(ch)) n += 1;
      }
      return n;
    };
    let scored = hitMode ? scoreHitCandidates(history, candidates, matcher, prior) : scoreAvoidCandidates(history, candidates, matcher, prior);
    if (blockedValues.size) {
      scored = scored.filter((item) => !blockedValues.has(String(item && item.value !== undefined ? item.value : "")));
    }
    scored = scored.filter((item) => Number(item && item.sampleTotal || 0) > 0);
    if (preferDigits.size) {
      scored = scored.map((item) => {
        const overlap = overlapCount(item && item.value);
        const bonus = hitMode ? overlap * 0.22 : overlap * 0.85;
        return Object.assign({}, item, {
          preferOverlap: overlap,
          score: Number((Number(item.score || 0) + bonus).toFixed(4))
        });
      });
      scored.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
    }
    if (!scored.length) {
      return {
        key,
        label,
        value: "",
        code: "",
        primary: "",
        backup: "",
        algorithm: "unavailable-no-real-sample",
        hardnessScore: null,
        rate30: null,
        rate60: null,
        rate120: null,
        rate300: null,
        maxMiss120: null,
        maxMissLong: null,
        lower120: null,
        stage: "暂无真实样本",
        hitMode,
        matchMode: "unavailable",
        jointSwitchAllowed: false,
        dynamicEveryPrediction: false,
        rotationPool: [],
        rotationIndex: null,
        rotationMaxGap: null,
        performance: null,
        truthAvailable: false,
        confidence: null,
        recommendable: false,
        preferOverlap: 0
      };
    }
    const lowRaw = shouldUseBackupAlgorithm(performance, `extra.${key}`);
    // 账本被历史烂码拖低、但当前候选 rate120 已很高时，禁止强制 backup（threeDigitHit 曾长期粘 123/次优）
    const topRate120 = Number(scored[0] && scored[0].rate120);
    const low = !!(lowRaw && !(Number.isFinite(topRate120) && topRate120 >= 90));
    let picked = pickByRank(scored, low, `extra.${key}`, opts.rankOptions || null);
    // 三数共现：短窗实绩落后时跟数据换码（只动该组），避免 hardness 同分把烂码粘死
    if (["threeDigitAvoid", "threeDigitHit", "threeDigitAvoid2"].includes(key) && picked && picked.selected && picked.primary) {
      const sel = picked.selected;
      const pri = picked.primary;
      const selVal = String(sel.value != null ? sel.value : "");
      const priVal = String(pri.value != null ? pri.value : "");
      if (selVal && priVal && selVal !== priVal) {
        const selR30 = Number(sel.rate30);
        const priR30 = Number(pri.rate30);
        const selR120 = Number(sel.rate120);
        const priR120 = Number(pri.rate120);
        const lag30 = Number.isFinite(selR30) && Number.isFinite(priR30) && (priR30 - selR30) >= 3;
        const weak30 = Number.isFinite(selR30) && selR30 < 90 && Number.isFinite(priR30) && priR30 > selR30;
        const lag120 = Number.isFinite(selR120) && Number.isFinite(priR120) && (priR120 - selR120) >= 2;
        if (lag30 || weak30 || lag120) {
          picked = Object.assign({}, picked, { selected: pri, reason: "data-lag-switch", holdCount: 1 });
          if (typeof state !== "undefined" && state) {
            if (!state.modelSelectionLocks || typeof state.modelSelectionLocks !== "object") state.modelSelectionLocks = {};
            const latest = state.latestRecord || (Array.isArray(state.history) && state.history[0]) || {};
            state.modelSelectionLocks[`v28:extra.${key}:main`] = {
              value: priVal,
              period: String(latest.period || latest.uid || ""),
              holdCount: 1,
              reason: "data-lag-switch",
              primaryValue: priVal,
              primaryScore: Number(pri.score || 0),
              selectedScore: Number(pri.score || 0),
              updatedAt: (new Date()).toISOString()
            };
          }
        }
      }
    }
    const firstRealCandidate = scored[0];
    let selected = picked.selected || firstRealCandidate;
    let primary = picked.primary || firstRealCandidate;
    let backup = picked.backup || primary;
    let algorithm = hitMode ? (low ? "dynamic-cooccur-hit-backup-low-hit-rate" : "dynamic-cooccur-hit-primary") : (key === "sumAvoid" ? (low ? "dynamic-practical-sum-backup" : "dynamic-practical-sum-rotation") : (low ? "independent-backup-low-hit-rate" : "dynamic-big-data-primary"));
    if (picked && picked.reason === "data-lag-switch") algorithm = "data-lag-switch-three-digit";
    let rotationPool = [];
    let rotationIndex = null;
    let rotationMaxGap = null;
    if (!hitMode && key === "sumAvoid" && scored.length) {
      const dynamicPool = scored.slice(0, Math.min(6, scored.length));
      const dynamicPick = dynamicPool[stableDynamicIndex(history, dynamicPool.length)] || selected;
      selected = low ? (dynamicPool[1] || dynamicPick) : dynamicPick;
      primary = dynamicPool[0] || primary;
      backup = dynamicPool[1] || primary;
    }
  if (hitMode && (key === "anyTwoHeweiHit" || key === "anyThreeHeweiHit") && scored.length) {
      // 二/三数合：尽量锁住上一期数字；仅当旧码掉出近顶分差才换（不要换数字）。
      // 三数合额外：近窗加权 + 更严解锁（低 rate120 / 近窗连挂），避免烂码粘死拖基线。
      const prevExtra = ((((getState() || {}).currentPrediction || {}).extraKills || {})[key]) || {};
      const prevCode = String(prevExtra.code || prevExtra.value || "").replace(/\D/g, "");
      if (key === "anyThreeHeweiHit") {
        scored = scored.map((item) => {
          const r30 = Number(item && item.rate30 || 0);
          const lower = Number(item && item.lower120 || 0);
          const miss30 = Number(item && item.missStreak30 || 0);
          const nearBonus = r30 * 0.14 + lower * 0.10 - Math.max(0, miss30 - 1) * 0.55;
          return Object.assign({}, item, {
            score: Number((Number(item && item.score || 0) + nearBonus).toFixed(4))
          });
        });
        scored.sort((a, b) => b.score !== a.score ? b.score - a.score : a.maxMissLong !== b.maxMissLong ? a.maxMissLong - b.maxMissLong : a.longHits !== b.longHits ? b.longHits - a.longHits : compareCandidateValue(a, b));
      }
      const bestScore = Number(scored[0] && scored[0].score || 0);
      const keepGap = key === "anyThreeHeweiHit" ? 0.35 : 1.0;
      const prevItem = prevCode
        ? scored.find((item) => String(item && item.value || "").replace(/\D/g, "") === prevCode)
        : null;
      const prevScore = prevItem ? Number(prevItem.score || 0) : NaN;
      const scoreKeep = !!(prevItem && Number.isFinite(prevScore) && Number.isFinite(bestScore) && (bestScore - prevScore) <= keepGap);
      let keepPrev = scoreKeep;
      let breakReason = "";
      if (key === "anyThreeHeweiHit") {
        const rate120Ok = Number(prevItem && prevItem.rate120 || 0) >= 96;
        const missOk = Number(prevItem && prevItem.missStreak30 || 0) < 2;
        if (!prevItem) breakReason = "top1";
        else if (!rate120Ok) breakReason = "low120";
        else if (!missOk) breakReason = "recent-miss";
        else if (!scoreKeep) breakReason = "score-gap";
        keepPrev = !!(prevItem && rate120Ok && missOk && scoreKeep);
      }
      selected = keepPrev ? prevItem : scored[0];
      primary = scored[0];
      backup = scored[1] || scored[0];
      rotationPool = [];
      rotationIndex = null;
      rotationMaxGap = null;
      if (key === "anyTwoHeweiHit") {
        algorithm = keepPrev ? "sticky-hold-two-hewei" : "sticky-top1-two-hewei";
      } else if (keepPrev) {
        algorithm = "sticky-hold-three-hewei";
      } else if (breakReason === "low120") {
        algorithm = "sticky-break-three-hewei-low120";
      } else if (breakReason === "recent-miss") {
        algorithm = "sticky-break-three-hewei-recent-miss";
      } else if (breakReason === "score-gap") {
        algorithm = "sticky-break-three-hewei-score-gap";
      } else {
        algorithm = "sticky-top1-three-hewei";
      }
    }
    const selectedValue = String(selected.value !== undefined ? selected.value : (selected.candidate !== undefined ? selected.candidate : ""));
    const primaryValue = String(primary.value !== undefined ? primary.value : (primary.candidate !== undefined ? primary.candidate : selectedValue));
    const backupValue = String(backup.value !== undefined ? backup.value : (backup.candidate !== undefined ? backup.candidate : primaryValue));
    const baselineMeta = highBaselineScoreMeta(key);
    const recommendThreshold = hitMode ? 52 : 78;
    const recommendable = Number(selected.rate120 || selected.rate60 || selected.rate30 || 0) >= recommendThreshold
      && Number(selected.lower120 || 0) >= (hitMode ? 45 : 65);
    const confidence = recommendable ? "normal" : "low";
    return {
      key,
      label,
      value: selectedValue,
      code: selectedValue,
      primary: primaryValue,
      backup: backupValue,
      algorithm,
      hardnessScore: Number(selected.score || 0),
      rate30: selected.rate30,
      rate60: selected.rate60,
      rate120: selected.rate120,
      rate300: selected.rate300,
      maxMiss120: selected.maxMiss120,
      maxMissLong: selected.maxMissLong,
      lower120: selected.lower120,
      stage: selected.stage,
      hitMode,
      matchMode: key === "anyTwoHeweiHit" ? "hit-if-any-two-hewei-tail-in-02468-exclude-one" : (key === "anyThreeHeweiHit" ? "hit-if-any-three-hewei-tail-in-selected-five" : (hitMode ? "hit-if-all-digits-in-first-four" : "avoid-if-not-matched")),
      scoreClass: baselineMeta ? baselineMeta.scoreClass : (hitMode ? "predictive-hit" : "predictive-avoid"),
      randomBaseline: baselineMeta ? baselineMeta.baseline : null,
      scoreHint: baselineMeta ? `${baselineMeta.hint}，非预测力` : "",
      predictivePower: !baselineMeta,
      jointSwitchAllowed: false,
      dynamicEveryPrediction: true,
      rotationPool,
      rotationIndex,
      rotationMaxGap,
      performance: getModelStat(performance, `extra.${key}`) || null,
      confidence,
      recommendable,
      preferOverlap: Number(selected.preferOverlap || 0)
    };
  }

  const stablePreferDigits = Array.from(getStableMathExcludeDigitSet());
  const twoAvoidCandidates = twoDigitAvoidCandidates();
  const twoAvoidPicked = [];
  const twoAvoidRankOptions = { poolSize: 8, keepGap: 10, unlockGap: 16, backupKeepGap: 8, minHold: 12 };
  // 三数共现：单独放宽粘性，避免与四定对数共用导致长期不换码
  const threeAvoidRankOptions = { poolSize: 8, keepGap: 4, unlockGap: 8, backupKeepGap: 4, minHold: 3 };
  const twoAvoid1 = calculateOne("twoDigitAvoid", "对数避开1组", twoAvoidCandidates, (record, value) => threeDigitCooccurs(record, value), 0.45, null, { excludeValues: twoAvoidPicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  if (twoAvoid1.value) twoAvoidPicked.push(twoAvoid1.value);
  const twoAvoid2 = calculateOne("twoDigitAvoid2", "对数避开2组", twoAvoidCandidates, (record, value) => threeDigitCooccurs(record, value), 0.45, null, { excludeValues: twoAvoidPicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  if (twoAvoid2.value) twoAvoidPicked.push(twoAvoid2.value);
  const twoAvoid3 = calculateOne("twoDigitAvoid3", "对数避开3组", twoAvoidCandidates, (record, value) => threeDigitCooccurs(record, value), 0.45, null, { excludeValues: twoAvoidPicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  const threeCandidates = threeDigitAvoidCandidates();
  const threePicked = [];
  const threeAvoid1 = calculateOne("threeDigitAvoid", "三数共现避开1组", threeCandidates, (record, value) => threeDigitCooccurs(record, value), 0.25, null, { excludeValues: threePicked, rankOptions: threeAvoidRankOptions, preferDigits: stablePreferDigits });
  if (threeAvoid1.value) threePicked.push(threeAvoid1.value);
  const threeAvoid2 = calculateOne("threeDigitHit", "三数共现避开2组", threeCandidates, (record, value) => threeDigitCooccurs(record, value), 0.25, null, { excludeValues: threePicked, rankOptions: threeAvoidRankOptions, preferDigits: stablePreferDigits });
  if (threeAvoid2.value) threePicked.push(threeAvoid2.value);
  const threeAvoid3 = calculateOne("threeDigitAvoid2", "三数共现避开3组", threeCandidates, (record, value) => threeDigitCooccurs(record, value), 0.25, null, { excludeValues: threePicked, rankOptions: threeAvoidRankOptions, preferDigits: stablePreferDigits });
  return {
    sumAvoid: calculateOne("sumAvoid", "单点和值过滤（高基线）", sumAvoidCandidates(), (record, value) => firstFourSum(record) === Number(value), 0.8),
    sumRangeAvoid: calculateOne("sumRangeAvoid", "和值极端区间过滤（高基线）", sumRangeAvoidCandidates(), (record, value) => firstFourSumInRange(record, value), 0.55),
    spanAvoid: calculateOne("spanAvoid", "跨度极罕形态过滤（高基线）", spanAvoidCandidates(), (record, value) => extraStructureMatched(record, "spanAvoid", value), 0.55),
    repeatAvoid: calculateOne("repeatAvoid", "重号极罕形态过滤（高基线）", repeatAvoidCandidates(), (record, value) => extraStructureMatched(record, "repeatAvoid", value), 0.55),
    consecutiveAvoid: calculateOne("consecutiveAvoid", "连号极罕形态过滤（高基线）", consecutiveAvoidCandidates(), (record, value) => extraStructureMatched(record, "consecutiveAvoid", value), 0.55),
    parityRatioAvoid: calculateOne("parityRatioAvoid", "极端奇偶比过滤（高基线）", parityRatioCandidates(), (record, value) => extraStructureMatched(record, "parityRatioAvoid", value), 0.55),
    sizeRatioAvoid: calculateOne("sizeRatioAvoid", "极端大小比过滤（高基线）", sizeRatioCandidates(), (record, value) => extraStructureMatched(record, "sizeRatioAvoid", value), 0.55),
    threeDigitAvoid: threeAvoid1,
    twoDigitAvoid: twoAvoid1,
    twoDigitAvoid2: twoAvoid2,
    twoDigitAvoid3: twoAvoid3,
    twoDigitHit: calculateOne("twoDigitHit", "两数共现避开（基线参考）", twoDigitAvoidCandidates(), (record, value) => threeDigitCooccurs(record, value), 0.45, null, { rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits }),
    threeDigitHit: threeAvoid2,
    threeDigitAvoid2: threeAvoid3,
    anyTwoHeweiHit: calculateOne("anyTwoHeweiHit", "二数合尾02468排1码命中（高基线参考）", anyTwoHeweiCandidates(), (record, value) => anyHeweiCombinationHit(record, value, 2, 1), 0.55, "hit"),
    anyThreeHeweiHit: calculateOne("anyThreeHeweiHit", "三数合尾宽命中（高基线参考）", digitSetCandidatesMixedParity(5), (record, value) => anyHeweiCombinationHit(record, value, 3, 1), 0.62, "hit")
  };
}


module.exports = {
  bindRuntime,
  calculateExtraAvoidKills,
  anyTwoHeweiCandidates,
  digitSetCandidatesMixedParity,
  isAnyTwoHeweiCode,
  anyHeweiCombinationHit
};
