"use strict";
/** Shared L1 framework item pipeline + autoTop6/killFramework builders (extracted). */
const kit = require("../../_shared/formula_kit");
const { DIGIT_POOL, MODEL_LONG_WINDOW, MODEL_STABLE_WINDOW, MODEL_LOW_HIT_RATE, getModelStat, modelPerformanceSummary, stableWindowScore, wilsonLowerRate, rateVariance } = kit;
const extraFormula = require("../../extra-kills/formula");
const heweiFormula = require("../../hewei/formula");

const MODEL_NEAR_WINDOW = Number(process.env.MODEL_NEAR_WINDOW || 40);
const ANY_TWO_HEWEI_SOURCE_DIGITS = "02468";
const ANY_TWO_HEWEI_CODE_SIZE = 4;
const AUTO_TOP6_RECENT_MISS_LIMIT = Number(process.env.AUTO_TOP6_RECENT_MISS_LIMIT || 3);
const AUTO_TOP6_LONG_FLOOR_OFFSET = Number(process.env.AUTO_TOP6_LONG_FLOOR_OFFSET || 3);
const AUTO_TOP6_LOWER_FLOOR_OFFSET = Number(process.env.AUTO_TOP6_LOWER_FLOOR_OFFSET || 12);

const AUTO_TOP6_POSITIONS = [
  { key: "wan", label: "千位", index: 0 },
  { key: "qian", label: "百位", index: 1 },
  { key: "bai", label: "十位", index: 2 },
  { key: "shi", label: "个位", index: 3 }
];
const AUTO_TOP6_HEWEI = [
  { key: "total", label: "总合尾综合", index: -2 },
  { key: "wanQianBai", label: "千百十合尾", index: 0 },
  { key: "qianBaiShi", label: "百十个合尾", index: 1 },
  { key: "wanQianBaiShi", label: "千百十个合尾", index: 2 },
  { key: "unified", label: "三个合尾统一", index: -1 }
];
const AUTO_TOP6_EXTRAS = [
  { key: "sumAvoid", label: "单点和值过滤（高基线）", mode: "avoid", kind: "sum", scoreClass: "structural-rare-avoid" },
  { key: "sumRangeAvoid", label: "和值极端区间过滤（高基线）", mode: "avoid", kind: "sumRange", scoreClass: "structural-rare-avoid" },
  { key: "spanAvoid", label: "跨度极罕形态过滤（高基线）", mode: "avoid", kind: "span", scoreClass: "structural-rare-avoid" },
  { key: "repeatAvoid", label: "重号极罕形态过滤（高基线）", mode: "avoid", kind: "structure", scoreClass: "structural-rare-avoid" },
  { key: "consecutiveAvoid", label: "连号极罕形态过滤（高基线）", mode: "avoid", kind: "structure", scoreClass: "structural-rare-avoid" },
  { key: "parityRatioAvoid", label: "极端奇偶比过滤（高基线）", mode: "avoid", kind: "ratio", scoreClass: "structural-rare-avoid" },
  { key: "sizeRatioAvoid", label: "极端大小比过滤（高基线）", mode: "avoid", kind: "ratio", scoreClass: "structural-rare-avoid" },
  { key: "threeDigitAvoid", label: "三数共现避开1组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid", label: "对数避开1组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid2", label: "对数避开2组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid3", label: "对数避开3组", mode: "avoid", kind: "digits" },
  { key: "twoDigitHit", label: "两数共现避开（基线参考）", mode: "avoid", kind: "digits", scoreClass: "pair-avoid-baseline" },
  { key: "threeDigitHit", label: "三数共现避开2组", mode: "avoid", kind: "digits" },
  { key: "threeDigitAvoid2", label: "三数共现避开3组", mode: "avoid", kind: "digits" },
  { key: "anyTwoHeweiHit", label: "二数合尾02468排1码命中（高基线参考）", mode: "hit", kind: "heweiTails", scoreClass: "wide-hit-baseline" },
  { key: "anyThreeHeweiHit", label: "三数合尾宽命中（高基线参考）", mode: "hit", kind: "heweiTails", scoreClass: "wide-hit-baseline" }
];

const AUTO_TOP6_SNAPSHOT_VERSION = "backend-auto-top6-snapshot-v10-formal6-unique-exclude-digit";
const RECOMMEND2_SNAPSHOT_VERSION = "backend-recommend2-snapshot-v16-formal-anytwo4-pool02468-label-v2";
const KILL_FRAMEWORK_SNAPSHOT_VERSION = "backend-kill-framework-snapshot-v8-formal-anytwo4-pool02468-label-v2";
const KILL_FRAMEWORK_ITEM_COUNT = 8;
const COMPACT_ATTACK_SNAPSHOT_VERSION = "backend-three-ding-framework-v9-no-shape-anytwo4-pool02468-label-v2";
const COMPACT_ATTACK_ITEM_COUNT = 5;
const THREE_DING_QBS_KEYS = new Set(["wan", "qian", "bai"]);
const THREE_DING_TWO_SNAPSHOT_VERSION = "backend-three-ding-two-v12-force-unique-digit-anytwo4-pool02468-label-v3";
const THREE_DING_TWO_ANY_TWO_SEED = ANY_TWO_HEWEI_SOURCE_DIGITS;
const THREE_DING_TWO_ITEM_COUNT = 5;
const THREE_DING_ONE_SNAPSHOT_VERSION = "backend-three-ding-one-v10-force-unique-digit-anytwo4-pool02468-label-v2";
const THREE_DING_ONE_ITEM_COUNT = 6;
const BROTHER_NUMBER_PAIRS = ["01", "12", "23", "34", "45", "56", "67", "78", "89", "09"];
let _runtime = { getState: () => ({}) };
















// Runner-parity env constants required by extracted L1 builders (must stay local; not host globals).
const HEWEI_BLOCK_FLOOR = Number(process.env.HEWEI_BLOCK_FLOOR || 72);
const HEWEI_UNIFIED_BLOCK_FLOOR = Number(process.env.HEWEI_UNIFIED_BLOCK_FLOOR || 60);

const THREE_DUISHU_SNAPSHOT_VERSION = "backend-three-duishu-snapshot-v1";





const THREE_DUISHU_MIN_TRAIN = Number(process.env.THREE_DUISHU_MIN_TRAIN || 80);

const THREE_DUISHU_WALK_WINDOW = Number(process.env.THREE_DUISHU_WALK_WINDOW || 120);


const THREE_DUISHU_MIN_HOLD = Number(process.env.THREE_DUISHU_MIN_HOLD || 3);

const THREE_DUISHU_SWITCH_MARGIN = Number(process.env.THREE_DUISHU_SWITCH_MARGIN || 4.5);


const SCORE_THREE_DUISHU_LRU_MAX = Number(process.env.SCORE_THREE_DUISHU_LRU_MAX || 48);






const THREE_DUISHU_BLOCK_FLOOR = Number(process.env.THREE_DUISHU_BLOCK_FLOOR || 55);


const THREE_DUISHU_BLOCK_SIZE = Number(process.env.THREE_DUISHU_BLOCK_SIZE || 20);

const THREE_DUISHU_BLOCK_WINDOW = Number(process.env.THREE_DUISHU_BLOCK_WINDOW || 120);







const THREE_DUISHU_PAIRS = [
  { key: "05", digits: ["0", "5"], codes: ["0", "5"] },
  { key: "16", digits: ["1", "6"], codes: ["1", "6"] },
  { key: "27", digits: ["2", "7"], codes: ["2", "7"] },
  { key: "38", digits: ["3", "8"], codes: ["3", "8"] },
  { key: "49", digits: ["4", "9"], codes: ["4", "9"] }
];


const scoreThreeDuishuCandidatesLru = new Map();
























const MODEL_MIN_SAMPLE = Number(process.env.MODEL_MIN_SAMPLE || 8);

const MODEL_RATE_WINDOW = Number(process.env.MODEL_RATE_WINDOW || 30);











const STABLE_MATH_SHAPE_CODE_MAP = {
  "全奇": "单单单单",
  "全偶": "双双双双",
  "全大": "大大大大",
  "全小": "小小小小",
  "全0路": "小小小小",
  "全1路": "单单单单",
  "全2路": "双双双双"
};

const STABLE_MATH_PARITY_SHAPE_LABELS = new Set(["全奇", "全偶", "全1路", "全2路"]);














const stableMathFilterBridge = require("../../stable_math_filter_bridge");

let state = null;









const HIGH_BASELINE_SCORE_META = {
  spanAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  repeatAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  consecutiveAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  sumRangeAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.0, hint: "极端区间过滤" },
  sumAvoid: { scoreClass: "structural-rare-avoid", baseline: 97.0, hint: "单点和值过滤" },
  parityRatioAvoid: { scoreClass: "structural-rare-avoid", baseline: 93.0, hint: "极端奇偶比过滤" },
  sizeRatioAvoid: { scoreClass: "structural-rare-avoid", baseline: 93.0, hint: "极端大小比过滤" },
  anyTwoHeweiHit: { scoreClass: "wide-hit-baseline", baseline: 99.0, hint: "宽命中参考" },
  anyThreeHeweiHit: { scoreClass: "wide-hit-baseline", baseline: 97.0, hint: "宽命中参考" },
  twoDigitHit: { scoreClass: "pair-avoid-baseline", baseline: 90.0, hint: "除对数（非共现命中）" }
};


const HEWEI_UNIFIED_GUARD_RATE = [];

const HEWEI_GUARD_RATE = [];

























































function bindRuntime(runtime) { _runtime = Object.assign({}, _runtime, runtime || {}); }

function getState() { const s = (typeof _runtime.getState === "function" ? _runtime.getState() : {}) || {}; state = s; return s; }

function anyTwoHeweiCandidates() { return extraFormula.anyTwoHeweiCandidates ? extraFormula.anyTwoHeweiCandidates.apply(null, arguments) : []; }

function digitSetCandidatesMixedParity() { return extraFormula.digitSetCandidatesMixedParity ? extraFormula.digitSetCandidatesMixedParity.apply(null, arguments) : []; }

function calculateMetrics(n){ return heweiFormula.calculateMetrics(n); }

function getCompositeHeweiGroup(r){ return heweiFormula.getCompositeHeweiGroup(r); }

function firstFourMaxConsecutiveRun(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return 0;
  const sorted = Array.from(new Set(nums)).sort((a, b) => a - b);
  let best = sorted.length ? 1 : 0;
  let current = sorted.length ? 1 : 0;
  for (let index = 1; index < sorted.length; index += 1) {
    if (sorted[index] === sorted[index - 1] + 1) current += 1;
    else current = 1;
    if (current > best) best = current;
  }
  return best;
}

function firstFourSizeRatio(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return "";
  const big = nums.filter((value) => value >= 5).length;
  return `${big}大${4 - big}小`;
}

function firstFourRepeatShape(record) {
  const nums = firstFourNumbers(record);
  if (nums.length < 4) return "";
  const counts = {};
  nums.forEach((digit) => { counts[String(digit)] = Number(counts[String(digit)] || 0) + 1; });
  const values = Object.keys(counts).map((key) => counts[key]).sort((a, b) => b - a);
  if (!values.length) return "";
  if (values[0] >= 4) return "四重";
  if (values[0] >= 3) return "三重";
  const pairCount = values.filter((count) => count === 2).length;
  if (pairCount >= 2) return "双重";
  if (pairCount === 1) return "一对";
  return "无重";
}

function firstFourParityRatio(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return "";
  const odd = nums.filter((value) => Math.abs(value) % 2 === 1).length;
  return `${odd}单${4 - odd}双`;
}

function firstFourHasRepeat(record) {
  const shape = firstFourRepeatShape(record);
  return !!shape && shape !== "无重";
}

function firstFourHasConsecutive(record) {
  return firstFourMaxConsecutiveRun(record) >= 2;
}

function firstFourConsecutiveShape(record) {
  const run = firstFourMaxConsecutiveRun(record);
  if (run >= 4) return "四连";
  if (run >= 3) return "三连";
  if (run >= 2) return "二连";
  return "无连";
}

function firstFourSpan(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return null;
  return Math.max(...nums) - Math.min(...nums);
}

function normalizeNumberRange(value) {
  const nums = String(value === undefined || value === null ? "" : value)
    .match(/\d+/g);
  if (!nums || nums.length < 2) return null;
  const a = Number(nums[0]);
  const b = Number(nums[1]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  const start = Math.max(0, Math.min(36, Math.min(a, b)));
  const end = Math.max(0, Math.min(36, Math.max(a, b)));
  return { start, end, code: `${start}-${end}` };
}

function brotherNumberResultFromCode(code) {
  const text = String(code || "").replace(/\D/g, "");
  if (text.length < 4) return null;
  for (let i = 0; i < BROTHER_NUMBER_PAIRS.length; i += 1) {
    const pair = BROTHER_NUMBER_PAIRS[i];
    if (text.indexOf(pair.charAt(0)) >= 0 && text.indexOf(pair.charAt(1)) >= 0) return "有";
  }
  return "否";
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

function firstFourSumInRange(record, value) {
  const range = normalizeNumberRange(value);
  if (!range) return false;
  const sum = firstFourSum(record);
  return sum >= range.start && sum <= range.end;
}

function invalidatePanelStateCache(options) {
  if (typeof _runtime.invalidatePanelStateCache === "function") {
    return _runtime.invalidatePanelStateCache(options);
  }
}

function numbersFromRecordByIndexes(record, indexes) {
  const nums = Array.isArray(record && record.numbers) ? record.numbers : [];
  const out = [];
  (Array.isArray(indexes) ? indexes : []).forEach((index) => {
    if (!Number.isInteger(index) || index < 0 || index >= nums.length) return;
    if (nums[index] === undefined || nums[index] === null) return;
    out.push(String(nums[index]));
  });
  return out;
}

function autoTop6IsThreeWithOneShape(code) {
  const text = String(code || "");
  if (!/^[大小单双]{4}$/.test(text)) return false;
  const counts = {};
  text.split("").forEach((ch) => { counts[ch] = Number(counts[ch] || 0) + 1; });
  const values = Object.keys(counts).map((key) => counts[key]).sort((a, b) => a - b);
  return values.length === 2 && values[0] === 1 && values[1] === 3;
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

function uniqueDigits(value) {
  const out = [];
  String(value === undefined || value === null ? "" : value).replace(/\D/g, "").split("").forEach((digit) => {
    if (digit !== "" && !out.includes(digit)) out.push(digit);
  });
  return out;
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

function isAnyTwoHeweiCode(value) {
  const code = String(value || "").replace(/\D/g, "");
  if (code.length !== ANY_TWO_HEWEI_CODE_SIZE) return false;
  if (new Set(code.split("")).size !== ANY_TWO_HEWEI_CODE_SIZE) return false;
  return code.split("").every((digit) => ANY_TWO_HEWEI_SOURCE_DIGITS.includes(digit));
}

function threeDigitCooccurs(record, candidate) {
  const actual = firstFourNumbers(record);
  return String(candidate).split("").every((digit) => actual.includes(String(digit)));
}

function firstFourCode(record) {
  const nums = firstFourNumbers(record);
  return nums.length >= 4 ? nums.slice(0, 4).map(String).join("") : "";
}

function highBaselineScoreMeta(key) {
  const clean = String(key || "").replace(/^extra\./, "");
  return HIGH_BASELINE_SCORE_META[clean] || null;
}

function isHighBaselineScoreKey(key) {
  return !!highBaselineScoreMeta(key);
}

function firstFourNumbers(record) {
  return (record && Array.isArray(record.numbers) ? record.numbers : []).slice(0, 4).map(String);
}

function firstFourSum(record) {
  return firstFourNumbers(record).reduce((sum, n) => sum + Number(n || 0), 0);
}

function finiteNumberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function autoTop6RateStatsFromSuccesses(successes) {
  const vals = (Array.isArray(successes) ? successes : []).filter((value) => typeof value === "boolean");
  let missStreak = 0;
  let currentRun = 0;
  let maxMissStreak = 0;
  for (let i = 0; i < vals.length; i += 1) {
    if (vals[i] === true) break;
    missStreak += 1;
  }
  vals.forEach((ok) => {
    if (ok) {
      currentRun = 0;
    } else {
      currentRun += 1;
      if (currentRun > maxMissStreak) maxMissStreak = currentRun;
    }
  });
  const hit = vals.filter(Boolean).length;
  return {
    total: vals.length,
    hit,
    rate: vals.length ? Number(((hit / vals.length) * 100).toFixed(2)) : null,
    missStreak,
    maxMissStreak
  };
}

function autoTop6CandidateItems(prediction) {
  const list = [];
  const seen = new Set();
  function add(item) {
    const code = cleanAutoTop6Code(item && item.code);
    if (!item || !code) return;
    const clean = Object.assign({}, item, { code });
    const id = [clean.kind, clean.key, code].join(":");
    if (seen.has(id)) return;
    seen.add(id);
    list.push(clean);
  }

  const positionKills = prediction && prediction.positionKills || {};
  AUTO_TOP6_POSITIONS.forEach((pos) => {
    const entry = positionKills[pos.key] || {};
    const recommend = entry.recommendDigit !== undefined ? entry.recommendDigit : (entry.selectedDigit !== undefined ? entry.selectedDigit : (entry.main !== undefined ? entry.main : (entry.killDigits && entry.killDigits[0])));
    const main = entry.primaryDigit !== undefined ? entry.primaryDigit : (entry.mainAlgorithmDigit !== undefined ? entry.mainAlgorithmDigit : (entry.mainDigit !== undefined ? entry.mainDigit : entry.main));
    const backup = entry.originalBackupDigit !== undefined ? entry.originalBackupDigit : (entry.backupAlgorithmDigit !== undefined ? entry.backupAlgorithmDigit : (entry.backup !== undefined ? entry.backup : (entry.backupDigit !== undefined ? entry.backupDigit : (entry.killDigits && entry.killDigits[1]))));
    add({ kind: "position", key: pos.key, side: "recommend", name: `${pos.label}推荐`, code: recommend });
    add({ kind: "position", key: pos.key, side: "main", name: `${pos.label}主攻`, code: main });
    add({ kind: "position", key: pos.key, side: "backup", name: `${pos.label}备用`, code: backup });
  });

  const independentHeweiKills = prediction && prediction.independentHeweiKills || {};
  AUTO_TOP6_HEWEI.forEach((hewei) => {
    const meta = resolveHeweiAlgorithmMeta(prediction, hewei.key);
    const recommend = meta.recommend;
    const main = meta.main;
    const backup = meta.backup;
    const shared = {
      algorithmStability: meta.stability || null,
      confidence: meta.confidence || null,
      recommendable: meta.recommendable,
      algorithmReason: meta.reason || "",
      algorithm: meta.algorithm || ""
    };
    add(Object.assign({ kind: "hewei", key: hewei.key, side: "recommend", name: `${hewei.label}推荐`, code: recommend }, shared));
    add(Object.assign({ kind: "hewei", key: hewei.key, side: "main", name: `${hewei.label}主攻`, code: main }, shared));
    add(Object.assign({ kind: "hewei", key: hewei.key, side: "backup", name: `${hewei.label}备用`, code: backup }, shared));
  });

  const extraKills = prediction && prediction.extraKills || {};
  AUTO_TOP6_EXTRAS.forEach((extra) => {
    const saved = extraKills[extra.key];
    const code = saved && typeof saved === "object" ? (saved.value !== undefined ? saved.value : saved.code) : saved;
    const extraItem = { kind: "extra", key: extra.key, side: "recommend", name: extra.label, code, mode: extra.mode, extraKind: extra.kind };
    add(extraItem);
  });
  return list;
}

function autoTop6DecorateItem(history, item, evaluatedCount) {
  const stats = autoTop6RateStatsMultiWindow(history, item, [30, 60, 120, MODEL_STABLE_WINDOW]);
  const s30 = stats[30];
  const s60 = stats[60];
  const s120 = stats[120];
  const s300 = stats[MODEL_STABLE_WINDOW];
  const r30 = s30.rate;
  let r60 = s60.rate;
  let r120 = s120.rate;
  let r300 = s300.rate;
  if (r60 === null) r60 = r30;
  if (r120 === null) r120 = r60;
  if (r300 === null) r300 = r120;
  const hard = stableWindowScore(s30, s60, s120, s300, s300, item && item.mode === "hit" ? 1.0 : 2.2);
  let score = hard.score;
  score -= Math.max(0, Number(s120.maxMissStreak || 0) - 3) * 0.28;
  score -= Math.max(0, Number(s300.maxMissStreak || 0) - 5) * 0.18;
  const safetyValues = [r30, r120].map(finiteNumberOrNull).filter((value) => value !== null);
  const safetyRate = safetyValues.length ? Math.min(...safetyValues) : null;
  const threshold = autoTop6SafetyThreshold(item);
  const longFloor = autoTop6LongFloor(item, threshold);
  const lowerFloor = autoTop6LowerFloor(item, threshold);
  const lower120 = s120.total ? Number(wilsonLowerRate(s120.hit, s120.total).toFixed(2)) : null;
  const stableReasons = [];
  if (s30.total >= 20 && r30 !== null && r30 < threshold) stableReasons.push(`30期${Number(r30).toFixed(2)}低于${threshold}`);
  if (s120.total >= 60 && r120 !== null && r120 < threshold) stableReasons.push(`120期${Number(r120).toFixed(2)}低于${threshold}`);
  if (s300.total >= 120 && r300 !== null && r300 < longFloor) stableReasons.push(`1000期${Number(r300).toFixed(2)}低于${longFloor}`);
  if (lower120 !== null && s120.total >= 80 && lower120 < lowerFloor) stableReasons.push(`120期下界${Number(lower120).toFixed(2)}低于${lowerFloor}`);
  if (s30.missStreak >= AUTO_TOP6_RECENT_MISS_LIMIT) stableReasons.push(`当前连错${s30.missStreak}手`);
  if (s120.total >= 80 && s120.maxMissStreak >= 5) stableReasons.push(`120期最大连错${s120.maxMissStreak}手`);
  if (s300.total >= 300 && s300.maxMissStreak >= 7) stableReasons.push(`1000期最大连错${s300.maxMissStreak}手`);

  // 合尾：优先对齐算法层 block stability / recommendable，避免「算法稳定、框架观察」分叉
  const algoStability = item && item.algorithmStability && typeof item.algorithmStability === "object"
    ? item.algorithmStability
    : null;
  if (item && item.kind === "hewei" && algoStability) {
    const block = algoStability.block || {};
    const floor = Number(block.floor || (item.key === "unified" ? HEWEI_UNIFIED_BLOCK_FLOOR : HEWEI_BLOCK_FLOOR));
    if (Number(block.weakBlocks || 0) > 0) stableReasons.push(`算法弱块${Number(block.weakBlocks)}个`);
    if (block.minRate != null && Number(block.minRate) < floor) {
      stableReasons.push(`算法块最低${Number(block.minRate).toFixed(2)}低于${floor}`);
    }
    if (algoStability.lower120 != null && Number(algoStability.lower120) < Number(algoStability.guardRate || threshold) - 13) {
      stableReasons.push(`算法下界${Number(algoStability.lower120).toFixed(2)}不足`);
    }
  }
  if (item && item.kind === "hewei" && item.recommendable === false) {
    stableReasons.push("算法标记低置信合尾");
  }

  const stableGate = stableReasons.length === 0;
  return Object.assign({}, item, {
    code: cleanAutoTop6Code(item.code),
    rate: Number(score.toFixed(4)),
    rate30: r30,
    rate60: r60,
    rate120: r120,
    rate300: r300,
    stableScore: Number(score.toFixed(4)),
    hardnessScore: Number(score.toFixed(4)),
    safetyRate,
    safetyThreshold: threshold,
    longFloor,
    lower120,
    recentMissStreak: s30.missStreak,
    maxMissStreak120: s120.maxMissStreak,
    maxMissStreak1000: s300.maxMissStreak,
    stabilityGate: stableGate,
    stabilityReason: stableReasons.join("；"),
    lowConfidence: !stableGate,
    stageLabel: evaluatedCount >= MODEL_STABLE_WINDOW ? "稳定期" : (evaluatedCount >= 120 ? "小修期" : "观察期")
  });
}

function autoTop6ItemId(item) {
  return [item && item.kind, item && item.key, item && item.side || "recommend", String(item && item.code)].join(":");
}

function autoTop6RecordSuccess(item, record) {
  const state = getState();
  if (!item || !record) return null;
  const code = cleanAutoTop6Code(item.code);
  if (!code) return null;
  if (item.kind === "position") {
    const pos = AUTO_TOP6_POSITIONS.find((entry) => entry.key === item.key);
    const nums = Array.isArray(record.numbers) ? record.numbers.map(String) : [];
    if (!pos || nums[pos.index] === undefined) return null;
    return String(nums[pos.index]) !== code;
  }
  if (item.kind === "hewei") {
    if (item.key === "total" || item.key === "totalHewei") {
      const nums = Array.isArray(record.numbers) ? record.numbers : [];
      if (nums.length < 4) return null;
      const actual = String(calculateMetrics(nums).totalHewei);
      if (!/^\d$/.test(actual)) return null;
      return actual !== code;
    }
    const group = getCompositeHeweiGroup(record).map(String);
    if (item.key === "unified") return group.length >= 3 ? !group.includes(code) : null;
    const meta = AUTO_TOP6_HEWEI.find((entry) => entry.key === item.key);
    if (!meta || group[meta.index] === undefined) return null;
    return String(group[meta.index]) !== code;
  }
  if (item.kind === "extra") {
    if (item.key === "sumAvoid") return String(firstFourSum(record)) !== code;
    if (item.key === "sumRangeAvoid") return !firstFourSumInRange(record, code);
    if (
      item.key === "spanAvoid" ||
      item.key === "repeatAvoid" ||
      item.key === "consecutiveAvoid" ||
      item.key === "parityRatioAvoid" ||
      item.key === "sizeRatioAvoid"
    ) return !extraStructureMatched(record, item.key, code);
    let matched = false;
    if (item.key === "anyTwoHeweiHit" || item.key === "anyThreeHeweiHit") {
      const size = item.key === "anyTwoHeweiHit" ? 2 : 3;
      const isValidCode = item.key === "anyTwoHeweiHit"
        ? isAnyTwoHeweiCode(code)
        : (/^\d{5}$/.test(code) && new Set(code.split("")).size === 5);
      if (!isValidCode) return null;
      const scope = (Array.isArray(item.heweiScopeIndexes) ? item.heweiScopeIndexes : [])
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value >= 0 && value <= 3);
      const uniqueScope = [...new Set(scope)].sort((left, right) => left - right);
      if (uniqueScope.length >= size) {
        matched = anyHeweiCombinationHitOnNumbers(
          numbersFromRecordByIndexes(record, uniqueScope),
          code,
          size,
          1
        );
      } else {
        matched = anyHeweiCombinationHit(record, code, size, 1);
      }
    } else matched = threeDigitCooccurs(record, code);
    return item.mode === "hit" ? matched : !matched;
  }
  if (item.kind === "derivedReverseShape") {
    const shapeKey = item.shapeKey || (item.key === "reverseSize" ? "shapeSize" : "shapeParity");
    const scopeLen = Number(item.shapeScopeLength || 0) === 3 ? 3 : 4;
    const codeText = String(code || "");
    if (scopeLen === 3) {
      if (!/^[大小单双]{3,4}$/.test(codeText)) return null;
    } else if (!/^[大小单双]{4}$/.test(codeText)) {
      return null;
    }
    const actualShape = autoTop6PatternOf(record, shapeKey);
    if (!actualShape) return null;
    if (scopeLen === 3) {
      return actualShape.slice(0, 3) !== codeText.slice(0, 3);
    }
    return actualShape !== codeText;
  }
  if (item.kind === "brotherNumber") {
    const actualCode = firstFourCode(record) || firstFourNumbers(record).join("");
    const actual = brotherNumberResultFromCode(actualCode);
    if (actual === null) return null;
    return actual === String(item.code);
  }
  if (item.kind === "shape") {
    const shapeKey = item.key || "shapeParity";
    if (!/^[大小单双]{4}$/.test(code)) return null;
    const avoidCode = autoTop6ForceThreeWithOne(code, shapeKey);
    const actualShape = autoTop6PatternOf(record, shapeKey);
    if (!actualShape) return null;
    return actualShape !== avoidCode;
  }
  return null;
}

function killFrameworkSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  // 正式项不含 side=backup；替补只进定位看板，不能冒充 2定位/2合尾
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const heweiCount = formal.filter((item) => item && item.kind === "hewei").length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape").length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const anyThreeCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot &&
    snapshot.version === KILL_FRAMEWORK_SNAPSHOT_VERSION &&
    formal.length === KILL_FRAMEWORK_ITEM_COUNT &&
    items.length === KILL_FRAMEWORK_ITEM_COUNT &&
    positionCount === 2 &&
    heweiCount === 2 &&
    reverseCount === 2 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1
  );
}

function buildKillFrameworkSnapshot(prediction, history, options) {
  const state = getState();
  // 杀码框架：2定位 + 2合尾 + 2反向形态 + 二数合02468排1码 + 三数合5码（无对数避开）
  const opts = options && typeof options === "object" ? options : {};
  const comboWindow = Math.max(30, Math.min(MODEL_STABLE_WINDOW, Number(opts.comboWindow || MODEL_STABLE_WINDOW)));
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(comboWindow, safeHistory.length));

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  function comboStats(items) {
    let total = 0;
    let hit = 0;
    let validChecks = 0;
    let successChecks = 0;
    comboRows.forEach((record) => {
      const results = (items || []).map((item) => autoTop6RecordSuccess(item, record)).filter((value) => typeof value === "boolean");
      if (results.length !== (items || []).length || !results.length) return;
      total += 1;
      const wins = results.filter(Boolean).length;
      validChecks += results.length;
      successChecks += wins;
      if (wins === results.length) hit += 1;
    });
    return {
      total,
      hit,
      rate: total ? hit * 100 / total : null,
      avgRate: validChecks ? successChecks * 100 / validChecks : null
    };
  }

  function comboPickScore(item) {
    const stats = comboStats(chosen.concat([item]));
    const full = stats.rate === null ? -1 : Number(stats.rate || 0);
    const avg = stats.avgRate === null ? 0 : Number(stats.avgRate || 0);
    return full * 10000 + avg * 100 + Number(item.stableScore || 0);
  }

  function pickBest(pool, maxPerSameModel, allowSameExcludeDigit) {
    const modelCount = {};
    chosen.forEach((item) => {
      const modelKey = `${item.kind}:${item.key}`;
      modelCount[modelKey] = Number(modelCount[modelKey] || 0) + 1;
    });
    let best = null;
    let bestScore = -Infinity;
    (pool || []).forEach((item) => {
      const id = autoTop6ItemId(item);
      const modelKey = `${item.kind}:${item.key}`;
      if (used.has(id)) return;
      if (Number(modelCount[modelKey] || 0) >= Number(maxPerSameModel || 1)) return;
      if (frameworkConflictsSameHeweiDigit(chosen, item)) return;
      if (!allowSameExcludeDigit && frameworkConflictsSameExcludeDigit(chosen, item)) return;
      const score = comboPickScore(item);
      if (score > bestScore || (score === bestScore && best && order(item, best) < 0)) {
        best = item;
        bestScore = score;
      }
    });
    if (!best) return false;
    used.add(autoTop6ItemId(best));
    chosen.push(best);
    return true;
  }

  function noteLow(pool, label) {
    (pool || []).forEach((item) => {
      if (item.lowConfidence) {
        const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
        const reason = item.stabilityReason ? `，原因 ${item.stabilityReason}` : "";
        repairReport.push(`${label}暂停低稳：${item.name} ${item.code}，安全率 ${rate}${reason}`);
      }
    });
  }

  const positionPool = candidates.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  const heweiPool = candidates.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  noteLow(positionPool, "杀码框架定位池");
  noteLow(heweiPool, "杀码框架合尾池");

  // 正式 2定位/2合尾：始终只从非 backup 池补齐；allowUnhealthy 仅影响是否允许观察项（已默认允许）
  void opts.allowUnhealthy;
  const positionHealthy = healthy.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  let before = chosen.length;
  while (chosen.length - before < 2 && pickBest(positionHealthy, 1, false)) {}
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionHealthy, 2, false)) {}
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, false)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
    }
    if (chosen.length - before >= 2) repairReport.push("杀码框架定位健康项不足，已用正式观察项补齐（不含替补）。");
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, true)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
      repairReport.push(`杀码框架定位同码相碰无替代，被迫保留：${chosen[chosen.length - 1].name} ${chosen[chosen.length - 1].code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`杀码框架定位正式项不足2个（不含替补），当前定位启用 ${chosen.length - before} 个。`);

  const heweiHealthy = healthy.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  before = chosen.length;
  while (chosen.length - before < 2 && pickBest(heweiHealthy, 1, false)) {}
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiHealthy, 2, false)) {}
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiPool, 2, false)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
    }
    if (chosen.length - before >= 2) repairReport.push("杀码框架合尾健康项不足，已用正式观察项补齐（不含替补）。");
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiPool, 2, true)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
      repairReport.push(`杀码框架合尾与定位同码相碰无替代，被迫保留：${chosen[chosen.length - 1].name} ${chosen[chosen.length - 1].code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`杀码框架合尾正式项不足2个（不含替补），当前合尾启用 ${chosen.length - before} 个。`);

  const reverseBase = chosen.filter((item) => item.kind === "position");
  // reverse 候选可能为 null（无定位锁）；缺项时必须空数组，避免 .map 抛错堵死事件循环
  const reversePool = (autoTop6ReverseShapeCandidatesFromPositions(reverseBase, safeHistory) || [])
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(order);
  noteLow(reversePool, "杀码框架反向形态池");
  const reverseHealthy = reversePool.filter((item) => item && item.stabilityGate === true);
  // 先吃健康项，不足 2 再用全池观察项补齐。
  // 旧逻辑 `healthy.length ? healthy : pool` 在只过闸 1 路时会永久缺第 2 项，导致 tipReady 卡死。
  function pushKillReverse(item, forceObserve) {
    if (!item || used.has(autoTop6ItemId(item))) return false;
    if (chosen.filter((row) => row && row.kind === "derivedReverseShape").length >= 2) return false;
    if (forceObserve || !item.stabilityGate) {
      item.repairedIntoTop = true;
      repairReport.push(`杀码框架反向形态未达安全阈值，暂用观察项：${item.name} ${item.code}。`);
    }
    used.add(autoTop6ItemId(item));
    chosen.push(item);
    return true;
  }
  reverseHealthy.slice().sort((a, b) => {
    const delta = comboPickScore(b) - comboPickScore(a);
    return delta || order(a, b);
  }).forEach((item) => pushKillReverse(item, false));
  if (chosen.filter((row) => row && row.kind === "derivedReverseShape").length < 2) {
    reversePool.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    }).forEach((item) => pushKillReverse(item, true));
  }
  if (chosen.filter((row) => row && row.kind === "derivedReverseShape").length < 2) {
    repairReport.push("杀码框架反向形态不足2个，等待定位项稳定后自动恢复。");
  }

  const killAnyTwo = pickStructuralAnyTwoHeweiHit(
    safeHistory,
    evaluatedCount,
    heweiScopeIndexesFromItems(chosen),
    chosen,
    prediction,
    new Set(),
    resolveStableMathAssist(prediction)
  );
  if (!killAnyTwo) {
    repairReport.push("杀码框架固定模型缺失：anyTwoHeweiHit");
  } else if (!used.has(autoTop6ItemId(killAnyTwo))) {
    repairReport.push(`杀码框架二数合结构对齐：${killAnyTwo.code}`);
    used.add(autoTop6ItemId(killAnyTwo));
    chosen.push(killAnyTwo);
  }

  const killFrameworkAnyThree = digitSetCandidatesMixedParity(5)
    .map((code) => autoTop6DecorateItem(safeHistory, {
      kind: "extra",
      key: "anyThreeHeweiHit",
      side: "recommend",
      name: "全码池三数合尾5码命中",
      code,
      mode: "hit",
      algorithm: "kill-framework-any-three-hewei-selected-five",
      hitMode: true,
      matchMode: "hit-if-any-three-hewei-tail-in-selected-five",
      dynamicEveryPrediction: true
    }, evaluatedCount))
    .sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    })[0] || null;
  if (killFrameworkAnyThree) {
    if (killFrameworkAnyThree.lowConfidence) {
      const rate = killFrameworkAnyThree.safetyRate === null ? "样本不足" : `${Number(killFrameworkAnyThree.safetyRate).toFixed(2)}%`;
      repairReport.push(`杀码框架三数合5码观察加入：${killFrameworkAnyThree.code}，安全率 ${rate}`);
      killFrameworkAnyThree.repairedIntoTop = true;
    }
    if (!used.has(autoTop6ItemId(killFrameworkAnyThree))) {
      used.add(autoTop6ItemId(killFrameworkAnyThree));
      chosen.push(killFrameworkAnyThree);
    }
  } else {
    repairReport.push("杀码框架固定模型缺失：anyThreeHeweiHit五码");
  }

  return {
    version: KILL_FRAMEWORK_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, KILL_FRAMEWORK_ITEM_COUNT),
    framework: {
      positionCount: 2,
      heweiCount: 2,
      reverseShapeCount: 2,
      heweiComboCount: 2,
      pairAvoidCount: 0
    },
    items: chosen.slice(0, KILL_FRAMEWORK_ITEM_COUNT).map((item, index) => ({
      rank: index + 1,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      mode: item.mode || "avoid",
      rate: item.rate,
      stableScore: item.stableScore,
      rate30: item.rate30,
      rate60: item.rate60,
      rate120: item.rate120,
      rate300: item.rate300,
      safetyRate: item.safetyRate,
      safetyThreshold: item.safetyThreshold,
      longFloor: item.longFloor,
      lower120: item.lower120,
      recentMissStreak: item.recentMissStreak,
      maxMissStreak120: item.maxMissStreak120,
      maxMissStreak1000: item.maxMissStreak1000,
      hardnessScore: item.hardnessScore,
      stabilityGate: !!item.stabilityGate,
      stabilityReason: item.stabilityReason || "",
      stageLabel: item.stageLabel,
      lowConfidence: !!item.lowConfidence,
      repairedIntoTop: !!item.repairedIntoTop,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || ""
    })),
    repairReport
  };
}

function ensurePredictionKillFrameworkSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (killFrameworkSnapshotIsComplete(prediction.killFramework)) return prediction.killFramework;
  prediction.killFramework = buildKillFrameworkSnapshot(prediction, history);
  prediction.killFrameworkItems = prediction.killFramework.items;
  prediction.killFrameworkRepairReport = prediction.killFramework.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.killFrameworkSnapshotVersion = prediction.killFramework.version;
  return prediction.killFramework;
}

function autoTop6ReverseShapeCandidatesFromPositions(positionItems, history) {
  const locks = {
    shapeParity: [null, null, null, null],
    shapeSize: [null, null, null, null]
  };
  let changed = false;
  (Array.isArray(positionItems) ? positionItems : []).forEach((item) => {
    const pos = AUTO_TOP6_POSITIONS.find((entry) => entry.key === item.key);
    const digitText = cleanAutoTop6Code(item.code).replace(/\D/g, "").charAt(0);
    const digit = Number(digitText);
    if (!pos || !Number.isFinite(digit)) return;
    locks.shapeParity[pos.index] = digit % 2 ? "双" : "单";
    locks.shapeSize[pos.index] = digit >= 5 ? "小" : "大";
    changed = true;
  });
  if (!changed) return [];
  const rows = (Array.isArray(history) ? history : []).filter((record) => record && Array.isArray(record.numbers)).slice(0, 120);
  function complete(shapeKey, fallback) {
    const pool = autoTop6ShapePool(shapeKey);
    const locked = locks[shapeKey];
    const matches = pool.filter((candidate) => {
      for (let index = 0; index < 4; index += 1) {
        if (locked[index] && candidate[index] !== locked[index]) return false;
      }
      return true;
    });
    if (matches.length) {
      function recentSeenScore(candidate) {
        let score = 0;
        rows.forEach((record, index) => {
          if (autoTop6PatternOf(record, shapeKey) === candidate) score += Math.pow(0.94, index);
        });
        return score;
      }
      return matches.slice().sort((a, b) => {
        const sa = recentSeenScore(a);
        const sb = recentSeenScore(b);
        if (sa !== sb) return sa - sb;
        return a.localeCompare(b, "zh-CN");
      })[0] || fallback;
    }
    const out = String(fallback || "").split("");
    for (let index = 0; index < 4; index += 1) {
      if (locked[index]) out[index] = locked[index];
      else if (!/^[大小单双]$/.test(out[index] || "")) out[index] = String(fallback || "单单单双").charAt(index) || "单";
    }
    return autoTop6ForceThreeWithOne(out.join(""), shapeKey);
  }
  const parityCode = autoTop6ForceThreeWithOne(complete("shapeParity", "双双双单"), "shapeParity");
  const sizeCode = autoTop6ForceThreeWithOne(complete("shapeSize", "大大大小"), "shapeSize");
  const out = [];
  if (/^[大小单双]{4}$/.test(parityCode)) {
    out.push({
      kind: "derivedReverseShape",
      key: "reverseParity",
      side: "recommend",
      name: "单双反向形态",
      code: parityCode,
      shapeKey: "shapeParity",
      mode: "avoid",
      derivedFrom: "position-reverse"
    });
  }
  if (/^[大小单双]{4}$/.test(sizeCode)) {
    out.push({
      kind: "derivedReverseShape",
      key: "reverseSize",
      side: "recommend",
      name: "大小反向形态",
      code: sizeCode,
      shapeKey: "shapeSize",
      mode: "avoid",
      derivedFrom: "position-reverse"
    });
  }
  return out;
}

function frameworkConflictsSameHeweiDigit(chosen, item) {
  const token = frameworkHeweiDigitToken(item);
  if (!token) return false;
  return (Array.isArray(chosen) ? chosen : []).some((picked) => frameworkHeweiDigitToken(picked) === token);
}

function frameworkConflictsSameExcludeDigit(chosen, item) {
  const token = frameworkSingleExcludeDigitToken(item);
  if (!token) return false;
  return (Array.isArray(chosen) ? chosen : []).some((picked) => frameworkSingleExcludeDigitToken(picked) === token);
}

function frameworkHeweiDigitToken(item) {
  if (!item || item.kind !== "hewei") return "";
  if ((item.side || "recommend") === "backup") return "";
  const digits = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  return digits.length === 1 ? digits : "";
}

function frameworkSingleExcludeDigitToken(item) {
  if (!item) return "";
  if ((item.side || "recommend") === "backup" && item.kind === "position") return "";
  if (item.kind !== "position" && item.kind !== "hewei") return "";
  const digits = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  return digits.length === 1 ? digits : "";
}

function frameworkSingleExcludeDigits(items) {
  const out = new Set();
  (Array.isArray(items) ? items : []).forEach((item) => {
    const token = frameworkSingleExcludeDigitToken(item);
    if (token) out.add(token);
  });
  return out;
}

function pickStructuralAnyTwoHeweiHit(history, evaluatedCount, scopeIndexes, chosenItems, prediction, avoidCodes, assist) {
  const state = getState();
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const avoid = avoidCodes instanceof Set ? avoidCodes : new Set(Array.isArray(avoidCodes) ? avoidCodes : []);
  const alignment = structuralAnyTwoAlignment(chosenItems, prediction, assist || resolveStableMathAssist(prediction));
  const codes = anyTwoHeweiCandidates();
  let bestCode = null;
  let bestScore = -Infinity;
  codes.forEach((code) => {
    if (avoid.has(code)) return;
    const score = scoreStructuralAnyTwoCode(code, alignment);
    if (score > bestScore || (score === bestScore && bestCode && code.localeCompare(bestCode) < 0)) {
      bestScore = score;
      bestCode = code;
    }
  });
  if (!bestCode) bestCode = codes.find((code) => !avoid.has(code)) || anyTwoHeweiCandidates()[0];
  let item = {
    kind: "extra",
    key: "anyTwoHeweiHit",
    side: "recommend",
    name: "二数合尾02468排1码命中",
    code: bestCode,
    mode: "hit",
    derivedFrom: "structural-align-position-hewei",
    structuralPick: true,
    algorithm: "structural-any-two-hewei-02468-exclude-one",
    hitMode: true,
    matchMode: "hit-if-any-two-hewei-tail-in-02468-exclude-one",
    dynamicEveryPrediction: true
  };
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  item = autoTop6DecorateItem(safeHistory, item, evaluatedCount);
  item.structuralPick = true;
  item.derivedFrom = "structural-align-position-hewei";
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  return item;
}

function structuralAnyTwoAlignment(chosenItems, prediction, assist) {
  const kills = new Set(frameworkSingleExcludeDigits(chosenItems));
  const keep = new Set(DIGIT_POOL.filter((digit) => !kills.has(digit)));
  const prefer = new Set();
  const pk = prediction && prediction.positionKills ? prediction.positionKills : {};
  ["wan", "qian", "bai", "shi"].forEach((key) => {
    const row = pk[key] || {};
    if (row.codexStableRecommended && /^\d$/.test(String(row.codexStableDigit || ""))) {
      const digit = String(row.codexStableDigit);
      if (!kills.has(digit)) prefer.add(digit);
    }
    if (/^\d$/.test(String(row.backup || ""))) {
      const digit = String(row.backup);
      if (!kills.has(digit)) prefer.add(digit);
    }
    if (/^\d$/.test(String(row.main || ""))) kills.add(String(row.main));
    (Array.isArray(row.killDigits) ? row.killDigits : []).forEach((digit) => {
      if (/^\d$/.test(String(digit))) kills.add(String(digit));
    });
  });
  if (assist && assist.ok) {
    (assist.excludeDigits || []).forEach((digit) => {
      if (/^\d$/.test(String(digit))) kills.add(String(digit));
    });
  }
  DIGIT_POOL.forEach((digit) => {
    if (!kills.has(digit)) keep.add(digit);
  });
  return { keep, prefer, kills };
}

function scoreStructuralAnyTwoCode(code, alignment) {
  const digits = String(code || "").split("");
  const { keep, prefer, kills } = alignment;
  let score = 0;
  // 软筛选：命中更稳排除单码按风险扣分，不再一次性 -100 硬杀
  digits.forEach((digit) => {
    if (keep.has(digit)) score += 14;
    else if (kills.has(digit)) score -= 28;
    if (prefer.has(digit)) score += 30;
  });
  score += digits.filter((digit) => keep.has(digit)).length * 3;
  score -= digits.filter((digit) => kills.has(digit)).length * 12;
  if (typeof stableMathFilterBridge.scoreCombination === "function") {
    const risk = stableMathFilterBridge.scoreCombination(String(code || "").slice(0, 4));
    if (risk && risk.riskScore != null) {
      score -= Number(risk.riskScore) * 18;
      if (risk.riskLevel === "high") score -= 25;
      else if (risk.riskLevel === "mid") score -= 8;
    }
  }
  return score;
}

function resolveStableMathAssist(prediction) {
  const raw = (prediction && prediction.stableMathFilter)
    || (prediction && prediction.detail && prediction.detail.stableMathFilter)
    || (state && state.stableMathFilter)
    || stableMathFilterBridge.slimStableMathFilter(stableMathFilterBridge.readCachedFilter())
    || null;
  if (!raw || typeof raw !== "object") {
    return {
      ok: false,
      excludeDigits: [],
      excludeShapes: [],
      excludeSpans: [],
      excludeSumRanges: [],
      excludeRepeats: [],
      excludeConsecutives: [],
      excludeParityRatios: [],
      excludeSizeRatios: [],
      summary: null,
      source: "missing"
    };
  }
  const digits = (Array.isArray(raw.exclude_digits) ? raw.exclude_digits : [])
    .map((value) => String(value))
    .filter((value) => /^\d$/.test(value));
  const shapes = Array.isArray(raw.exclude_shapes) ? raw.exclude_shapes.map(String) : [];
  const spans = Array.isArray(raw.exclude_spans) ? raw.exclude_spans.map(Number).filter((n) => Number.isFinite(n)) : [];
  const sums = Array.isArray(raw.exclude_sum_ranges) ? raw.exclude_sum_ranges.map(String) : [];
  const fw = raw.framework && typeof raw.framework === "object" ? raw.framework : {};
  const repeats = Array.isArray(raw.exclude_repeats)
    ? raw.exclude_repeats.map(String)
    : (Array.isArray(fw.repeatAvoid) ? fw.repeatAvoid.map(String) : []);
  const consecutives = Array.isArray(raw.exclude_consecutives)
    ? raw.exclude_consecutives.map(String)
    : (Array.isArray(fw.consecutiveAvoid) ? fw.consecutiveAvoid.map(String) : []);
  const parityRatios = Array.isArray(raw.exclude_parity_ratios)
    ? raw.exclude_parity_ratios.map(String)
    : (Array.isArray(fw.parityRatioAvoid) ? fw.parityRatioAvoid.map(String) : []);
  const sizeRatios = Array.isArray(raw.exclude_size_ratios)
    ? raw.exclude_size_ratios.map(String)
    : (Array.isArray(fw.sizeRatioAvoid) ? fw.sizeRatioAvoid.map(String) : []);
  return {
    ok: true,
    excludeDigits: digits,
    excludeShapes: shapes,
    excludeSpans: spans,
    excludeSumRanges: sums,
    excludeRepeats: repeats,
    excludeConsecutives: consecutives,
    excludeParityRatios: parityRatios,
    excludeSizeRatios: sizeRatios,
    summary: raw.summary || null,
    framework: raw.framework || null,
    latestPeriod: raw.latest_period || null,
    version: raw.version || "stable-math-filter",
    source: prediction && prediction.stableMathFilter ? "prediction" : "cache",
    softRisk: raw.softRisk && typeof raw.softRisk === "object"
      ? raw.softRisk
      : (stableMathFilterBridge.softRiskMeta ? stableMathFilterBridge.softRiskMeta() : { mode: "soft_risk" })
  };
}

function heweiScopeIndexesFromItems(items) {
  // 与 AUTO_TOP6_POSITIONS 对齐：wan/qian/bai/shi -> 千百十个
  const keyToIndex = { wan: 0, qian: 1, bai: 2, shi: 3 };
  const indexes = [];
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || item.kind !== "position") return;
    const index = keyToIndex[String(item.key || "")];
    if (!Number.isInteger(index) || index < 0 || index > 3) return;
    if (!indexes.includes(index)) indexes.push(index);
  });
  return indexes.sort((left, right) => left - right);
}

function resolveHeweiAlgorithmMeta(prediction, heweiKey) {
  const detail = prediction && prediction.detail && typeof prediction.detail === "object" ? prediction.detail : {};
  if (heweiKey === "unified") {
    const src = detail.threeFourHeweiKill || {};
    return {
      recommend: prediction && prediction.threeFourHeweiKillDigit || src.killDigit || "",
      main: src.primaryDigit || prediction && prediction.threeFourHeweiKillDigit || src.killDigit || "",
      backup: prediction && prediction.threeFourHeweiBackupDigit || src.backupDigit || "",
      stability: src.stability || null,
      confidence: src.confidence || null,
      recommendable: src.recommendable,
      reason: src.reason || "",
      algorithm: src.algorithm || ""
    };
  }
  if (heweiKey === "total" || heweiKey === "totalHewei") {
    const src = detail.totalHeweiKill || {};
    return {
      recommend: prediction && prediction.totalHeweiKill || src.killHewei || "",
      main: src.primaryDigit || prediction && prediction.totalHeweiKill || src.killHewei || "",
      backup: prediction && prediction.totalHeweiBackupDigit || src.backupDigit || "",
      stability: src.stability || null,
      confidence: src.confidence || null,
      recommendable: src.recommendable,
      reason: src.reason || "",
      algorithm: src.algorithm || ""
    };
  }
  const item = ((prediction && prediction.independentHeweiKills) || {})[heweiKey] || {};
  const detailItem = ((detail.independentHeweiKills) || {})[heweiKey] || item;
  return {
    recommend: item.killDigit || detailItem.killDigit || "",
    main: item.primaryDigit || detailItem.primaryDigit || item.killDigit || "",
    backup: item.backupDigit || detailItem.backupDigit || "",
    stability: detailItem.stability || item.stability || null,
    confidence: detailItem.confidence || item.confidence || null,
    recommendable: detailItem.recommendable !== undefined ? detailItem.recommendable : item.recommendable,
    reason: detailItem.reason || item.reason || "",
    algorithm: detailItem.algorithm || item.algorithm || ""
  };
}

function autoTop6ShapePool(shapeKey) {
  const pool = shapeKey === "shapeSize" ? ["大", "小"] : ["单", "双"];
  const out = [];
  pool.forEach((a) => {
    pool.forEach((b) => {
      pool.forEach((c) => {
        pool.forEach((d) => {
          const code = `${a}${b}${c}${d}`;
          if (autoTop6IsThreeWithOneShape(code)) out.push(code);
        });
      });
    });
  });
  return out;
}

function autoTop6ForceThreeWithOne(code, shapeKey) {
  const text = String(code || "");
  if (autoTop6IsThreeWithOneShape(text)) return text;
  if (!/^[大小单双]{4}$/.test(text)) return text;
  const pool = autoTop6ShapePool(shapeKey);
  function distance(candidate) {
    let diff = 0;
    for (let index = 0; index < 4; index += 1) if (candidate[index] !== text[index]) diff += 1;
    return diff;
  }
  return pool.slice().sort((a, b) => {
    const da = distance(a);
    const db = distance(b);
    if (da !== db) return da - db;
    return a.localeCompare(b, "zh-CN");
  })[0] || text;
}

function autoTop6PatternOf(record, shapeKey) {
  const nums = Array.isArray(record && record.numbers) ? record.numbers.slice(0, 4).map(Number) : [];
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return "";
  if (shapeKey === "shapeParity") return nums.map((value) => value % 2 ? "单" : "双").join("");
  if (shapeKey === "shapeSize") return nums.map((value) => value >= 5 ? "大" : "小").join("");
  return "";
}

function autoTop6RateStatsMultiWindow(history, item, limits) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const maxLimit = Math.max(...lims);
  const rows = (Array.isArray(history) ? history : []).slice(0, maxLimit);
  const successes = rows.map((record) => autoTop6RecordSuccess(item, record));
  const out = {};
  lims.forEach((limit) => {
    out[limit] = autoTop6RateStatsFromSuccesses(successes.slice(0, limit));
  });
  return out;
}

function autoTop6SafetyThreshold(item) {
  const key = String(item && item.key || "");
  if (item && item.kind === "hewei" && key === "unified") return HEWEI_UNIFIED_GUARD_RATE;
  if (item && item.kind === "hewei") return HEWEI_GUARD_RATE;
  // 高基线模型不用 85% 原始通过率冒充「健康/强预测」
  if (item && item.kind === "extra" && isHighBaselineScoreKey(key)) {
    const meta = highBaselineScoreMeta(key);
    return Math.max(0, Number(meta && meta.baseline || 0) - 3);
  }
  return MODEL_LOW_HIT_RATE;
}

function autoTop6LongFloor(item, threshold) {
  if (item && item.kind === "hewei" && item.key === "unified") return Math.max(55, Number(threshold || 0) - 6);
  return Math.max(70, Number(threshold || 0) - AUTO_TOP6_LONG_FLOOR_OFFSET);
}

function autoTop6LowerFloor(item, threshold) {
  if (item && item.kind === "hewei" && item.key === "unified") return Math.max(50, Number(threshold || 0) - 14);
  return Math.max(68, Number(threshold || 0) - AUTO_TOP6_LOWER_FLOOR_OFFSET);
}

function cleanAutoTop6Code(value) {
  const text = String(value === undefined || value === null ? "" : value).trim();
  if (!text || text === "--" || text === "undefined" || text === "null") return "";
  return text;
}

function isoNow() {
  return new Date().toISOString();
}

module.exports = {
  bindRuntime,
  buildKillFrameworkSnapshot,
  ensurePredictionKillFrameworkSnapshot
};
