"use strict";
/** Shared L1 framework item pipeline + autoTop6/killFramework builders (extracted). */
const kit = require("./formula_kit");
const { DIGIT_POOL, MODEL_LONG_WINDOW, MODEL_STABLE_WINDOW, MODEL_LOW_HIT_RATE, getModelStat, modelPerformanceSummary, stableWindowScore, wilsonLowerRate, rateVariance } = kit;
const extraFormula = require("../extra-kills/formula");
const heweiFormula = require("../hewei/formula");

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
  { key: "recentExactCodeAvoid300", label: "近300重复四码避开", mode: "avoid", kind: "recentExactCode" },
  { key: "recentExactCodeAvoid500", label: "近500重复四码避开", mode: "avoid", kind: "recentExactCode" },
  { key: "recentExactCodeAvoid800", label: "近800重复四码避开", mode: "avoid", kind: "recentExactCode" },
  { key: "recentExactCodeAvoid1000", label: "近1000重复四码避开", mode: "avoid", kind: "recentExactCode" },
  { key: "recentExactCodeAvoidMax", label: "最大旧码回删", mode: "avoid", kind: "recentExactCode" },
  { key: "threeDigitAvoid", label: "对数共现避开", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid", label: "对数避开1组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid2", label: "对数避开2组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid3", label: "对数避开3组", mode: "avoid", kind: "digits" },
  { key: "twoDigitHit", label: "两数共现除对数（基线参考）", mode: "avoid", kind: "digits", scoreClass: "pair-avoid-baseline" },
  { key: "threeDigitHit", label: "三数共现命中", mode: "hit", kind: "digits" },
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
const SIDE_STABLE_SNAPSHOT_VERSION = "backend-side-stable-snapshot-v5-1pos-2hewei-2digit-sum-anytwo4-pool02468-three5-old1300-label-v2";
const SIDE_STABLE_ITEM_COUNT = 8;
const SIDE_STABLE_TARGET_MIN = 4900;
const SIDE_STABLE_TARGET_MAX = 5000;
const BROTHER_NUMBER_PAIRS = ["01", "12", "23", "34", "45", "56", "67", "78", "89", "09"];
const COLD_FRAMEWORK_SNAPSHOT_VERSION = "backend-cold-framework-snapshot-v6-anytwo4-pool02468-label-v2";
const COLD_FRAMEWORK_ITEM_COUNT = 8;
const COLD_FRAMEWORK_COLD_WINDOW = Number(process.env.COLD_FRAMEWORK_COLD_WINDOW || 60);
const COLD_FRAMEWORK_TARGET_GROUP_COUNT = 5000;
const DING_KILL_SNAPSHOT_VERSION = "backend-ding-kill-snapshot-v4-anytwo4-pool02468-label-v2";
const DING_KILL_ITEM_COUNT = 7;
const DING_KILL_TARGET_MIN = 5000;
const DING_KILL_TARGET_MAX = 5010;
const PICK5000_SNAPSHOT_VERSION = "backend-pick5000-snapshot-v4-anytwo4-pool02468-stagger-label-v2";
const PICK5000_ITEM_COUNT = 8;
const PICK5000_TARGET_GROUP_COUNT = 5000;

let _runtime = { getState: () => ({}) };
function bindRuntime(runtime) { _runtime = Object.assign({}, _runtime, runtime || {}); }
function getState() { const s = (typeof _runtime.getState === "function" ? _runtime.getState() : {}) || {}; state = s; return s; }
function anyTwoHeweiCandidates() { return extraFormula.anyTwoHeweiCandidates ? extraFormula.anyTwoHeweiCandidates.apply(null, arguments) : []; }
function digitSetCandidatesMixedParity() { return extraFormula.digitSetCandidatesMixedParity ? extraFormula.digitSetCandidatesMixedParity.apply(null, arguments) : []; }
function calculateMetrics(n){ return heweiFormula.calculateMetrics(n); }
function getCompositeHeweiGroup(r){ return heweiFormula.getCompositeHeweiGroup(r); }
function getStableMathExcludeDigitSet(){ return heweiFormula.getStableMathExcludeDigitSet(); }

const THREE_DUISHU_SNAPSHOT_VERSION = "backend-three-duishu-snapshot-v1";

function invalidatePanelStateCache() {}

function publicThreeDuishuCandidate(candidate) {
  if (!candidate) return null;
  const out = Object.assign({}, candidate);
  delete out._pairs;
  return out;
}

function threeDuishuQualityLabel(candidate, walkForward) {
  if (!candidate) return "无数据";
  const walkRate = Number(walkForward && walkForward.rate || 0);
  const blockMin = Number(candidate.block && candidate.block.minRate || 0);
  const lower120 = Number(candidate.lower120 || 0);
  const weakBlocks = Number(candidate.block && candidate.block.weakBlocks || 0);
  if (walkRate >= 70 && blockMin >= 60 && lower120 >= 58 && weakBlocks === 0) return "稳固";
  if (walkRate >= 62 && blockMin >= THREE_DUISHU_BLOCK_FLOOR && lower120 >= 52) return "可用";
  return "观察";
}

function threeDuishuRecordHits(record, pairs) {
  const recordMask = typeof record === "number" ? record : firstFourDigitMask(record);
  if (!recordMask) return false;
  return resolveThreeDuishuPairs(pairs).some((pair) => {
    const pairMask = threeDuishuPairMask(pair);
    return pairMask > 0 && (recordMask & pairMask) === pairMask;
  });
}

const THREE_DUISHU_MIN_TRAIN = Number(process.env.THREE_DUISHU_MIN_TRAIN || 80);

const THREE_DUISHU_WALK_WINDOW = Number(process.env.THREE_DUISHU_WALK_WINDOW || 120);

function walkForwardThreeDuishu(history) {
  const rows = (Array.isArray(history) ? history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const maxTests = Math.max(0, Math.min(THREE_DUISHU_WALK_WINDOW, rows.length - THREE_DUISHU_MIN_TRAIN));
  let hit = 0;
  let fail = 0;
  let streak = 0;
  const recent = [];
  for (let index = 0; index < maxTests; index += 1) {
    // 只用当期开奖前的更早数据训练，禁止偷看当期/未来
    const train = rows.slice(index + 1);
    if (train.length < THREE_DUISHU_MIN_TRAIN) continue;
    const selected = scoreThreeDuishuCandidates(train)[0];
    if (!selected) continue;
    const success = !threeDuishuRecordHits(rows[index], selected._pairs);
    if (success) {
      hit += 1;
    } else {
      fail += 1;
    }
    recent.push({
      period: rows[index].period || "",
      uid: rows[index].uid || "",
      code: selected.code,
      pairs: selected.pairs,
      shot: selected.shot,
      success
    });
  }
  for (const item of recent) {
    if (!item.success) break;
    streak += 1;
  }
  const total = hit + fail;
  const insufficient = total < MODEL_MIN_SAMPLE;
  // walk-forward 会按不同 train 长度灌满 LRU；算完立刻清掉，避免跨请求残留大候选数组
  try { scoreThreeDuishuCandidatesLru.clear(); } catch (_) {}
  return {
    total,
    hit,
    fail,
    rate: insufficient ? null : (total ? Number(((hit / total) * 100).toFixed(2)) : null),
    rateText: insufficient ? "数据不足" : (total ? `${Number(((hit / total) * 100).toFixed(2))}%` : "暂无命中记录"),
    lower: wilsonLowerPercent(hit, total),
    streak,
    minTrain: THREE_DUISHU_MIN_TRAIN,
    window: THREE_DUISHU_WALK_WINDOW,
    recent: recent.slice(0, 40),
    sampleInsufficient: insufficient
  };
}

const THREE_DUISHU_MIN_HOLD = Number(process.env.THREE_DUISHU_MIN_HOLD || 3);

const THREE_DUISHU_SWITCH_MARGIN = Number(process.env.THREE_DUISHU_SWITCH_MARGIN || 4.5);

function selectStableThreeDuishuCandidate(candidates, period, options) {
  const opts = options && typeof options === "object" ? options : {};
  const applyLock = opts.applyLock !== false;
  const list = (Array.isArray(candidates) ? candidates : []).filter(Boolean);
  const primary = list[0] || null;
  if (!primary) {
    return { selected: null, primary: null, locked: false, reason: "empty", holdCount: 0 };
  }
  const keyOf = (candidate) => String(candidate && (candidate.pairKey || candidate.value || candidate.code) || "");
  const scoreOf = (candidate) => {
    const score = Number(candidate && candidate.score);
    return Number.isFinite(score) ? score : 0;
  };
  if (!state.threeDuishuSelectionLock || typeof state.threeDuishuSelectionLock !== "object") {
    state.threeDuishuSelectionLock = {};
  }
  const lock = applyLock ? state.threeDuishuSelectionLock : Object.assign({}, state.threeDuishuSelectionLock);
  const primaryKey = keyOf(primary);
  const oldKey = String(lock.pairKey || "");
  const old = oldKey ? list.find((candidate) => keyOf(candidate) === oldKey) : null;
  const oldHold = Number(lock.holdCount || 0);
  const samePeriod = String(lock.period || "") === String(period || "");
  let selected = primary;
  let reason = oldKey ? "primary-clearly-better" : "primary-initial";

  if (old && oldKey !== primaryKey) {
    const primaryScore = scoreOf(primary);
    const oldScore = scoreOf(old);
    const scoreMargin = primaryScore - oldScore;
    const primaryBlockMin = Number(primary.block && primary.block.minRate || 0);
    const oldBlockMin = Number(old.block && old.block.minRate || 0);
    const primaryLower120 = Number(primary.lower120 || 0);
    const oldLower120 = Number(old.lower120 || 0);
    const primaryWeakBlocks = Number(primary.block && primary.block.weakBlocks || 0);
    const oldWeakBlocks = Number(old.block && old.block.weakBlocks || 0);
    const blockImproved = primaryBlockMin - oldBlockMin >= THREE_DUISHU_SWITCH_MARGIN && primaryWeakBlocks <= oldWeakBlocks;
    const lowerImproved = primaryLower120 - oldLower120 >= THREE_DUISHU_SWITCH_MARGIN;
    const forceSwitch = scoreMargin >= THREE_DUISHU_SWITCH_MARGIN || (blockImproved && lowerImproved);
    if (oldHold < THREE_DUISHU_MIN_HOLD && scoreMargin < THREE_DUISHU_SWITCH_MARGIN * 1.6) {
      selected = old;
      reason = "min-hold-keep-current";
    } else if (!forceSwitch) {
      selected = old;
      reason = "stable-lock-keep-current";
    }
  } else if (old && oldKey === primaryKey) {
    selected = old;
    reason = "stable-current-primary";
  }

  const selectedKey = keyOf(selected);
  const holdCount = oldKey && selectedKey === oldKey ? (samePeriod ? Math.max(1, oldHold) : oldHold + 1) : 1;
  const nextLock = {
    pairKey: selectedKey,
    code: selected.code,
    period: String(period || ""),
    holdCount,
    reason,
    selectedScore: scoreOf(selected),
    primaryKey,
    primaryCode: primary.code,
    primaryScore: scoreOf(primary),
    switchMargin: THREE_DUISHU_SWITCH_MARGIN,
    minHold: THREE_DUISHU_MIN_HOLD,
    updatedAt: isoNow()
  };
  if (applyLock) state.threeDuishuSelectionLock = nextLock;
  return {
    selected,
    primary,
    locked: selectedKey !== primaryKey,
    reason,
    holdCount
  };
}

const SCORE_THREE_DUISHU_LRU_MAX = Number(process.env.SCORE_THREE_DUISHU_LRU_MAX || 48);

function resolveThreeDuishuPairs(pairsOrKeys) {
  return (Array.isArray(pairsOrKeys) ? pairsOrKeys : []).map((item) => {
    if (item && Array.isArray(item.digits)) return item;
    const key = String(item && item.key || item || "").replace(/[^\d]/g, "").slice(0, 2);
    if (!/^\d{2}$/.test(key)) return null;
    return THREE_DUISHU_PAIRS.find((pair) => pair.key === key) || {
      key,
      digits: [key[0], key[1]],
      codes: [key[0], key[1]]
    };
  }).filter(Boolean);
}

function threeDuishuShotText(pairs) {
  const keys = resolveThreeDuishuPairs(pairs).map((pair) => pair.key);
  return keys.map((key) => `除对数${key}`).join("");
}

function chooseThreeDuishuDisplayCode(records, pair) {
  const codes = Array.isArray(pair && pair.codes) ? pair.codes.map(String) : [];
  if (codes.length >= 2) return codes[1];
  return codes[0] || "";
}

function wilsonLowerPercent(hit, total, z) {
  if (!total) return 0;
  const confidence = Number(z || 1.281551565545);
  const p = hit / total;
  const z2 = confidence * confidence;
  const denom = 1 + z2 / total;
  const center = p + z2 / (2 * total);
  const margin = confidence * Math.sqrt((p * (1 - p) + z2 / (4 * total)) / total);
  return Number((Math.max(0, (center - margin) / denom) * 100).toFixed(2));
}

function threeDuishuPairHealth(records, pairs, limit) {
  const rows = (Array.isArray(records) ? records : [])
    .filter((record) => firstFourNumbers(record).length >= 4)
    .slice(0, Math.max(1, Number(limit || 120)));
  const details = (Array.isArray(pairs) ? pairs : []).map((pair) => {
    const result = threeDuishuComboRate(rows, [pair], rows.length || 1);
    return {
      pair: pair.key,
      rate: result.rate,
      lower: wilsonLowerPercent(result.hit, result.total),
      fail: result.fail,
      total: result.total
    };
  });
  if (!details.length) {
    return { limit: rows.length, minRate: null, avgRate: null, minLower: null, details: [] };
  }
  const rates = details.map((item) => Number(item.rate || 0));
  const lowers = details.map((item) => Number(item.lower || 0));
  return {
    limit: rows.length,
    minRate: Number(Math.min(...rates).toFixed(2)),
    avgRate: Number((rates.reduce((sum, rate) => sum + rate, 0) / rates.length).toFixed(2)),
    minLower: Number(Math.min(...lowers).toFixed(2)),
    details
  };
}

const THREE_DUISHU_BLOCK_FLOOR = Number(process.env.THREE_DUISHU_BLOCK_FLOOR || 55);

function threeDuishuComboRate(records, pairs, limit) {
  const masks = preprocessThreeDuishuHistoryMasks(records, limit);
  if (!masks.length) return { total: 0, hit: 0, fail: 0, rate: null, streak: 0 };
  const pairMasks = (Array.isArray(pairs) ? pairs : []).map((pair) => threeDuishuPairMask(pair)).filter((mask) => mask > 0);
  return threeDuishuComboRateFromMasks(masks, pairMasks, masks.length);
}

const THREE_DUISHU_BLOCK_SIZE = Number(process.env.THREE_DUISHU_BLOCK_SIZE || 20);

const THREE_DUISHU_BLOCK_WINDOW = Number(process.env.THREE_DUISHU_BLOCK_WINDOW || 120);

function threeDuishuComboBlockStats(records, pairs, windowSize, blockSize) {
  const rows = (Array.isArray(records) ? records : [])
    .filter((record) => firstFourNumbers(record).length >= 4)
    .slice(0, Math.max(1, Number(windowSize || THREE_DUISHU_BLOCK_WINDOW)));
  const size = Math.max(1, Number(blockSize || THREE_DUISHU_BLOCK_SIZE));
  const blocks = [];
  for (let start = 0; start < rows.length; start += size) {
    const blockRows = rows.slice(start, start + size);
    if (!blockRows.length) continue;
    const result = threeDuishuComboRate(blockRows, pairs, blockRows.length);
    if (result.total) {
      blocks.push({
        index: blocks.length + 1,
        total: result.total,
        hit: result.hit,
        fail: result.fail,
        rate: result.rate
      });
    }
  }
  if (!blocks.length) {
    return {
      window: rows.length,
      blockSize: size,
      blockCount: 0,
      minRate: null,
      avgRate: null,
      maxRate: null,
      variance: 0,
      weakBlocks: 0,
      blocks: []
    };
  }
  const rates = blocks.map((block) => Number(block.rate || 0));
  const sum = rates.reduce((acc, rate) => acc + rate, 0);
  const avg = sum / rates.length;
  const variance = rates.reduce((acc, rate) => acc + Math.pow(rate - avg, 2), 0) / rates.length;
  return {
    window: rows.length,
    blockSize: size,
    blockCount: blocks.length,
    minRate: Number(Math.min(...rates).toFixed(2)),
    avgRate: Number(avg.toFixed(2)),
    maxRate: Number(Math.max(...rates).toFixed(2)),
    variance: Number(Math.sqrt(variance).toFixed(2)),
    weakBlocks: blocks.filter((block) => Number(block.rate || 0) < THREE_DUISHU_BLOCK_FLOOR).length,
    floor: THREE_DUISHU_BLOCK_FLOOR,
    blocks
  };
}

function threeDuishuComboRateFromMasks(masks, pairMasks, limit) {
  const rows = (Array.isArray(masks) ? masks : []).slice(0, Math.max(1, Number(limit || MODEL_RATE_WINDOW)));
  if (!rows.length) return { total: 0, hit: 0, fail: 0, rate: null, streak: 0 };
  let hit = 0;
  let fail = 0;
  rows.forEach((mask) => {
    const recordHits = pairMasks.some((pairMask) => (mask & pairMask) === pairMask);
    if (recordHits) fail += 1;
    else hit += 1;
  });
  let streak = 0;
  for (const mask of rows) {
    if (pairMasks.some((pairMask) => (mask & pairMask) === pairMask)) break;
    streak += 1;
  }
  return {
    total: rows.length,
    hit,
    fail,
    rate: Number(((hit / rows.length) * 100).toFixed(2)),
    streak
  };
}

function threeDuishuPairMask(pair) {
  let mask = 0;
  (Array.isArray(pair && pair.digits) ? pair.digits : []).forEach((digit) => {
    const value = Number(String(digit));
    if (Number.isFinite(value) && value >= 0 && value <= 9) mask |= (1 << value);
  });
  return mask;
}

function firstFourDigitMask(record) {
  const actual = firstFourNumbers(record);
  if (actual.length < 4) return 0;
  let mask = 0;
  actual.forEach((digit) => {
    const value = Number(String(digit));
    if (Number.isFinite(value) && value >= 0 && value <= 9) mask |= (1 << value);
  });
  return mask;
}

function preprocessThreeDuishuHistoryMasks(records, limit) {
  return (Array.isArray(records) ? records : [])
    .filter((record) => firstFourNumbers(record).length >= 4)
    .slice(0, Math.max(1, Number(limit || MODEL_RATE_WINDOW)))
    .map((record) => firstFourDigitMask(record))
    .filter((mask) => mask > 0);
}

function threeDuishuComboRateMultiWindow(records, pairs, limits) {
  const uniqueLimits = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const maxLimit = Math.max(...uniqueLimits);
  const masks = preprocessThreeDuishuHistoryMasks(records, maxLimit);
  const pairMasks = (Array.isArray(pairs) ? pairs : []).map((pair) => threeDuishuPairMask(pair)).filter((mask) => mask > 0);
  const out = {};
  uniqueLimits.forEach((limit) => {
    out[limit] = threeDuishuComboRateFromMasks(masks, pairMasks, limit);
  });
  return out;
}

const THREE_DUISHU_PAIRS = [
  { key: "05", digits: ["0", "5"], codes: ["0", "5"] },
  { key: "16", digits: ["1", "6"], codes: ["1", "6"] },
  { key: "27", digits: ["2", "7"], codes: ["2", "7"] },
  { key: "38", digits: ["3", "8"], codes: ["3", "8"] },
  { key: "49", digits: ["4", "9"], codes: ["4", "9"] }
];

function scoreThreeDuishuCandidatesCore(history) {
  const candidates = [];
  const stableExclude = getStableMathExcludeDigitSet();
  for (let a = 0; a < THREE_DUISHU_PAIRS.length - 2; a += 1) {
    for (let b = a + 1; b < THREE_DUISHU_PAIRS.length - 1; b += 1) {
      for (let c = b + 1; c < THREE_DUISHU_PAIRS.length; c += 1) {
        const pairs = [THREE_DUISHU_PAIRS[a], THREE_DUISHU_PAIRS[b], THREE_DUISHU_PAIRS[c]];
        const rateWindows = threeDuishuComboRateMultiWindow(history, pairs, [30, MODEL_NEAR_WINDOW, 60, 120, MODEL_STABLE_WINDOW]);
        const r30 = rateWindows[30];
        const r40 = rateWindows[MODEL_NEAR_WINDOW];
        const r60 = rateWindows[60];
        const r120 = rateWindows[120];
        const r300 = rateWindows[MODEL_STABLE_WINDOW];
        const block = threeDuishuComboBlockStats(history, pairs, THREE_DUISHU_BLOCK_WINDOW, THREE_DUISHU_BLOCK_SIZE);
        const pairHealth = threeDuishuPairHealth(history, pairs, 120);
        const lower30 = wilsonLowerPercent(r30.hit, r30.total);
        const lower40 = wilsonLowerPercent(r40.hit, r40.total);
        const lower60 = wilsonLowerPercent(r60.hit, r60.total);
        const lower120 = wilsonLowerPercent(r120.hit, r120.total);
        const lower300 = wilsonLowerPercent(r300.hit, r300.total);
        const shortRate = Number(r40.rate != null ? r40.rate : r30.rate || 0);
        const midRate = Number(r120.rate || r60.rate || 0);
        const longRate = Number(r300.rate || midRate || 0);
        const blockMinRate = Number(block.minRate || midRate || 0);
        const blockAvgRate = Number(block.avgRate || midRate || 0);
        const pairMinLower = Number(pairHealth.minLower || 0);
        const instability = Math.max(
          Math.abs(shortRate - Number(r60.rate || shortRate)),
          Math.abs(Number(r60.rate || shortRate) - midRate),
          Math.abs(midRate - longRate)
        );
        const displayDigits = pairs.map((pair) => chooseThreeDuishuDisplayCode(history, pair)).sort((left, right) => Number(left) - Number(right));
        const code = displayDigits.join("");
        const stableMathOverlap = displayDigits.reduce((count, digit) => count + (stableExclude.has(String(digit)) ? 1 : 0), 0);
        const recentFailPenalty = Number((r40.fail != null ? r40.fail : r30.fail) || 0) * 0.28 + Math.max(0, 3 - Number((r40.streak != null ? r40.streak : r30.streak) || 0)) * 0.6;
        const weakBlockPenalty = Number(block.weakBlocks || 0) * 0.9;
        const score =
          lower30 * 0.12 +
          lower40 * 0.12 +
          lower60 * 0.14 +
          lower120 * 0.22 +
          lower300 * 0.12 +
          blockMinRate * 0.16 +
          blockAvgRate * 0.07 +
          pairMinLower * 0.05 -
          instability * 0.22 -
          Number(block.variance || 0) * 0.03 -
          recentFailPenalty -
          weakBlockPenalty +
          stableMathOverlap * 1.25 +
          Math.min(Number((r40.streak != null ? r40.streak : r30.streak) || 0), 10) * 0.1;
        const pairKey = pairs.map((pair) => pair.key).join("|");
        candidates.push({
          _pairs: pairs,
          value: pairKey,
          pairKey,
          code,
          shot: threeDuishuShotText(pairs),
          pairs: pairs.map((pair) => pair.key),
          pairCodes: pairs.map((pair) => pair.codes.join("")),
          rate30: r30.rate,
          rate40: r40.rate,
          rate60: r60.rate,
          rate120: r120.rate,
          rate300: r300.rate,
          lower30,
          lower40,
          lower60,
          lower120,
          lower300,
          sample30: r30.total,
          sample40: r40.total,
          sample120: r120.total,
          fail30: r30.fail,
          fail40: r40.fail,
          streak30: r30.streak,
          streak40: r40.streak,
          instability: Number(instability.toFixed(2)),
          block,
          pairHealth,
          stableMathOverlap,
          recentFailPenalty: Number(recentFailPenalty.toFixed(2)),
          weakBlockPenalty: Number(weakBlockPenalty.toFixed(2)),
          score: Number(score.toFixed(4))
        });
      }
    }
  }
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (Number(b.block && b.block.minRate || 0) !== Number(a.block && a.block.minRate || 0)) return Number(b.block && b.block.minRate || 0) - Number(a.block && a.block.minRate || 0);
    if (Number(b.lower120 || 0) !== Number(a.lower120 || 0)) return Number(b.lower120 || 0) - Number(a.lower120 || 0);
    if (Number(b.rate30 || 0) !== Number(a.rate30 || 0)) return Number(b.rate30 || 0) - Number(a.rate30 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return a.code.localeCompare(b.code);
  });
  return candidates;
}

const scoreThreeDuishuCandidatesLru = new Map();

function scoreThreeDuishuCandidatesCacheKey(history) {
  const rows = (Array.isArray(history) ? history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const head = rows[0];
  const tail = rows[rows.length - 1];
  const recentPeriods = rows.slice(0, 3).map((row) => (row && row.period != null ? String(row.period) : "")).join(",");
  const stableSig = Array.from(getStableMathExcludeDigitSet()).sort().join("");
  return [
    rows.length,
    head && head.uid ? head.uid : "",
    head && Array.isArray(head.numbers) ? head.numbers.slice(0, 4).join("") : "",
    tail && tail.uid ? tail.uid : "",
    tail && Array.isArray(tail.numbers) ? tail.numbers.slice(0, 4).join("") : "",
    recentPeriods,
    stableSig
  ].join("|");
}

function scoreThreeDuishuCandidates(history) {
  const cacheKey = scoreThreeDuishuCandidatesCacheKey(history);
  if (scoreThreeDuishuCandidatesLru.has(cacheKey)) {
    return scoreThreeDuishuCandidatesLru.get(cacheKey);
  }
  const candidates = scoreThreeDuishuCandidatesCore(history);
  scoreThreeDuishuCandidatesLru.set(cacheKey, candidates);
  if (scoreThreeDuishuCandidatesLru.size > SCORE_THREE_DUISHU_LRU_MAX) {
    const oldest = scoreThreeDuishuCandidatesLru.keys().next().value;
    scoreThreeDuishuCandidatesLru.delete(oldest);
  }
  return candidates;
}

function buildThreeDuishuRecommendationPayload(history, period) {
  const candidates = scoreThreeDuishuCandidates(history);
  const selected = selectStableThreeDuishuCandidate(candidates, period, { applyLock: true });
  const best = selected.selected || candidates[0];
  if (!best) {
    return { ok: false, code: "", shot: "", pairs: [], error: "no candidate" };
  }
  const walkForward = walkForwardThreeDuishu(history);
  const quality = threeDuishuQualityLabel(best, walkForward);
  const recommendable = quality !== "观察";
  const confidence = quality === "稳固" ? "high" : (quality === "可用" ? "normal" : "low");
  return {
    ok: true,
    period,
    latestNumbers: state.latestRecord && Array.isArray(state.latestRecord.numbers) ? state.latestRecord.numbers.slice(0, 5) : [],
    code: best.code,
    shot: best.shot,
    pairs: best.pairs,
    pairCodes: best.pairCodes,
    rate30: best.rate30,
    rate40: best.rate40,
    rate60: best.rate60,
    rate120: best.rate120,
    rate300: best.rate300,
    sample30: best.sample30,
    sample40: best.sample40,
    sample120: best.sample120,
    fail30: best.fail30,
    fail40: best.fail40,
    streak30: best.streak30,
    streak40: best.streak40,
    lower30: best.lower30,
    lower40: best.lower40,
    lower120: best.lower120,
    instability: best.instability,
    block: best.block,
    pairHealth: best.pairHealth,
    selectedReason: selected.reason,
    selectedLocked: selected.locked,
    selectedHoldCount: selected.holdCount,
    primaryCode: selected.primary && selected.primary.code,
    primaryPairKey: selected.primary && selected.primary.pairKey,
    score: best.score,
    walkForward,
    quality,
    confidence,
    recommendable,
    stableMathOverlap: Number(best.stableMathOverlap || 0),
    candidates: candidates.slice(0, 5).map(publicThreeDuishuCandidate)
  };
}

function threeDuishuHistorySignature(history, period) {
  const rows = Array.isArray(history) ? history : [];
  const first = rows[0];
  return [
    String(period || ""),
    rows.length,
    first && first.uid ? first.uid : "",
    first && Array.isArray(first.numbers) ? first.numbers.slice(0, 4).join("") : ""
  ].join("|");
}

function getThreeDuishuRecommendation() {
  const history = (Array.isArray(state && state.history) ? state.history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const period = state.latestRecord && state.latestRecord.period ? state.latestRecord.period : "";
  const signature = threeDuishuHistorySignature(history, period);
  const cached = state && state.threeDuishuRecommendationCache;
  if (cached && cached.signature === signature) {
    const out = Object.assign({}, cached);
    delete out.signature;
    delete out.computedAt;
    return out;
  }
  const fresh = recomputeThreeDuishuRecommendation();
  const out = Object.assign({}, fresh);
  delete out.signature;
  delete out.computedAt;
  return out;
}

function threeDuishuSnapshotIsComplete(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return false;
  if (String(snapshot.version || "") !== THREE_DUISHU_SNAPSHOT_VERSION) return false;
  const pairs = (Array.isArray(snapshot.pairs) ? snapshot.pairs : []).map(String).filter((key) => /^\d{2}$/.test(key));
  const code = String(snapshot.code || "");
  return pairs.length === 3 && /^\d{3}$/.test(code);
}

function pick5000SnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const coldPositionCount = items.filter((item) => item && item.kind === "position" && item.side === "cold").length;
  const coldHeweiCount = items.filter((item) => item && item.kind === "hewei" && item.side === "cold").length;
  const recentCount = items.filter((item) => item && item.kind === "extra" && item.key === "recentExactCodeAvoid500" && Number.isFinite(Number(item.generatedGroupCount))).length;
  const reverseCount = items.filter((item) => item && item.kind === "derivedReverseShape").length;
  const anyTwoCount = items.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const anyThreeCount = items.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  const txt = snapshot && snapshot.txtSelection ? snapshot.txtSelection : {};
  const rawCount = Number(txt.rawCount || 0);
  const selectedCount = Number(txt.selectedCount || 0);
  const txtValid = txt.strategy === "ascending-first-n"
    && selectedCount > 0
    && rawCount >= selectedCount
    && (rawCount === selectedCount || /^\d{4}$/.test(String(txt.cutoffCode || "")));
  return !!(
    snapshot &&
    snapshot.version === PICK5000_SNAPSHOT_VERSION &&
    items.length === PICK5000_ITEM_COUNT &&
    coldPositionCount === 2 &&
    coldHeweiCount === 2 &&
    recentCount === 1 &&
    reverseCount === 1 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1 &&
    txtValid
  );
}

function dingKillChooseRecentExactCandidate(baseItems, recentCandidates, tailItems, target) {
  const bandMin = DING_KILL_TARGET_MIN;
  const bandMax = DING_KILL_TARGET_MAX;
  const want = Math.max(bandMin, Math.min(bandMax, Number(target || bandMin)));
  const candidates = (Array.isArray(recentCandidates) ? recentCandidates : []).filter((item) => item && item.code);
  if (!candidates.length) return null;
  const base = (Array.isArray(baseItems) ? baseItems : []).filter((item) => item && item.code);
  const tail = (Array.isArray(tailItems) ? tailItems : []).filter((item) => item && item.code);
  const compared = candidates.map((item) => {
    // 一次扫描：目标带内只需知道是否够 want；超 bandMax 时用 early-stop 的 raw 下界即可
    const groupInfo = coldFrameworkGeneratedGroupInfo(base.concat([item], tail), want);
    const selectedCount = Number(groupInfo.selectedCount || 0);
    const earlyStopped = groupInfo.earlyStopped === true;
    const rawGeneratedGroupCount = earlyStopped
      ? Math.max(Number(groupInfo.rawCount || 0), want + 1)
      : Number(groupInfo.rawCount || 0);
    let cutoffCode = String(groupInfo.cutoffCode || "");
    let trimmedGroupCount = Math.max(0, rawGeneratedGroupCount - selectedCount);
    let distance = 0;
    if (rawGeneratedGroupCount > bandMax) {
      distance = rawGeneratedGroupCount - want;
    } else if (rawGeneratedGroupCount < bandMin) {
      distance = (bandMin - rawGeneratedGroupCount) + 10000;
    } else {
      distance = Math.abs(rawGeneratedGroupCount - want);
    }
    return {
      key: item.key,
      name: item.name,
      code: item.code,
      rawGeneratedGroupCount,
      generatedGroupCount: selectedCount,
      trimmedGroupCount,
      cutoffCode,
      trimStrategy: "ascending-first-n",
      distance,
      inBand: rawGeneratedGroupCount >= bandMin && rawGeneratedGroupCount <= bandMax,
      stableScore: Number(item.stableScore || item.rate || 0)
    };
  }).sort((a, b) => {
    if (a.distance !== b.distance) return a.distance - b.distance;
    if (!!b.inBand !== !!a.inBand) return (b.inBand ? 1 : 0) - (a.inBand ? 1 : 0);
    if (b.stableScore !== a.stableScore) return b.stableScore - a.stableScore;
    return String(a.key || "").localeCompare(String(b.key || ""));
  });
  const best = compared[0];
  const source = candidates.filter((item) => item && item.key === best.key)[0] || candidates[0];
  const reason = best.trimmedGroupCount > 0
    ? `旧码择优，目标带${bandMin}-${bandMax}（本期目标${want}），攻击语完整展开${best.rawGeneratedGroupCount}组；${best.generatedGroupCount}组及截止${best.cutoffCode}仅作选型参考，不裁TXT`
    : `旧码择优，目标带${bandMin}-${bandMax}（本期目标${want}），当前${best.generatedGroupCount}组`;
  return Object.assign({}, source, {
    targetGroupCount: want,
    targetBandMin: bandMin,
    targetBandMax: bandMax,
    rawGeneratedGroupCount: best.rawGeneratedGroupCount,
    generatedGroupCount: best.generatedGroupCount,
    trimmedGroupCount: best.trimmedGroupCount,
    generatedCodeCutoff: best.cutoffCode,
    trimStrategy: best.trimStrategy,
    groupCountDistance: best.distance,
    choiceReason: reason,
    choiceCompared: compared
  });
}

function dingKillPeriodTarget(basePeriod) {
  const text = String(basePeriod || "0");
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const span = DING_KILL_TARGET_MAX - DING_KILL_TARGET_MIN + 1;
  return DING_KILL_TARGET_MIN + ((hash >>> 0) % span);
}

function dingKillSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const heweiCount = formal.filter((item) => item && item.kind === "hewei").length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape").length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const anyThreeCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  const recentCount = formal.filter((item) => item && item.kind === "extra" && /^recentExactCodeAvoid(?:\d+|Max)$/.test(String(item.key || "")) && Number.isFinite(Number(item.generatedGroupCount))).length;
  const txt = snapshot && snapshot.txtSelection ? snapshot.txtSelection : {};
  const selectedCount = Number(txt.selectedCount || 0);
  const rawCount = Number(txt.rawCount || 0);
  const txtValid = txt.strategy === "ascending-first-n"
    && selectedCount > 0
    && rawCount >= selectedCount
    && (rawCount === selectedCount || /^\d{4}$/.test(String(txt.cutoffCode || "")));
  return !!(
    snapshot &&
    snapshot.version === DING_KILL_SNAPSHOT_VERSION &&
    formal.length === DING_KILL_ITEM_COUNT &&
    items.length === DING_KILL_ITEM_COUNT &&
    positionCount === 2 &&
    heweiCount === 1 &&
    reverseCount === 1 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1 &&
    recentCount === 1 &&
    txtValid
  );
}

function coldFrameworkChooseRecentExactCandidate(baseItems, recentCandidates, tailItems) {
  const target = COLD_FRAMEWORK_TARGET_GROUP_COUNT;
  const candidates = (Array.isArray(recentCandidates) ? recentCandidates : []).filter((item) => item && item.code);
  if (!candidates.length) return null;
  const base = (Array.isArray(baseItems) ? baseItems : []).filter((item) => item && item.code);
  const tail = (Array.isArray(tailItems) ? tailItems : []).filter((item) => item && item.code);
  const compared = candidates.map((item) => {
    const groupInfo = coldFrameworkGeneratedGroupInfo(base.concat([item], tail), target);
    const rawGeneratedGroupCount = groupInfo.rawCount;
    const generatedGroupCount = groupInfo.selectedCount;
    const trimmedGroupCount = Math.max(0, rawGeneratedGroupCount - target);
    return {
      key: item.key,
      name: item.name,
      code: item.code,
      rawGeneratedGroupCount,
      generatedGroupCount,
      trimmedGroupCount,
      cutoffCode: groupInfo.cutoffCode,
      trimStrategy: groupInfo.strategy,
      distance: Math.abs(generatedGroupCount - target),
      stableScore: Number(item.stableScore || item.rate || 0)
    };
  }).sort((a, b) => {
    if (a.distance !== b.distance) return a.distance - b.distance;
    if (b.stableScore !== a.stableScore) return b.stableScore - a.stableScore;
    return String(a.key || "").localeCompare(String(b.key || ""));
  });
  const best = compared[0];
  const source = candidates.filter((item) => item && item.key === best.key)[0] || candidates[0];
  const reason = best.trimmedGroupCount > 0
    ? `500/800择优，目标${target}组，攻击语完整展开${best.rawGeneratedGroupCount}组；${target}组及截止${best.cutoffCode}仅作选型参考，不裁TXT`
    : `500/800择优，目标${target}组，当前${best.generatedGroupCount}组`;
  return Object.assign({}, source, {
    targetGroupCount: target,
    rawGeneratedGroupCount: best.rawGeneratedGroupCount,
    generatedGroupCount: best.generatedGroupCount,
    trimmedGroupCount: best.trimmedGroupCount,
    generatedCodeCutoff: best.cutoffCode,
    trimStrategy: best.trimStrategy,
    groupCountDistance: best.distance,
    choiceReason: reason,
    choiceCompared: compared
  });
}

function coldFrameworkExtraCandidate(prediction, history, evaluatedCount, key) {
  const item = autoTop6CandidateItems(prediction).filter((candidate) => candidate && candidate.kind === "extra" && candidate.key === key)[0] || null;
  if (!item) return null;
  if (/^recentExactCodeAvoid\d+$/.test(String(key || ""))) {
    const lookback = Number(String(key).replace(/\D/g, ""));
    return coldFrameworkDecorateRecentExactItem(history, item, evaluatedCount, lookback);
  }
  return autoTop6DecorateItem(history, item, evaluatedCount);
}

function coldFrameworkHeweiCandidates(history, evaluatedCount) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const compositeGroups = safeHistory.map((record) => getCompositeHeweiGroup(record).map(String));
  const heweiDigitSeries = {};
  AUTO_TOP6_HEWEI.filter((entry) => entry.key !== "unified").forEach((hewei) => {
    heweiDigitSeries[hewei.key] = compositeGroups.map((group) => group[hewei.index]);
  });
  return AUTO_TOP6_HEWEI.filter((entry) => entry.key !== "unified").map((hewei) => {
    const stats = coldFrameworkDigitStatsFromSeries(heweiDigitSeries[hewei.key], COLD_FRAMEWORK_COLD_WINDOW);
    const code = stats.cold[0] || "";
    return autoTop6DecorateItem(safeHistory, {
      kind: "hewei",
      key: hewei.key,
      side: "cold",
      name: `${hewei.label}冷码`,
      code,
      mode: "avoid",
      coldPool: stats.cold,
      hotPool: stats.hot,
      coldWindow: stats.window,
      coldCount: stats.minCount,
      hotCount: stats.maxCount
    }, evaluatedCount);
  }).filter((item) => item && item.code).sort(coldFrameworkOrder);
}

function coldFrameworkOrder(a, b) {
  if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
  if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
  if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
  if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
  return String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
}

function coldFrameworkDigitStatsFromSeries(digitSeries, limit) {
  const rows = (Array.isArray(digitSeries) ? digitSeries : []).slice(0, Math.max(1, Number(limit || COLD_FRAMEWORK_COLD_WINDOW)));
  const counts = {};
  DIGIT_POOL.forEach((digit) => { counts[digit] = 0; });
  rows.forEach((value) => {
    const digit = String(value === undefined || value === null ? "" : value).replace(/\D/g, "").charAt(0);
    if (DIGIT_POOL.includes(digit)) counts[digit] = Number(counts[digit] || 0) + 1;
  });
  const ranked = DIGIT_POOL.slice().sort((a, b) => {
    if (Number(counts[a] || 0) !== Number(counts[b] || 0)) return Number(counts[a] || 0) - Number(counts[b] || 0);
    return Number(a) - Number(b);
  });
  const hotRanked = ranked.slice().reverse().sort((a, b) => {
    if (Number(counts[b] || 0) !== Number(counts[a] || 0)) return Number(counts[b] || 0) - Number(counts[a] || 0);
    return Number(a) - Number(b);
  });
  return {
    total: rows.length,
    window: Math.max(1, Number(limit || COLD_FRAMEWORK_COLD_WINDOW)),
    counts,
    cold: ranked,
    hot: hotRanked,
    minCount: rows.length ? Number(counts[ranked[0]] || 0) : 0,
    maxCount: rows.length ? Number(counts[hotRanked[0]] || 0) : 0
  };
}

function coldFrameworkDigitStats(history, getter, limit) {
  const rows = (Array.isArray(history) ? history : []).filter(Boolean).slice(0, Math.max(1, Number(limit || COLD_FRAMEWORK_COLD_WINDOW)));
  return coldFrameworkDigitStatsFromSeries(rows.map((record) => getter(record)), limit);
}

function coldFrameworkPositionCandidates(history, evaluatedCount) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  return AUTO_TOP6_POSITIONS.map((pos) => {
    const stats = coldFrameworkDigitStats(safeHistory, (record) => {
      const nums = Array.isArray(record && record.numbers) ? record.numbers : [];
      return nums[pos.index];
    }, COLD_FRAMEWORK_COLD_WINDOW);
    const code = stats.cold[0] || "";
    return autoTop6DecorateItem(safeHistory, {
      kind: "position",
      key: pos.key,
      side: "cold",
      name: `${pos.label}冷码`,
      code,
      mode: "avoid",
      coldPool: stats.cold,
      hotPool: stats.hot,
      coldWindow: stats.window,
      coldCount: stats.minCount,
      hotCount: stats.maxCount
    }, evaluatedCount);
  }).filter((item) => item && item.code).sort(coldFrameworkOrder);
}

function coldFrameworkSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const coldPositionCount = items.filter((item) => item && item.kind === "position" && item.side === "cold").length;
  const coldHeweiCount = items.filter((item) => item && item.kind === "hewei" && item.side === "cold").length;
  const recentCount = items.filter((item) => item && item.kind === "extra" && ["recentExactCodeAvoid500", "recentExactCodeAvoid800"].includes(item.key)).length;
  const reverseCount = items.filter((item) => item && item.kind === "derivedReverseShape").length;
  const anyTwoCount = items.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const anyThreeCount = items.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot &&
    snapshot.version === COLD_FRAMEWORK_SNAPSHOT_VERSION &&
    items.length === COLD_FRAMEWORK_ITEM_COUNT &&
    coldPositionCount === 2 &&
    coldHeweiCount === 2 &&
    recentCount === 1 &&
    reverseCount === 1 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1
  );
}

function coldFrameworkSerializeItem(item, index) {
  return {
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
    codeCount: item.codeCount,
    codes: Array.isArray(item.codes) ? item.codes.slice(0, recentExactCodeCodeLimit(item)) : undefined,
    shapeKey: item.shapeKey || "",
    derivedFrom: item.derivedFrom || "",
    coldPool: Array.isArray(item.coldPool) ? item.coldPool.slice(0, 4) : undefined,
    hotPool: Array.isArray(item.hotPool) ? item.hotPool.slice(0, 4) : undefined,
    coldWindow: item.coldWindow,
    coldCount: item.coldCount,
    hotCount: item.hotCount,
    targetGroupCount: item.targetGroupCount,
    rawGeneratedGroupCount: item.rawGeneratedGroupCount,
    generatedGroupCount: item.generatedGroupCount,
    trimmedGroupCount: item.trimmedGroupCount,
    generatedCodeCutoff: item.generatedCodeCutoff || "",
    trimStrategy: item.trimStrategy || "",
    groupCountDistance: item.groupCountDistance,
    choiceReason: item.choiceReason || ""
    // 不落盘 choiceCompared：候选对比表会把单条预测撑到十余 KB，999 条合计十余 MB，saveState 同步 stringify 堵死事件循环
  };
}

function coldFrameworkGeneratedGroupInfo(items, target) {
  const activeItems = (Array.isArray(items) ? items : []).filter((item) => item && item.code);
  const limit = Math.max(0, Number(target || 0));
  if (!activeItems.length) return { rawCount: 0, selectedCount: 0, cutoffCode: "", strategy: "ascending-first-n" };
  // 只需前 N 组时提前停，避免结算选型时 10000 全扫堵死事件循环；rawCount 在未扫完时为下界。
  const stopAt = limit > 0 ? limit : 10000;
  const accepted = [];
  let scannedAll = true;
  for (let n = 0; n < 10000; n += 1) {
    const code = String(n).padStart(4, "0");
    const numbers = code.split("").concat(["0"]);
    const record = {
      numbers,
      metrics: calculateMetrics(numbers)
    };
    const ok = activeItems.every((item) => autoTop6RecordSuccess(item, record) === true);
    if (ok) {
      accepted.push(code);
      if (accepted.length >= stopAt && limit > 0) {
        scannedAll = false;
        break;
      }
    }
  }
  const selectedCount = limit > 0 ? Math.min(limit, accepted.length) : accepted.length;
  return {
    rawCount: scannedAll ? accepted.length : Math.max(accepted.length, limit),
    selectedCount,
    cutoffCode: selectedCount > 0 && (selectedCount < accepted.length || !scannedAll)
      ? accepted[selectedCount - 1]
      : "",
    strategy: "ascending-first-n",
    earlyStopped: !scannedAll
  };
}

function sideStableChooseRecentExactCandidate(baseItems, recentCandidates, tailItems, target) {
  const bandMin = SIDE_STABLE_TARGET_MIN;
  const bandMax = SIDE_STABLE_TARGET_MAX;
  const want = Math.max(bandMin, Math.min(bandMax, Number(target || bandMin)));
  const candidates = (Array.isArray(recentCandidates) ? recentCandidates : []).filter((item) => item && item.code);
  if (!candidates.length) return null;
  const base = (Array.isArray(baseItems) ? baseItems : []).filter((item) => item && item.code);
  const tail = (Array.isArray(tailItems) ? tailItems : []).filter((item) => item && item.code);
  const compared = candidates.map((item) => {
    // 一次扫描：目标带内只需知道是否够 want；超 bandMax 时用 early-stop 的 raw 下界即可
    const groupInfo = coldFrameworkGeneratedGroupInfo(base.concat([item], tail), want);
    const selectedCount = Number(groupInfo.selectedCount || 0);
    const earlyStopped = groupInfo.earlyStopped === true;
    const rawGeneratedGroupCount = earlyStopped
      ? Math.max(Number(groupInfo.rawCount || 0), want + 1)
      : Number(groupInfo.rawCount || 0);
    let cutoffCode = String(groupInfo.cutoffCode || "");
    let trimmedGroupCount = Math.max(0, rawGeneratedGroupCount - selectedCount);
    let distance = 0;
    if (rawGeneratedGroupCount > bandMax) {
      distance = rawGeneratedGroupCount - want;
    } else if (rawGeneratedGroupCount < bandMin) {
      distance = (bandMin - rawGeneratedGroupCount) + 10000;
    } else {
      distance = Math.abs(rawGeneratedGroupCount - want);
    }
    return {
      key: item.key,
      name: item.name,
      code: item.code,
      rawGeneratedGroupCount,
      generatedGroupCount: selectedCount,
      trimmedGroupCount,
      cutoffCode,
      trimStrategy: "ascending-first-n",
      distance,
      inBand: rawGeneratedGroupCount >= bandMin && rawGeneratedGroupCount <= bandMax,
      stableScore: Number(item.stableScore || item.rate || 0)
    };
  }).sort((a, b) => {
    if (a.distance !== b.distance) return a.distance - b.distance;
    if (!!b.inBand !== !!a.inBand) return (b.inBand ? 1 : 0) - (a.inBand ? 1 : 0);
    if (b.stableScore !== a.stableScore) return b.stableScore - a.stableScore;
    return String(a.key || "").localeCompare(String(b.key || ""));
  });
  const best = compared[0];
  const source = candidates.filter((item) => item && item.key === best.key)[0] || candidates[0];
  const reason = best.trimmedGroupCount > 0
    ? `旁路稳筛旧码择优，目标带${bandMin}-${bandMax}（本期目标${want}），攻击语完整展开${best.rawGeneratedGroupCount}组；${best.generatedGroupCount}组及截止${best.cutoffCode}仅作选型参考，不裁TXT`
    : `旁路稳筛旧码择优，目标带${bandMin}-${bandMax}（本期目标${want}），当前${best.generatedGroupCount}组`;
  return Object.assign({}, source, {
    targetGroupCount: want,
    targetBandMin: bandMin,
    targetBandMax: bandMax,
    rawGeneratedGroupCount: best.rawGeneratedGroupCount,
    generatedGroupCount: best.generatedGroupCount,
    trimmedGroupCount: best.trimmedGroupCount,
    generatedCodeCutoff: best.cutoffCode,
    trimStrategy: best.trimStrategy,
    groupCountDistance: best.distance,
    choiceReason: reason,
    choiceCompared: compared
  });
}

function coldFrameworkRecentExactRollingStats(history, lookbackLimit, sampleLimit) {
  // 滑动窗口计数，避免每个 sample 都 slice + 新建 Set（冷框架选型时会放大成 OOM 尖峰）
  const rows = (Array.isArray(history) ? history : []).filter((record) => /^\d{4}$/.test(firstFourCode(record)));
  const lookback = Math.max(1, Number(lookbackLimit || 500));
  const sample = Math.max(1, Number(sampleLimit || MODEL_RATE_WINDOW));
  const maxRows = Math.min(sample, rows.length);
  const codes = rows.slice(0, Math.min(rows.length, maxRows + lookback)).map((record) => firstFourCode(record));
  const windowCounts = new Map();
  const addToWindow = (idx) => {
    if (idx < 0 || idx >= codes.length) return;
    const code = codes[idx];
    windowCounts.set(code, (windowCounts.get(code) || 0) + 1);
  };
  const removeFromWindow = (idx) => {
    if (idx < 0 || idx >= codes.length) return;
    const code = codes[idx];
    const next = (windowCounts.get(code) || 0) - 1;
    if (next <= 0) windowCounts.delete(code);
    else windowCounts.set(code, next);
  };
  for (let j = 1; j <= lookback && j < codes.length; j += 1) addToWindow(j);
  const vals = [];
  for (let index = 0; index < maxRows; index += 1) {
    const code = codes[index];
    const previousCount = Math.min(lookback, Math.max(0, codes.length - index - 1));
    if (/^\d{4}$/.test(code) && previousCount >= 1) {
      vals.push(!windowCounts.has(code));
    }
    removeFromWindow(index + 1);
    addToWindow(index + 1 + lookback);
  }
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

function coldFrameworkDecorateRecentExactItem(history, item, evaluatedCount, lookbackLimit) {
  const base = autoTop6DecorateItem(history, item, evaluatedCount);
  const s30 = coldFrameworkRecentExactRollingStats(history, lookbackLimit, 30);
  const s60 = coldFrameworkRecentExactRollingStats(history, lookbackLimit, 60);
  const s120 = coldFrameworkRecentExactRollingStats(history, lookbackLimit, 120);
  const sStable = coldFrameworkRecentExactRollingStats(history, lookbackLimit, MODEL_STABLE_WINDOW);
  const rates = [s30.rate, s120.rate, sStable.rate].map(finiteNumberOrNull).filter((value) => value !== null);
  const safetyRate = rates.length ? Math.min(...rates) : null;
  const weightedParts = [
    { weight: 0.36, rate: finiteNumberOrNull(s30.rate) },
    { weight: 0.30, rate: finiteNumberOrNull(s60.rate) },
    { weight: 0.22, rate: finiteNumberOrNull(s120.rate) },
    { weight: 0.12, rate: finiteNumberOrNull(sStable.rate) }
  ].filter((part) => part.rate !== null);
  const weightTotal = weightedParts.reduce((sum, part) => sum + part.weight, 0);
  let score = weightTotal ? weightedParts.reduce((sum, part) => sum + part.rate * part.weight, 0) / weightTotal : Number(base.stableScore || 0);
  score -= Math.max(0, Number(s120.maxMissStreak || 0) - 4) * 0.22;
  score -= Math.max(0, Number(sStable.maxMissStreak || 0) - 7) * 0.12;
  const stableReasons = [];
  if (s30.total >= 20 && s30.rate !== null && s30.rate < MODEL_LOW_HIT_RATE) stableReasons.push(`滚动30期${Number(s30.rate).toFixed(2)}低于${MODEL_LOW_HIT_RATE}`);
  if (s120.total >= 60 && s120.rate !== null && s120.rate < MODEL_LOW_HIT_RATE) stableReasons.push(`滚动120期${Number(s120.rate).toFixed(2)}低于${MODEL_LOW_HIT_RATE}`);
  if (sStable.total >= 120 && sStable.rate !== null && sStable.rate < MODEL_LOW_HIT_RATE) stableReasons.push(`滚动1000期${Number(sStable.rate).toFixed(2)}低于${MODEL_LOW_HIT_RATE}`);
  if (s30.missStreak >= AUTO_TOP6_RECENT_MISS_LIMIT) stableReasons.push(`当前连错${s30.missStreak}手`);
  return Object.assign({}, base, {
    rate: Number(score.toFixed(4)),
    stableScore: Number(score.toFixed(4)),
    hardnessScore: Number(score.toFixed(4)),
    rate30: s30.rate,
    rate60: s60.rate,
    rate120: s120.rate,
    rate300: sStable.rate,
    safetyRate,
    recentMissStreak: s30.missStreak,
    maxMissStreak120: s120.maxMissStreak,
    maxMissStreak1000: sStable.maxMissStreak,
    stabilityGate: stableReasons.length === 0,
    stabilityReason: stableReasons.join("；"),
    lowConfidence: stableReasons.length > 0,
    rollingRecentExact: true
  });
}

const MODEL_MIN_SAMPLE = Number(process.env.MODEL_MIN_SAMPLE || 8);

const MODEL_RATE_WINDOW = Number(process.env.MODEL_RATE_WINDOW || 30);

function recentExactCodeAvoidStats(history, lookbackLimit, sampleLimit) {
  const rows = (Array.isArray(history) ? history : []).filter((record) => /^\d{4}$/.test(firstFourCode(record)));
  const lookback = Math.max(1, Number(lookbackLimit || 300));
  const sample = Math.max(1, Number(sampleLimit || MODEL_RATE_WINDOW));
  const maxRows = Math.min(sample, rows.length);
  const codes = rows.slice(0, maxRows).map((record) => firstFourCode(record)).filter((code) => /^\d{4}$/.test(code));
  const windowCounts = new Map();
  const addToWindow = (idx) => {
    if (idx < 0 || idx >= codes.length) return;
    const code = codes[idx];
    windowCounts.set(code, (windowCounts.get(code) || 0) + 1);
  };
  const removeFromWindow = (idx) => {
    if (idx < 0 || idx >= codes.length) return;
    const code = codes[idx];
    const next = (windowCounts.get(code) || 0) - 1;
    if (next <= 0) windowCounts.delete(code);
    else windowCounts.set(code, next);
  };
  for (let j = 1; j <= lookback && j < codes.length; j += 1) addToWindow(j);
  let total = 0;
  let match = 0;
  let lastSuccess = null;
  for (let index = 0; index < maxRows; index += 1) {
    const code = codes[index];
    const previousCount = Math.min(lookback, Math.max(0, codes.length - index - 1));
    if (/^\d{4}$/.test(code) && previousCount >= 1) {
      const success = !windowCounts.has(code);
      total += 1;
      if (success) match += 1;
      if (lastSuccess === null) lastSuccess = success;
    }
    removeFromWindow(index + 1);
    addToWindow(index + 1 + lookback);
  }
  const rate = total ? Number(((match / total) * 100).toFixed(2)) : null;
  return {
    total,
    match,
    rate,
    low: total >= MODEL_MIN_SAMPLE && rate !== null && rate < MODEL_LOW_HIT_RATE,
    lastSuccess
  };
}

function recentExactCodeSet(records, limit) {
  const rows = (Array.isArray(records) ? records : []).slice(0, Math.max(1, Number(limit || 300)));
  const set = new Set();
  rows.forEach((record) => {
    const code = firstFourCode(record);
    if (/^\d{4}$/.test(code)) set.add(code);
  });
  return set;
}

function recentExactCodeCandidate(limit, history) {
  const maxMode = String(limit || "").toLowerCase() === "max";
  const n = maxMode ? Math.max(1, Array.isArray(history) ? history.length : (MAX_HISTORY || 5000)) : Math.max(1, Number(limit || 300));
  const codes = Array.from(recentExactCodeSet(history, n)).sort();
  const performance = recentExactCodeAvoidStats(history, n, MODEL_RATE_WINDOW);
  const rate30 = recentExactCodeAvoidStats(history, n, 30);
  const rate120 = recentExactCodeAvoidStats(history, n, 120);
  const rate300 = recentExactCodeAvoidStats(history, n, MODEL_STABLE_WINDOW);
  return {
    key: maxMode ? RECENT_EXACT_CODE_MAX_KEY : `recentExactCodeAvoid${n}`,
    label: maxMode ? "最大旧码回删" : `近${n}重复四码避开`,
    value: maxMode ? "最大" : String(n),
    code: maxMode ? "最大" : String(n),
    primary: maxMode ? "最大" : String(n),
    backup: maxMode ? "最大" : String(n),
    lookback: n,
    maxHistoryWindow: maxMode,
    codeCount: codes.length,
    codes,
    algorithm: "rolling-recent-exact-code-avoid",
    hitMode: false,
    matchMode: "avoid-if-current-first-four-code-not-in-previous-window",
    dynamicEveryPrediction: true,
    performance,
    rate30: rate30.rate,
    rate120: rate120.rate,
    rate300: rate300.rate,
    stats30: rate30,
    stats120: rate120,
    stats300: rate300
  };
}

function sideStablePeriodTarget(basePeriod) {
  const text = String(basePeriod || "0");
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const span = SIDE_STABLE_TARGET_MAX - SIDE_STABLE_TARGET_MIN + 1;
  return SIDE_STABLE_TARGET_MIN + ((hash >>> 0) % span);
}

function sideStableSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const totalHeweiCount = formal.filter((item) => item && item.kind === "hewei" && (item.key === "total" || item.key === "totalHewei")).length;
  const wanQianBaiCount = formal.filter((item) => item && item.kind === "hewei" && item.key === "wanQianBai").length;
  const twoDigitAvoidCount = formal.filter((item) => item && item.kind === "extra" && item.key === "twoDigitAvoid" && /^\d{2}$/.test(String(item.code || ""))).length;
  const sumAvoidCount = formal.filter((item) => item && item.kind === "extra" && item.key === "sumAvoid").length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const anyThreeCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  // 封存后 compactRecentExactCodesOnPrediction 会省略 codes。codeCount 是实际
  // 命中的旧码条数，并不等于 1300 期窗口长度；用它作 >=1000 门槛会把有效的
  // 4900~5000 组选型误判为不完整，进而使整个预测无法完成封存。
  const recentCount = formal.filter((item) => {
    if (!item || item.kind !== "extra" || item.key !== "recentExactCodeAvoid1300") return false;
    if (String(item.code || "") !== "1300") return false;
    const generatedGroupCount = Number(item.generatedGroupCount || 0);
    const targetGroupCount = Number(item.targetGroupCount || 0);
    if (!Number.isFinite(generatedGroupCount) || generatedGroupCount < SIDE_STABLE_TARGET_MIN) return false;
    if (!Number.isFinite(targetGroupCount) || targetGroupCount < SIDE_STABLE_TARGET_MIN) return false;
    if (item.codesOmitted === true) return true;
    return Array.isArray(item.codes) && item.codes.length > 0;
  }).length;
  const txt = snapshot && snapshot.txtSelection || {};
  const rawCount = Number(txt.rawCount || 0);
  const selectedCount = Number(txt.selectedCount || 0);
  const txtValid = txt.strategy === "ascending-first-n" && selectedCount > 0 && rawCount >= selectedCount && (rawCount === selectedCount || /^\d{4}$/.test(String(txt.cutoffCode || "")));
  return !!(
    snapshot &&
    snapshot.version === SIDE_STABLE_SNAPSHOT_VERSION &&
    formal.length === SIDE_STABLE_ITEM_COUNT &&
    items.length === SIDE_STABLE_ITEM_COUNT &&
    positionCount === 1 &&
    totalHeweiCount === 1 &&
    wanQianBaiCount === 1 &&
    twoDigitAvoidCount === 1 &&
    sumAvoidCount === 1 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1 &&
    recentCount === 1 &&
    txtValid
  );
}

function frameworkTwoDigitCodesFromItems(items) {
  const out = new Set();
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || item.kind !== "extra") return;
    if (!["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(String(item.key || ""))) return;
    const code = String(item.code || "");
    if (/^\d{2}$/.test(code)) out.add(code);
  });
  return out;
}

function threeDingOneSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positions = formal.filter((item) => item && item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || "")));
  const uniquePositionKeys = new Set(positions.map((item) => String(item.key || "")));
  const heweiCount = formal.filter((item) => item && item.kind === "hewei" && item.key === "wanQianBai" && /^\d$/.test(String(item.code || ""))).length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape" && /^[大小]{3,4}$/.test(String(item.code || "")) && !/^[单双]{3,4}$/.test(String(item.code || ""))).length;
  const pairCount = formal.filter((item) => item && item.kind === "extra" && ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(String(item.key || "")) && /^\d{2}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot &&
    snapshot.version === THREE_DING_ONE_SNAPSHOT_VERSION &&
    formal.length === THREE_DING_ONE_ITEM_COUNT &&
    items.length === THREE_DING_ONE_ITEM_COUNT &&
    positions.length === 2 &&
    uniquePositionKeys.size === 2 &&
    heweiCount === 1 &&
    anyTwoCount === 1 &&
    reverseCount === 1 &&
    pairCount === 1
  );
}

function mapFrameworkSnapshotItems(chosen, heweiScopeIndexes, itemCount) {
  return chosen.slice(0, itemCount).map((item, index) => ({
    rank: index + 1,
    kind: item.kind,
    key: item.key,
    side: item.side || "recommend",
    name: item.name,
    code: item.code,
    mode: item.mode || (item.kind === "extra" && /Hit$/.test(String(item.key || "")) ? "hit" : "avoid"),
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
    codeCount: item.codeCount,
    codes: Array.isArray(item.codes) ? item.codes.slice(0, recentExactCodeCodeLimit(item)) : undefined,
    shapeKey: item.shapeKey || "",
    shapeScopeLength: item.shapeScopeLength || undefined,
    derivedFrom: item.derivedFrom || "",
    heweiScopeIndexes: (item.key === "anyTwoHeweiHit" || item.key === "anyThreeHeweiHit") && Array.isArray(heweiScopeIndexes) && heweiScopeIndexes.length >= 2
      ? heweiScopeIndexes.slice()
      : undefined
  }));
}

function pickThreeDingTwoAnyTwoFromSeed(history, evaluatedCount, scopeIndexes, chosenItems, prediction, avoidCodes) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const avoid = avoidCodes instanceof Set ? avoidCodes : new Set(Array.isArray(avoidCodes) ? avoidCodes : []);
  const seedArr = uniqueDigits(ANY_TWO_HEWEI_SOURCE_DIGITS).sort();
  const kills = frameworkSingleExcludeDigits(chosenItems);
  const heweiItem = (Array.isArray(chosenItems) ? chosenItems : []).find((item) => item && item.kind === "hewei" && item.key === "wanQianBai");
  const heweiKill = frameworkSingleExcludeDigitToken(heweiItem);
  let excludedDigit = null;
  if (heweiKill && seedArr.includes(heweiKill)) excludedDigit = heweiKill;
  if (!excludedDigit) {
    for (let index = 0; index < seedArr.length; index += 1) {
      const digit = seedArr[index];
      if (kills.has(digit)) {
        excludedDigit = digit;
        break;
      }
    }
  }
  if (!excludedDigit) excludedDigit = seedArr[seedArr.length - 1] || "8";
  let digits = seedArr.filter((digit) => digit !== excludedDigit);
  let bestCode = digits.sort().join("");
  if (!isAnyTwoHeweiCode(bestCode)) bestCode = anyTwoHeweiCandidates()[0];
  if (avoid.has(bestCode)) {
    const fallback = anyTwoHeweiCandidates().find((code) => !avoid.has(code) && code.split("").filter((digit) => seedArr.includes(digit)).length === ANY_TWO_HEWEI_CODE_SIZE);
    if (fallback) bestCode = fallback;
  }
  let item = {
    kind: "extra",
    key: "anyTwoHeweiHit",
    side: "recommend",
    name: "二数合尾02468排1码命中",
    code: bestCode,
    mode: "hit",
    derivedFrom: "three-ding-two-seed-02468-exclude-one",
    structuralPick: true,
    algorithm: "three-ding-two-any-two-seed-02468-exclude-one",
    seedCode: THREE_DING_TWO_ANY_TWO_SEED,
    excludedSeedDigit: excludedDigit,
    hitMode: true,
    matchMode: "hit-if-any-two-hewei-tail-in-02468-exclude-one",
    dynamicEveryPrediction: true
  };
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  item = autoTop6DecorateItem(safeHistory, item, evaluatedCount);
  item.derivedFrom = "three-ding-two-seed-02468-exclude-one";
  item.seedCode = THREE_DING_TWO_ANY_TWO_SEED;
  item.excludedSeedDigit = excludedDigit;
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  return item;
}

function isParityOnlyShapeCode(code) {
  return /^[单双]{3,4}$/.test(String(code || ""));
}

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

function stableMathShapeFrameworkItem(shapeLabel, scopeLength) {
  const label = String(shapeLabel || "").trim();
  // 三定系列禁用单双形态挂载（全奇/全偶等）
  if (STABLE_MATH_PARITY_SHAPE_LABELS.has(label)) return null;
  const full = STABLE_MATH_SHAPE_CODE_MAP[label];
  if (!full) return null;
  const len = Math.max(3, Math.min(4, Number(scopeLength || 3)));
  const code = len >= 4 ? full : full.slice(0, len);
  if (!/^[大小单双]{3,4}$/.test(code)) return null;
  if (isParityOnlyShapeCode(code)) return null;
  const isSize = /^[大小]{3,4}$/.test(code);
  return {
    kind: "derivedReverseShape",
    key: isSize ? "reverseSize" : "stableMathShape",
    side: "recommend",
    name: `更稳排除形态(${label})`,
    code,
    shapeKey: isSize ? "shapeSize" : "shapeParity",
    shapeScopeLength: len,
    mode: "avoid",
    derivedFrom: "stable-math-filter"
  };
}

function pickStableMathShapeCandidate(assist, history, evaluatedCount, scopeLength) {
  if (!assist || !assist.ok || !(assist.excludeShapes || []).length) return null;
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  for (let i = 0; i < assist.excludeShapes.length; i += 1) {
    const raw = stableMathShapeFrameworkItem(assist.excludeShapes[i], scopeLength);
    if (!raw) continue;
    const item = autoTop6DecorateItem(safeHistory, raw, evaluatedCount);
    item.shapeScopeLength = scopeLength || 3;
    return item;
  }
  return null;
}

function promoteFrameworkBackupItem(item, label) {
  if (!item) return item;
  if ((item.side || "recommend") !== "backup") return item;
  const next = Object.assign({}, item, {
    side: "recommend",
    repairedIntoTop: true,
    derivedFrom: item.derivedFrom || (item.kind === "hewei" ? "hewei-backup-algorithm" : "position-backup-algorithm")
  });
  if (item.kind === "hewei") {
    next.name = String(item.name || "").replace(/备用$/, "推荐") || "千百十合尾推荐";
  } else if (item.kind === "position") {
    next.name = String(item.name || "").replace(/备用$/, "推荐") || `${label || "定位"}备用算法`;
  }
  return next;
}

function buildThreeDingVariantHelpers(prediction, history, options) {
  const opts = options && typeof options === "object" ? options : {};
  const comboWindow = Math.max(30, Math.min(MODEL_STABLE_WINDOW, Number(opts.comboWindow || MODEL_STABLE_WINDOW)));
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const avoidDigits = opts.avoidDigits instanceof Set ? opts.avoidDigits : new Set(Array.isArray(opts.avoidDigits) ? opts.avoidDigits : []);
  const avoidAnyTwoCodes = opts.avoidAnyTwoCodes instanceof Set
    ? opts.avoidAnyTwoCodes
    : new Set(Array.isArray(opts.avoidAnyTwoCodes) ? opts.avoidAnyTwoCodes : []);
  const avoidTwoDigitCodes = opts.avoidTwoDigitCodes instanceof Set
    ? opts.avoidTwoDigitCodes
    : new Set(Array.isArray(opts.avoidTwoDigitCodes) ? opts.avoidTwoDigitCodes : []);
  const preferredPositionSides = Array.isArray(opts.preferredPositionSides) && opts.preferredPositionSides.length
    ? opts.preferredPositionSides.slice()
    : ["recommend", "main", "backup"];
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
    const digit = frameworkSingleExcludeDigitToken(item);
    const avoidPenalty = digit && avoidDigits.has(digit) ? 500000 : 0;
    return full * 10000 + avg * 100 + Number(item.stableScore || 0) - avoidPenalty;
  }

  function commitItem(item) {
    if (!item) return false;
    const promoted = promoteFrameworkBackupItem(item, item.kind === "position" ? "定位" : "合尾");
    const id = autoTop6ItemId(promoted);
    if (used.has(id)) return false;
    if (frameworkConflictsSameExcludeDigit(chosen, promoted)) return false;
    if (frameworkConflictsSameHeweiDigit(chosen, promoted)) return false;
    used.add(id);
    chosen.push(promoted);
    return true;
  }

  function pickBest(pool, maxPerSameModel, allowAvoidDigit) {
    const modelCount = {};
    chosen.forEach((item) => {
      const modelKey = `${item.kind}:${item.key}`;
      modelCount[modelKey] = Number(modelCount[modelKey] || 0) + 1;
    });
    let best = null;
    let bestScore = -Infinity;
    (pool || []).forEach((item) => {
      if (!item) return;
      const trial = promoteFrameworkBackupItem(item, item.kind === "position" ? "定位" : "合尾");
      const id = autoTop6ItemId(trial);
      const modelKey = `${trial.kind}:${trial.key}`;
      if (used.has(id)) return;
      if (Number(modelCount[modelKey] || 0) >= Number(maxPerSameModel || 1)) return;
      if (frameworkConflictsSameExcludeDigit(chosen, trial)) return;
      if (frameworkConflictsSameHeweiDigit(chosen, trial)) return;
      const digit = frameworkSingleExcludeDigitToken(trial);
      if (!allowAvoidDigit && digit && avoidDigits.has(digit)) return;
      const score = comboPickScore(trial);
      if (score > bestScore || (score === bestScore && best && order(trial, best) < 0)) {
        best = trial;
        bestScore = score;
      }
    });
    if (!best) return false;
    return commitItem(best);
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

  function sidePool(source, side) {
    return (source || []).filter((item) => (item.side || "recommend") === side);
  }

  function pickPositions(needCount, label) {
    const positionPool = candidates.filter((item) => item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || ""))).sort(order);
    noteLow(positionPool, `${label}定位池`);
    const positionHealthy = healthy.filter((item) => item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || ""))).sort(order);
    let before = chosen.length;
    preferredPositionSides.forEach((side) => {
      while (chosen.length - before < needCount && pickBest(sidePool(positionHealthy, side), 1, false)) {}
    });
    preferredPositionSides.forEach((side) => {
      while (chosen.length - before < needCount && pickBest(sidePool(positionHealthy, side), 1, true)) {}
    });
    if (chosen.length - before < needCount) {
      preferredPositionSides.forEach((side) => {
        while (chosen.length - before < needCount && pickBest(sidePool(positionPool, side), 1, true)) {
          chosen[chosen.length - 1].repairedIntoTop = true;
        }
      });
      if (chosen.length - before >= needCount) repairReport.push(`${label}定位健康项不足，已用观察项/备用算法补齐（仅千百十，不含个位）。`);
    }
    // 候选同码/跨框避码抽干时，对齐原三定：强制补独立码，避免 B 快照 intermittent incomplete
    function forceFillMissingPosition() {
      const keyOrder = ["wan", "qian", "bai"];
      const usedKeys = new Set(
        chosen
          .filter((item) => item && item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || "")))
          .map((item) => String(item.key))
      );
      let filled = 0;
      while (chosen.length - before < needCount) {
        const missingKey = keyOrder.find((key) => !usedKeys.has(key));
        if (!missingKey) break;
        const taken = frameworkSingleExcludeDigits(chosen);
        // 优先错开跨框 avoidDigits，再回退任意未占用单码（同框仍禁同码）
        const digitOrder = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
        const preferList = digitOrder.filter((digit) => !avoidDigits.has(digit)).concat(digitOrder);
        let digit = null;
        for (let i = 0; i < preferList.length; i += 1) {
          const text = String(preferList[i]);
          if (/^\d$/.test(text) && !taken.has(text)) {
            digit = text;
            break;
          }
        }
        if (digit == null) break;
        const forced = autoTop6DecorateItem(safeHistory, {
          kind: "position",
          key: missingKey,
          side: "recommend",
          name: ({ wan: "千位", qian: "百位", bai: "十位" }[missingKey] || missingKey) + "强制独立码",
          code: digit,
          derivedFrom: "three-ding-force-unique-digit",
          repairedIntoTop: true
        }, evaluatedCount);
        if (!commitItem(forced)) break;
        usedKeys.add(missingKey);
        filled += 1;
        const avoided = avoidDigits.has(digit) ? "（仍触碰跨框避码）" : "";
        repairReport.push(`${label}${missingKey}定位候选撞码/不足，已强制错开为 ${digit}${avoided}`);
      }
      return filled;
    }
    if (chosen.length - before < needCount) {
      const filled = forceFillMissingPosition();
      if (filled > 0 && chosen.length - before >= needCount) {
        repairReport.push(`${label}定位已用强制独立码补齐至${needCount}项（仅千百十）。`);
      }
    }
    if (chosen.length - before < needCount) repairReport.push(`${label}定位正式项不足${needCount}个（仅千百十）。`);
    else {
      const sidesUsed = chosen.slice(before).filter((item) => item.kind === "position").map((item) => item.derivedFrom || item.side || "recommend");
      repairReport.push(`${label}定位算法侧：${sidesUsed.join("/") || preferredPositionSides[0]}`);
    }
  }

  function pickWanQianBai(label, preferredSides) {
    const sideOrder = Array.isArray(preferredSides) && preferredSides.length ? preferredSides.slice() : ["recommend", "main", "backup"];
    const heweiPool = candidates.filter((item) => item.kind === "hewei" && item.key === "wanQianBai").sort((a, b) => {
      const sa = sideOrder.indexOf(a.side || "recommend");
      const sb = sideOrder.indexOf(b.side || "recommend");
      if (sa !== sb) return (sa < 0 ? 99 : sa) - (sb < 0 ? 99 : sb);
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    });
    noteLow(heweiPool, `${label}合尾池`);
    const before = chosen.length;
    let ok = false;
    sideOrder.forEach((side) => {
      if (ok) return;
      if (pickBest(sidePool(heweiPool, side).filter((item) => item.stabilityGate), 1, false)) ok = true;
    });
    sideOrder.forEach((side) => {
      if (ok) return;
      if (pickBest(sidePool(heweiPool, side), 1, false)) ok = true;
    });
    if (!ok && pickBest(heweiPool, 1, true)) ok = true;
    if (!ok) {
      repairReport.push(`${label}千百十合尾缺失`);
      return;
    }
    if (chosen.length > before) {
      const picked = chosen[chosen.length - 1];
      if (String(picked.derivedFrom || "").includes("backup")) {
        repairReport.push(`${label}合尾改用备用算法码入正式项：${picked.name} ${picked.code}`);
      } else if (!picked.stabilityGate) {
        picked.repairedIntoTop = true;
        repairReport.push(`${label}合尾使用观察项：${picked.name} ${picked.code}`);
      }
    }
  }

  function pickAnyTwo(label, scopeIndexes) {
    const picked = pickStructuralAnyTwoHeweiHit(
      safeHistory,
      evaluatedCount,
      scopeIndexes,
      chosen,
      prediction,
      avoidAnyTwoCodes,
      resolveStableMathAssist(prediction)
    );
    if (!picked) {
      repairReport.push(`${label}固定模型缺失：anyTwoHeweiHit`);
      return;
    }
    if (avoidAnyTwoCodes.has(String(picked.code || ""))) {
      repairReport.push(`${label}二数合无可错开候选，回退 ${picked.code}`);
    } else {
      repairReport.push(`${label}二数合结构对齐：${picked.code}`);
    }
    const id = autoTop6ItemId(picked);
    if (!used.has(id)) {
      used.add(id);
      if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) picked.heweiScopeIndexes = scopeIndexes.slice();
      chosen.push(picked);
    }
  }

  function pickThreeDingTwoAnyTwo(label, scopeIndexes) {
    const picked = pickThreeDingTwoAnyTwoFromSeed(
      safeHistory,
      evaluatedCount,
      scopeIndexes,
      chosen,
      prediction,
      avoidAnyTwoCodes
    );
    if (!picked) {
      repairReport.push(`${label}固定模型缺失：anyTwoHeweiHit`);
      return;
    }
    const excluded = String(picked.excludedSeedDigit || "");
    repairReport.push(`${label}二数合02468排除${excluded || "一码"}：${picked.code}`);
    const id = autoTop6ItemId(picked);
    if (!used.has(id)) {
      used.add(id);
      if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) picked.heweiScopeIndexes = scopeIndexes.slice();
      chosen.push(picked);
    }
  }

  function pickTwoDigitAvoid(label) {
    const keys = ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"];
    const pool = candidates.filter((item) => item && item.kind === "extra" && keys.includes(String(item.key || "")) && /^\d{2}$/.test(String(item.code || ""))).sort((a, b) => {
      const ka = keys.indexOf(String(a.key || ""));
      const kb = keys.indexOf(String(b.key || ""));
      if (ka !== kb) return ka - kb;
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    });
    let picked = null;
    for (let index = 0; index < pool.length; index += 1) {
      const item = pool[index];
      const code = String(item.code || "");
      if (avoidTwoDigitCodes.has(code)) continue;
      const digits = code.split("");
      const overlap = digits.filter((digit) => avoidDigits.has(digit)).length;
      if (overlap >= 2) continue;
      picked = item;
      break;
    }
    if (!picked) {
      for (let index = 0; index < pool.length; index += 1) {
        const item = pool[index];
        const digits = String(item.code || "").split("");
        const overlap = digits.filter((digit) => avoidDigits.has(digit)).length;
        if (overlap >= 2) continue;
        picked = item;
        break;
      }
    }
    if (!picked) picked = pool[0] || null;
    if (!picked) {
      repairReport.push(`${label}固定模型缺失：twoDigitAvoid`);
      return;
    }
    if (avoidTwoDigitCodes.has(String(picked.code || ""))) {
      repairReport.push(`${label}对数无可错开候选，回退 ${picked.code}`);
    }
    if (picked.lowConfidence) {
      const rate = picked.safetyRate === null ? "样本不足" : `${Number(picked.safetyRate).toFixed(2)}%`;
      repairReport.push(`${label}对数避开观察项：${picked.name} ${picked.code}，安全率 ${rate}`);
      picked.repairedIntoTop = true;
    }
    if (picked.key !== "twoDigitAvoid") {
      const originalKey = String(picked.key || "");
      repairReport.push(`${label}对数改用独立组：${originalKey}=${picked.code}`);
      // 完整性口径统一认 twoDigitAvoid；保留原 key 到 derivedFrom 便于追溯
      picked = Object.assign({}, picked, {
        key: "twoDigitAvoid",
        name: "对数避开1组",
        derivedFrom: picked.derivedFrom || originalKey
      });
    } else {
      repairReport.push(`${label}一对数：${picked.code}`);
    }
    const id = autoTop6ItemId(picked);
    if (!used.has(id)) {
      used.add(id);
      chosen.push(picked);
    }
  }

  function pickReverseShape(label) {
    const assist = resolveStableMathAssist(prediction);
    const scopeLen = 3;
    // 更稳单双形态（全奇→单单单）已禁用；仅接受大小类更稳形态
    const stableFirst = pickStableMathShapeCandidate(assist, safeHistory, evaluatedCount, scopeLen);
    if (stableFirst && !isParityOnlyShapeCode(stableFirst.code)) {
      const id = autoTop6ItemId(stableFirst);
      if (!used.has(id)) {
        used.add(id);
        chosen.push(stableFirst);
        repairReport.push(`${label}更稳排除形态：${stableFirst.name} ${String(stableFirst.code || "").slice(0, scopeLen)}`);
        return;
      }
    }
    const reverseBase = chosen.filter((item) => item && item.kind === "position");
    const reversePool = autoTop6ReverseShapeCandidatesFromPositions(reverseBase, safeHistory)
      .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
      // 三定 A/B 排除形态禁用单双，优先/仅用大小形态
      .filter((item) => item && !isParityOnlyShapeCode(item.code) && (item.shapeKey === "shapeSize" || item.key === "reverseSize" || /^[大小]{3,4}$/.test(String(item.code || ""))))
      .sort(order);
    noteLow(reversePool, `${label}反向形态池`);
    const reverseHealthy = reversePool.filter((item) => item && item.stabilityGate === true);
    const source = reverseHealthy.length ? reverseHealthy : reversePool;
    let reversePicked = source.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0] || null;
    if (!reversePicked) {
      // 无大小形态时强制挂一个大小排除，保证 A/B 结构完整，绝不回退单单单
      const forced = autoTop6DecorateItem(safeHistory, {
        kind: "derivedReverseShape",
        key: "reverseSize",
        side: "recommend",
        name: "排除形态(大小)",
        code: "大大小",
        shapeKey: "shapeSize",
        shapeScopeLength: 3,
        mode: "avoid",
        derivedFrom: "three-ding-size-shape-fallback",
        repairedIntoTop: true
      }, evaluatedCount);
      reversePicked = forced;
      repairReport.push(`${label}排除形态暂缺，已强制大小形态大大小（禁用单双）。`);
    }
    if (!reversePicked.stabilityGate) {
      reversePicked.repairedIntoTop = true;
      repairReport.push(`${label}排除形态未达安全阈值，暂用最优观察项：${reversePicked.name} ${reversePicked.code}。`);
    } else {
      const attackShape = String(reversePicked.code || "").slice(0, 3);
      repairReport.push(`${label}排除形态：${reversePicked.name} ${attackShape}（千百十三字攻击语，仅大小形态）`);
    }
    const id = autoTop6ItemId(reversePicked);
    if (!used.has(id)) {
      used.add(id);
      chosen.push(reversePicked);
    }
  }

  return { chosen, repairReport, pickPositions, pickWanQianBai, pickAnyTwo, pickThreeDingTwoAnyTwo, pickTwoDigitAvoid, pickReverseShape };
}

function frameworkAnyTwoCodesFromItems(items) {
  const out = new Set();
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || item.kind !== "extra" || item.key !== "anyTwoHeweiHit") return;
    const code = String(item.code || "");
    if (isAnyTwoHeweiCode(code)) out.add(code);
  });
  return out;
}

function mergeStableMathAvoidDigits(baseAvoid, assist) {
  const out = baseAvoid instanceof Set
    ? new Set(baseAvoid)
    : new Set(Array.isArray(baseAvoid) ? baseAvoid : []);
  ((assist && assist.excludeDigits) || []).forEach((digit) => out.add(String(digit)));
  return out;
}

function threeDingTwoSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positions = formal.filter((item) => item && item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || "")));
  const uniquePositionKeys = new Set(positions.map((item) => String(item.key || "")));
  const heweiCount = formal.filter((item) => item && item.kind === "hewei" && item.key === "wanQianBai" && /^\d$/.test(String(item.code || ""))).length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape" && /^[大小]{3,4}$/.test(String(item.code || "")) && !/^[单双]{3,4}$/.test(String(item.code || ""))).length;
  const pairCount = formal.filter((item) => item && item.kind === "extra" && ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(String(item.key || "")) && /^\d{2}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot &&
    snapshot.version === THREE_DING_TWO_SNAPSHOT_VERSION &&
    formal.length === THREE_DING_TWO_ITEM_COUNT &&
    items.length === THREE_DING_TWO_ITEM_COUNT &&
    positions.length === 1 &&
    uniquePositionKeys.size === 1 &&
    heweiCount === 1 &&
    anyTwoCount === 1 &&
    reverseCount === 1 &&
    pairCount === 1
  );
}

function predictionSnapshotsAreImmutable(prediction) {
  return !!(
    prediction &&
    prediction.evaluated !== true &&
    prediction.snapshotsSealedAt &&
    prediction.snapshotsSealedBaseUid &&
    String(prediction.snapshotsSealedBaseUid) === String(prediction.baseUid || "")
  );
}

function attachStableMathAssistToSnapshot(snapshot, assist, label) {
  if (!snapshot || typeof snapshot !== "object" || !assist || !assist.ok) return snapshot;
  const lines = [];
  const digits = (assist.excludeDigits || []).length ? assist.excludeDigits.join("、") : "无";
  lines.push(`${label}挂载更稳排除单码：${digits}`);
  if ((assist.excludeShapes || []).length) lines.push(`${label}挂载更稳排除形态：${assist.excludeShapes.join("、")}`);
  if ((assist.excludeSpans || []).length) lines.push(`${label}挂载更稳排除跨度：${assist.excludeSpans.join("、")}`);
  if ((assist.excludeSumRanges || []).length) lines.push(`${label}挂载更稳排除和值：${assist.excludeSumRanges.join("、")}`);
  if ((assist.excludeRepeats || []).length) lines.push(`${label}挂载更稳排除重号：${assist.excludeRepeats.join("、")}`);
  if ((assist.excludeConsecutives || []).length) lines.push(`${label}挂载更稳排除连号：${assist.excludeConsecutives.join("、")}`);
  if ((assist.excludeParityRatios || []).length) lines.push(`${label}挂载更稳排除奇偶比：${assist.excludeParityRatios.join("、")}`);
  if ((assist.excludeSizeRatios || []).length) lines.push(`${label}挂载更稳排除大小比：${assist.excludeSizeRatios.join("、")}`);
  snapshot.stableMathAssist = {
    version: assist.version,
    latestPeriod: assist.latestPeriod,
    source: assist.source,
    excludeDigits: assist.excludeDigits.slice(),
    excludeShapes: assist.excludeShapes.slice(),
    excludeSpans: assist.excludeSpans.slice(),
    excludeSumRanges: assist.excludeSumRanges.slice(),
    excludeRepeats: (assist.excludeRepeats || []).slice(),
    excludeConsecutives: (assist.excludeConsecutives || []).slice(),
    excludeParityRatios: (assist.excludeParityRatios || []).slice(),
    excludeSizeRatios: (assist.excludeSizeRatios || []).slice(),
    summary: assist.summary
  };
  if (!Array.isArray(snapshot.repairReport)) snapshot.repairReport = [];
  lines.forEach((line) => {
    if (snapshot.repairReport.indexOf(line) < 0) snapshot.repairReport.push(line);
  });
  if (!snapshot.framework || typeof snapshot.framework !== "object") snapshot.framework = {};
  snapshot.framework.stableMathAssist = true;
  snapshot.framework.excludeAlgorithm = "model-aligned-stable-exclude";
  return snapshot;
}

function stableMathAssistPickBoost(assist, item) {
  if (!assist || !assist.ok || !item) return 0;
  let boost = 0;
  const digit = frameworkSingleExcludeDigitToken(item);
  if (digit && (assist.excludeDigits || []).includes(String(digit))) boost += 18000;
  const noDynamicDigits = !(assist.excludeDigits || []).length;
  if (noDynamicDigits) {
    if ((assist.excludeShapes || []).length && item.kind === "derivedReverseShape") boost += 15000;
    if ((assist.excludeSpans || []).length && item.key === "spanAvoid") boost += 12000;
    if ((assist.excludeSumRanges || []).length && item.key === "sumRangeAvoid") boost += 10000;
    if (item.derivedFrom === "stable-math-filter") boost += 8000;
  }
  return boost;
}

function compactAttackSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positions = formal.filter((item) => item && item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || "")));
  const heweiCount = formal.filter((item) => item && item.kind === "hewei" && item.key === "wanQianBai" && /^\d$/.test(String(item.code || ""))).length;
  const anyTwoCount = formal.filter((item) => {
    if (!item || item.kind !== "extra" || item.key !== "anyTwoHeweiHit") return false;
    return isAnyTwoHeweiCode(item.code);
  }).length;
  const uniquePositionKeys = new Set(positions.map((item) => String(item.key || "")));
  return !!(
    snapshot &&
    snapshot.version === COMPACT_ATTACK_SNAPSHOT_VERSION &&
    formal.length === COMPACT_ATTACK_ITEM_COUNT &&
    items.length === COMPACT_ATTACK_ITEM_COUNT &&
    positions.length === 3 &&
    uniquePositionKeys.size === 3 &&
    ["wan", "qian", "bai"].every((key) => uniquePositionKeys.has(key)) &&
    heweiCount === 1 &&
    anyTwoCount === 1 &&
    formal.every((item) => !(item && item.kind === "derivedReverseShape")) &&
    formal.every((item) => !(item && item.kind === "shape"))
  );
}

function recommend2SnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  // 正式项不含 side=backup；替补只进定位看板，不能冒充 2定位/2合尾
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const heweiCount = formal.filter((item) => item && item.kind === "hewei").length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape").length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  // 二数合固定为 02468 中排除 1 个的 4 码；三数合仍为 5 码。
  const anyThreeCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  const pairAvoidCount = formal.filter((item) => item && item.kind === "extra" && ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(item.key)).length;
  return !!(
    snapshot &&
    snapshot.version === RECOMMEND2_SNAPSHOT_VERSION &&
    formal.length === 8 &&
    items.length === 8 &&
    positionCount === 2 &&
    heweiCount === 2 &&
    reverseCount === 1 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1 &&
    pairAvoidCount === 1
  );
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

const stableMathFilterBridge = require("../stable_math_filter_bridge");

let state = null;

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

function recentExactCodeWindowFromKey(key, fallback) {
  const text = `${String(key || "")} ${String(fallback === undefined || fallback === null ? "" : fallback)}`;
  if (/recentExactCodeAvoidMax/i.test(text) || /最大|全部|MAX/i.test(text)) return Math.max(1, MAX_HISTORY || 5000);
  const fromKey = String(key || "").match(/recentExactCodeAvoid(\d+)/);
  if (fromKey) return Math.max(1, Number(fromKey[1]));
  const fromFallback = String(fallback === undefined || fallback === null ? "" : fallback).match(/(\d+)/);
  if (fromFallback) return Math.max(1, Number(fromFallback[1]));
  return 500;
}

const RECENT_EXACT_CODE_MAX_KEY = "recentExactCodeAvoidMax";

function recentExactCodeCodeLimit(entry) {
  if (entry && String(entry.key || "") === RECENT_EXACT_CODE_MAX_KEY) return Math.max(1, Number(entry.codeCount || MAX_HISTORY || 5000));
  return recentExactCodeWindowFromKey(entry && entry.key, entry && (entry.value || entry.code || entry.primary || entry.backup));
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

const HEWEI_UNIFIED_GUARD_RATE = [];

const HEWEI_GUARD_RATE = [];

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
    if (saved && typeof saved === "object" && Array.isArray(saved.codes)) {
      extraItem.codes = saved.codes.slice(0, recentExactCodeCodeLimit({ key: extra.key, value: code }));
      extraItem.codeCount = Number(saved.codeCount || extraItem.codes.length);
    }
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
    if (/^recentExactCodeAvoid(?:\d+|Max)$/.test(String(item.key || ""))) {
      const actualCode = firstFourCode(record);
      if (!/^\d{4}$/.test(actualCode)) return null;
      // 优先用 tip 内冻结 codes；已 omit 时按条目 lookback 对「该历史行之前」的窗口重建（回测）
      let codeSet = null;
      if (Array.isArray(item.codes) && item.codes.length) {
        codeSet = new Set(item.codes.map(String).filter((value) => /^\d{4}$/.test(value)));
      } else if (item.codesOmitted || Number(item.codeCount || 0) > 0 || item.lookback != null) {
        const lookback = recentExactCodeWindowFromKey(item.key, item.value || item.code || item.lookback);
        const history = (typeof state !== "undefined" && state && Array.isArray(state.history)) ? state.history : [];
        const idx = history.indexOf(record);
        const previousRows = idx >= 0 ? history.slice(idx + 1) : history;
        codeSet = recentExactCodeSet(previousRows, lookback);
      }
      if (!codeSet || !codeSet.size) return null;
      return !codeSet.has(actualCode);
    }
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

function autoTop6SnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const heweiCount = formal.filter((item) => item && item.kind === "hewei").length;
  const pairHitCount = formal.filter((item) => item && item.kind === "extra" && item.key === "twoDigitHit").length;
  const reverseCount = items.filter((item) => item && item.kind === "derivedReverseShape").length;
  const reverseAsBackup = items.filter((item) => item && item.kind === "derivedReverseShape" && item.side === "backup").length;
  return !!(
    snapshot &&
    snapshot.version === AUTO_TOP6_SNAPSHOT_VERSION &&
    positionCount === 3 &&
    heweiCount === 2 &&
    pairHitCount === 1 &&
    formal.length === 6 &&
    reverseCount <= 1 &&
    reverseCount === reverseAsBackup &&
    items.length === formal.length + reverseCount
  );
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

function buildAutoTop6Snapshot(prediction, history) {
  const state = getState();
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(MODEL_STABLE_WINDOW, safeHistory.length));
  let targetItemCount = 6;

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
    let nearHit = 0;
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
      if (results.length > 1 && wins >= results.length - 1) nearHit += 1;
    });
    return {
      total,
      hit,
      nearHit,
      rate: total ? hit * 100 / total : null,
      nearRate: total ? nearHit * 100 / total : null,
      avgRate: validChecks ? successChecks * 100 / validChecks : null
    };
  }

  function comboPickScore(item) {
    const stats = comboStats(chosen.concat([item]));
    const full = stats.rate === null ? -1 : Number(stats.rate || 0);
    const near = stats.nearRate === null ? full : Number(stats.nearRate || 0);
    const avg = stats.avgRate === null ? 0 : Number(stats.avgRate || 0);
    return full * 10000 + near * 100 + avg + Number(item.stableScore || 0) / 100;
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
      if (Number(modelCount[modelKey] || 0) >= Number(maxPerSameModel || 2)) return;
      if (frameworkConflictsSameHeweiDigit(chosen, item)) return;
      // 定位/合尾单码同框禁止相碰；无替代时才允许放宽
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

  const positionPool = candidates.filter((item) => item.kind === "position");
  const heweiPool = candidates.filter((item) => item.kind === "hewei");
  noteLow(positionPool, "定位池");
  noteLow(heweiPool, "合尾池");

  let before = chosen.length;
  const formalPositionHealthy = healthy.filter((item) => item.kind === "position" && item.side !== "backup");
  const formalPositionPool = positionPool.filter((item) => item.side !== "backup");
  while (chosen.length - before < 3 && pickBest(formalPositionHealthy.slice().sort(order), 2, false)) {}
  while (chosen.length - before < 3 && pickBest(formalPositionPool.slice().sort(order), 2, false)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`定位池安全阈值内不足，明确加入观察项：${item.name} ${item.code}。`);
  }
  while (chosen.length - before < 3 && pickBest(formalPositionPool.slice().sort(order), 2, true)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`定位池无错开同码替代，被迫保留同码：${item.name} ${item.code}。`);
  }
  if (chosen.length - before < 3) repairReport.push(`定位池结构不完整：需要3项正式排除（不含替补），实际${chosen.length - before}项。`);

  const reversePool = autoTop6ReverseShapeCandidatesFromPositions(chosen.filter((item) => item.kind === "position"), safeHistory)
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(order);
  noteLow(reversePool, "反向形态池");
  const healthyReverse = reversePool.filter((item) => item && item.stabilityGate === true);
  if (healthyReverse.length) {
    const picked = healthyReverse.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
    if (!used.has(autoTop6ItemId(picked))) {
      // 反向形态只作参考（side=backup）：不进正式六项全过、不进攻击语；正式结构恒为 3+2+1=6
      picked.side = "backup";
      picked.name = String(picked.name || "反向形态") + "（参考）";
      used.add(autoTop6ItemId(picked));
      chosen.push(picked);
      targetItemCount = 7;
      repairReport.push(`反向形态作参考项保留：${picked.code}；不参与六项全过统计。`);
    }
  } else if (reversePool.length) {
    repairReport.push("反向形态池两路均未达安全阈值，暂不计入本期自动推荐。");
  }

  before = chosen.length;
  const formalHeweiHealthy = healthy.filter((item) => item.kind === "hewei" && item.side !== "backup");
  const formalHeweiPool = heweiPool.filter((item) => item.side !== "backup");
  while (chosen.length - before < 2 && pickBest(formalHeweiHealthy.slice().sort(order), 2, false)) {}
  while (chosen.length - before < 2 && pickBest(formalHeweiPool.slice().sort(order), 2, false)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`合尾池安全阈值内不足，明确加入观察项：${item.name} ${item.code}。`);
  }
  while (chosen.length - before < 2 && pickBest(formalHeweiPool.slice().sort(order), 2, true)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`合尾与定位同码相碰无替代，被迫保留同码：${item.name} ${item.code}。`);
  }
  if (chosen.length - before < 2) repairReport.push(`合尾池结构不完整：需要2项正式排除（不含替补），实际${chosen.length - before}项。`);

  // 第6项固定为两数共现除对数，绝不写成含对数；也不用对数避开1/2/3组冒充
  const cooccur = candidates
    .filter((item) => item.kind === "extra" && item.key === "twoDigitHit")
    .sort(order);
  noteLow(cooccur, "两数共现池");
  const healthyCooccur = cooccur.filter((item) => item && item.stabilityGate === true);
  if (cooccur.length) {
    const source = healthyCooccur.length ? healthyCooccur : cooccur;
    const picked = source.slice().sort((a, b) => {
      const overlapDelta = frameworkTwoDigitOverlapWithSingles(chosen, a) - frameworkTwoDigitOverlapWithSingles(chosen, b);
      if (overlapDelta) return overlapDelta;
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
    picked.name = "两数共现";
    picked.mode = "avoid";
    const overlap = frameworkTwoDigitOverlapWithSingles(chosen, picked);
    if (overlap > 0) {
      repairReport.push(`两数共现 ${picked.code} 与已选单码重叠${overlap}位，候选池无更优错开项时保留。`);
    }
    if (!picked.stabilityGate) {
      picked.repairedIntoTop = true;
      repairReport.push(`两数共现未达安全阈值，仍保留第6项除对数：${picked.code}；不改写成含对数。`);
    }
    if (!used.has(autoTop6ItemId(picked))) {
      used.add(autoTop6ItemId(picked));
      chosen.push(picked);
    }
  } else {
    repairReport.push("两数共现模型缺失，当前快照保持不完整并交由完整性巡检重建，不用对数避开1/2/3组冒充补位。");
  }
  if (chosen.length < targetItemCount) repairReport.push(`框架结构不足${targetItemCount}项，当前仅生成 ${chosen.length} 项；完整性巡检会拒绝并重建，不以无关模型补位。`);

  return {
    version: AUTO_TOP6_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, targetItemCount),
    baseItemCount: 6,
    reverseShapeCount: chosen.some((item) => item.kind === "derivedReverseShape") ? 1 : 0,
    formalItemCount: 6,
    framework: {
      positionCount: 3,
      heweiCount: 2,
      pairHitCount: 1,
      reverseShapeCount: chosen.some((item) => item.kind === "derivedReverseShape") ? 1 : 0,
      semantics: "formal-6=3pos+2hewei+1cooccur-avoid; reverse-shape=backup-reference-only"
    },
    items: chosen.slice(0, targetItemCount).map((item, index) => ({
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
      codeCount: item.codeCount,
      codes: Array.isArray(item.codes) ? item.codes.slice(0, recentExactCodeCodeLimit(item)) : undefined,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || ""
    })),
    repairReport
  };
}

function ensurePredictionAutoTop6Snapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (autoTop6SnapshotIsComplete(prediction.autoTop6)) return prediction.autoTop6;
  prediction.autoTop6 = buildAutoTop6Snapshot(prediction, history);
  prediction.autoTop6Items = prediction.autoTop6.items;
  prediction.autoTop6RepairReport = prediction.autoTop6.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.autoTop6SnapshotVersion = prediction.autoTop6.version;
  return prediction.autoTop6;
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
      codeCount: item.codeCount,
      codes: Array.isArray(item.codes) ? item.codes.slice(0, recentExactCodeCodeLimit(item)) : undefined,
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

function frameworkTwoDigitOverlapWithSingles(chosen, item) {
  if (!item) return 0;
  const code = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  if (code.length < 2) return 0;
  const singles = frameworkSingleExcludeDigits(chosen);
  if (!singles.size) return 0;
  let overlap = 0;
  const seen = new Set();
  for (const ch of code) {
    if (seen.has(ch)) continue;
    seen.add(ch);
    if (singles.has(ch)) overlap += 1;
  }
  return overlap;
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
  digits.forEach((digit) => {
    if (keep.has(digit)) score += 14;
    else if (kills.has(digit)) score -= 100;
    if (prefer.has(digit)) score += 30;
  });
  score += digits.filter((digit) => keep.has(digit)).length * 3;
  score -= digits.filter((digit) => kills.has(digit)).length * 40;
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
    source: prediction && prediction.stableMathFilter ? "prediction" : "cache"
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
  if (item && item.kind === "extra" && key === "threeDigitHit") return 5;
  // 高基线模型不用 85% 原始通过率冒充「健康/强预测」
  if (item && item.kind === "extra" && isHighBaselineScoreKey(key)) {
    const meta = highBaselineScoreMeta(key);
    return Math.max(0, Number(meta && meta.baseline || 0) - 3);
  }
  return MODEL_LOW_HIT_RATE;
}

function autoTop6LongFloor(item, threshold) {
  if (item && item.kind === "hewei" && item.key === "unified") return Math.max(55, Number(threshold || 0) - 6);
  if (item && item.kind === "extra" && item.key === "threeDigitHit") return Math.max(0, Number(threshold || 0) - 4);
  return Math.max(70, Number(threshold || 0) - AUTO_TOP6_LONG_FLOOR_OFFSET);
}

function autoTop6LowerFloor(item, threshold) {
  if (item && item.kind === "hewei" && item.key === "unified") return Math.max(50, Number(threshold || 0) - 14);
  if (item && item.kind === "extra" && item.key === "threeDigitHit") return 0;
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



function buildRecommend2Snapshot(prediction, history) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(MODEL_STABLE_WINDOW, safeHistory.length));

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
  noteLow(positionPool, "推荐2定位池");
  noteLow(heweiPool, "推荐2合尾池");

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
      repairReport.push(`推荐2定位健康项不足，已用正式观察项补齐：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, true)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
      repairReport.push(`推荐2定位同码相碰无替代，被迫保留：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`推荐2定位正式项不足2个（不含替补），当前定位启用 ${chosen.length - before} 个。`);

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
      repairReport.push(`推荐2合尾健康项不足，已用正式观察项补齐：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiPool, 2, true)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
      repairReport.push(`推荐2合尾与定位同码相碰无替代，被迫保留：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`推荐2合尾正式项不足2个（不含替补），当前合尾启用 ${chosen.length - before} 个。`);

  const reverseBase = chosen.filter((item) => item.kind === "position");
  const reversePool = autoTop6ReverseShapeCandidatesFromPositions(reverseBase, safeHistory)
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(order);
  noteLow(reversePool, "推荐2反向形态池");
  const reverseHealthy = reversePool.filter((item) => item && item.stabilityGate === true);
  let reversePicked = null;
  if (reverseHealthy.length) {
    reversePicked = reverseHealthy.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
  } else if (reversePool.length) {
    reversePicked = reversePool.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
    if (reversePicked) {
      reversePicked.repairedIntoTop = true;
      repairReport.push(`推荐2反向形态未达安全阈值，暂用最优观察项：${reversePicked.name} ${reversePicked.code}。`);
    }
  }
  if (reversePicked && !used.has(autoTop6ItemId(reversePicked))) {
    used.add(autoTop6ItemId(reversePicked));
    chosen.push(reversePicked);
  } else if (!reversePicked) {
    repairReport.push("推荐2反向形态暂缺，等待定位项稳定后自动恢复。");
  }

  ["anyTwoHeweiHit", "anyThreeHeweiHit"].forEach((extraKey) => {
    const pool = candidates.filter((item) => item && item.kind === "extra" && item.key === extraKey).sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    });
    const picked = pool[0] || null;
    if (!picked) {
      repairReport.push(`推荐2固定模型缺失：${extraKey}`);
      return;
    }
    if (picked.lowConfidence) {
      const rate = picked.safetyRate === null ? "样本不足" : `${Number(picked.safetyRate).toFixed(2)}%`;
      repairReport.push(`推荐2固定加入观察项：${picked.name} ${picked.code}，安全率 ${rate}${picked.stabilityReason ? "，原因 " + picked.stabilityReason : ""}`);
    }
    if (!used.has(autoTop6ItemId(picked))) {
      used.add(autoTop6ItemId(picked));
      chosen.push(picked);
    }
  });

  const pairAvoidKeys = ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"];
  const pairAvoidPool = candidates
    .filter((item) => item && item.kind === "extra" && pairAvoidKeys.includes(item.key))
    .sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    });
  const pairAvoidSource = pairAvoidPool[0] || null;
  if (!pairAvoidSource) {
    repairReport.push("推荐2固定模型缺失：对数2位避开");
  } else {
    const pairAvoidPicked = Object.assign({}, pairAvoidSource, { name: "对数2位避开" });
    if (pairAvoidPicked.lowConfidence) {
      const rate = pairAvoidPicked.safetyRate === null ? "样本不足" : `${Number(pairAvoidPicked.safetyRate).toFixed(2)}%`;
      repairReport.push(`推荐2固定加入观察项：${pairAvoidPicked.name} ${pairAvoidPicked.code}，安全率 ${rate}${pairAvoidPicked.stabilityReason ? "，原因 " + pairAvoidPicked.stabilityReason : ""}`);
    }
    if (!used.has(autoTop6ItemId(pairAvoidPicked))) {
      used.add(autoTop6ItemId(pairAvoidPicked));
      chosen.push(pairAvoidPicked);
    }
  }

  return {
    version: RECOMMEND2_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, 8),
    framework: {
      positionCount: 2,
      heweiCount: 2,
      reverseShapeCount: 1,
      heweiComboCount: 2,
      pairAvoidCount: 1
    },
    items: chosen.slice(0, 8).map((item, index) => ({
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
      codeCount: item.codeCount,
      codes: Array.isArray(item.codes) ? item.codes.slice(0, recentExactCodeCodeLimit(item)) : undefined,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || ""
    })),
    repairReport
  };
}

function ensurePredictionRecommend2Snapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (recommend2SnapshotIsComplete(prediction.recommend2)) return prediction.recommend2;
  prediction.recommend2 = buildRecommend2Snapshot(prediction, history);
  prediction.recommend2Items = prediction.recommend2.items;
  prediction.recommend2RepairReport = prediction.recommend2.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.recommend2SnapshotVersion = prediction.recommend2.version;
  return prediction.recommend2;
}

function buildCompactAttackSnapshot(prediction, history, options) {
  // 三定框架 v9：千百十合尾1码 + 千/百/十各除1码（定位加强）+ 二数合02468排1码；同框禁同码；不挂形态/单双
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
  const assist = resolveStableMathAssist(prediction);
  const preferExcludeDigits = new Set((assist && assist.ok && Array.isArray(assist.excludeDigits)) ? assist.excludeDigits.map(String) : []);

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.hardnessScore || 0) !== Number(a.hardnessScore || 0)) return Number(b.hardnessScore || 0) - Number(a.hardnessScore || 0);
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

  function positionStrengthScore(item) {
    // 定位加强：健康门 + 硬度 + 更稳排除码优先 + 近窗命中 + 无动态单码时形态/跨度优先
    const digit = frameworkSingleExcludeDigitToken(item);
    const gateBoost = item && item.stabilityGate ? 12000 : 0;
    const hardnessBoost = Number(item && item.hardnessScore || 0) * 40;
    const preferBoost = digit && preferExcludeDigits.has(digit) ? 18000 : 0;
    const stableBoost = stableMathAssistPickBoost(assist, item);
    const safetyBoost = Number(item && item.safetyRate || 0) * 20;
    const missPenalty = Number(item && item.recentMissStreak || 0) * 80;
    return gateBoost + hardnessBoost + preferBoost + stableBoost + safetyBoost - missPenalty;
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

  function promoteIfBackup(item, kindLabel) {
    if (!item) return item;
    if ((item.side || "recommend") !== "backup") return item;
    const next = Object.assign({}, item, {
      side: "recommend",
      repairedIntoTop: true,
      derivedFrom: item.derivedFrom || (item.kind === "hewei" ? "hewei-backup-algorithm" : "position-backup-algorithm")
    });
    if (item.kind === "hewei") {
      next.name = String(item.name || "").replace(/备用$/, "推荐") || "千百十合尾推荐";
    } else {
      next.name = String(item.name || "").replace(/备用$/, "推荐") || `${kindLabel || "定位"}备用算法`;
    }
    return next;
  }

  function commitItem(item) {
    if (!item) return false;
    const id = autoTop6ItemId(item);
    if (used.has(id)) return false;
    if (frameworkConflictsSameExcludeDigit(chosen, item)) return false;
    if (frameworkConflictsSameHeweiDigit(chosen, item)) return false;
    used.add(id);
    chosen.push(item);
    return true;
  }

  // 1) 先定千百十合尾，再选定位，避免合尾码占满后定位撞死
  const heweiPool = candidates.filter((item) => item.kind === "hewei" && item.key === "wanQianBai").sort(order);
  noteLow(heweiPool, "三定框架合尾池");
  const heweiSideOrder = ["recommend", "main", "backup"];
  let heweiPicked = false;
  function tryPickHewei(requireGate, allowConflict) {
    for (let s = 0; s < heweiSideOrder.length; s += 1) {
      const side = heweiSideOrder[s];
      const pool = heweiPool.filter((item) => (item.side || "recommend") === side && (!requireGate || item.stabilityGate)).sort(order);
      let best = null;
      let bestScore = -Infinity;
      pool.forEach((raw) => {
        const item = promoteIfBackup(raw, "合尾");
        const id = autoTop6ItemId(item);
        if (used.has(id)) return;
        if (!allowConflict && frameworkConflictsSameExcludeDigit(chosen, item)) return;
        if (!allowConflict && frameworkConflictsSameHeweiDigit(chosen, item)) return;
        const score = comboPickScore(item) + positionStrengthScore(item) - s * 30;
        if (score > bestScore || (score === bestScore && best && order(item, best) < 0)) {
          best = item;
          bestScore = score;
        }
      });
      if (best && commitItem(best)) {
        heweiPicked = true;
        return true;
      }
    }
    return false;
  }
  if (!tryPickHewei(true, false)) tryPickHewei(false, false);
  if (!heweiPicked && tryPickHewei(false, true)) {
    repairReport.push("三定框架千百十合尾放宽同码限制补齐");
  }
  if (!heweiPicked) repairReport.push("三定框架千百十合尾缺失");
  else {
    const hw = chosen.find((item) => item && item.kind === "hewei" && item.key === "wanQianBai");
    if (hw) repairReport.push(`三定框架千百十合尾：${hw.code}${hw.stabilityGate ? "" : "（观察项）"}`);
  }

  // 2) 定位加强：优先备用/主攻算法侧 + 健康门 + 硬度 + 更稳排除码
  const positionPool = candidates.filter((item) => item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || ""))).sort(order);
  noteLow(positionPool, "三定框架定位池");
  void opts.allowUnhealthy;
  const positionHealthy = healthy.filter((item) => item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || ""))).sort(order);
  const sideOrder = ["backup", "main", "recommend"];
  if (preferExcludeDigits.size) {
    repairReport.push(`三定框架定位加强：更稳排除码优先 ${Array.from(preferExcludeDigits).join("")}`);
  } else if ((assist.excludeShapes || []).length || (assist.excludeSpans || []).length) {
    repairReport.push(`三定框架定位加强：更稳形态/跨度辅助（单码空：形态${(assist.excludeShapes || []).slice(0, 2).join("、") || "无"}，跨度${(assist.excludeSpans || []).slice(0, 3).join("、") || "无"}）`);
  } else {
    repairReport.push("三定框架定位加强：优先备用/主攻算法侧与硬度分");
  }

  function pickBestForKey(key, allowConflict) {
    const pools = [
      positionHealthy.filter((item) => String(item.key) === key),
      positionPool.filter((item) => String(item.key) === key)
    ];
    for (let p = 0; p < pools.length; p += 1) {
      for (let s = 0; s < sideOrder.length; s += 1) {
        const side = sideOrder[s];
        const pool = pools[p].filter((item) => (item.side || "recommend") === side).sort(order);
        let best = null;
        let bestScore = -Infinity;
        pool.forEach((raw) => {
          const item = promoteIfBackup(raw, "定位");
          const id = autoTop6ItemId(item);
          if (used.has(id)) return;
          if (!allowConflict && frameworkConflictsSameExcludeDigit(chosen, item)) return;
          const score = comboPickScore(item) + positionStrengthScore(item) - s * 20 - p * 1000;
          if (score > bestScore || (score === bestScore && best && order(item, best) < 0)) {
            best = item;
            bestScore = score;
          }
        });
        if (best && commitItem(best)) return true;
      }
    }
    return false;
  }
  function forceFillMissingKey(key) {
    const taken = frameworkSingleExcludeDigits(chosen);
    let digit = null;
    // 强制补位时也优先更稳排除码
    const preferList = Array.from(preferExcludeDigits).concat(["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]);
    for (let i = 0; i < preferList.length; i += 1) {
      const text = String(preferList[i]);
      if (/^\d$/.test(text) && !taken.has(text)) {
        digit = text;
        break;
      }
    }
    if (digit == null) return false;
    const forced = autoTop6DecorateItem(safeHistory, {
      kind: "position",
      key,
      side: "recommend",
      name: ({ wan: "千位", qian: "百位", bai: "十位" }[key] || key) + "强制独立码",
      code: digit,
      derivedFrom: "compact-force-unique-digit",
      repairedIntoTop: true
    }, evaluatedCount);
    if (!commitItem(forced)) return false;
    repairReport.push(`三定框架${key}定位与同框撞码，已强制错开为 ${digit}`);
    return true;
  }

  // 固定按千/百/十各取1项，禁止只凑到2位就停
  ["wan", "qian", "bai"].forEach((key) => {
    if (pickBestForKey(key, false)) return;
    if (pickBestForKey(key, true)) {
      repairReport.push(`三定框架${key}定位放宽同码限制补齐`);
      return;
    }
    forceFillMissingKey(key);
  });
  const posCount = chosen.filter((item) => item && item.kind === "position").length;
  if (posCount < 3) repairReport.push("三定框架定位正式项不足千百十三项（不含个位）。");
  else {
    const sidesUsed = chosen.filter((item) => item && item.kind === "position").map((item) => item.derivedFrom || item.side || "recommend");
    repairReport.push(`三定框架三项定位已固定落到千百十（加强选码：${sidesUsed.join("/")}）。`);
  }

  // 3) 二数合02468排1码：结构对齐合尾/定位/Codex。
  const scopeForRank = heweiScopeIndexesFromItems(chosen);
  let picked = pickStructuralAnyTwoHeweiHit(safeHistory, evaluatedCount, scopeForRank, chosen, prediction, new Set(), assist);
  // 二数合正式规则固定为 02468 中排除 1 个，因此允许全双；只拒绝不符合四码规则的异常值。
  if (picked && !isAnyTwoHeweiCode(picked.code)) {
    repairReport.push(`三定框架二数合格式异常：${picked.code}`);
    picked = null;
  }
  if (!picked) {
    repairReport.push("三定框架固定模型缺失：anyTwoHeweiHit");
  } else {
    repairReport.push(`三定框架二数合结构对齐：${picked.code}`);
    commitItem(picked);
  }

  // 三定框架不再挂单双/大小形态项（曾误挂「单单单」且 3 字码无法结算，导致连中冻住）
  repairReport.push("三定框架已禁用排除形态（含单双形态），仅保留定位+合尾+二数合");

  const heweiScopeIndexes = heweiScopeIndexesFromItems(chosen);
  if (heweiScopeIndexes.length === 3) {
    repairReport.push(`三定框架二数合作用位：${heweiScopeIndexes.map((index) => ["千", "百", "十", "个"][index] || index).join("")}`);
  }

  // 输出顺序：3定位 → 千百十合 → 二数合（面板展示更直观）
  const ordered = [];
  ["wan", "qian", "bai"].forEach((key) => {
    const item = chosen.find((row) => row && row.kind === "position" && String(row.key) === key);
    if (item) ordered.push(item);
  });
  chosen.forEach((item) => {
    if (item && item.kind === "hewei" && item.key === "wanQianBai") ordered.push(item);
  });
  chosen.forEach((item) => {
    if (item && item.kind === "extra" && item.key === "anyTwoHeweiHit") ordered.push(item);
  });
  chosen.forEach((item) => {
    if (ordered.indexOf(item) < 0) ordered.push(item);
  });

  return {
    version: COMPACT_ATTACK_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(ordered.length, COMPACT_ATTACK_ITEM_COUNT),
    framework: {
      positionCount: 3,
      heweiComboCount: 1,
      shapeParityCount: 0,
      heweiCount: 1,
      pool: "qianBaiShi",
      pickOrder: ["hewei.wanQianBai", "positionx3-strengthened", "anyTwoHeweiHit-mixedParity"],
      positionSidePreference: sideOrder.slice(),
      heweiScopeIndexes: heweiScopeIndexes.length >= 2 ? heweiScopeIndexes.slice() : undefined
    },
    heweiScopeIndexes: heweiScopeIndexes.length >= 2 ? heweiScopeIndexes.slice() : undefined,
    items: ordered.slice(0, COMPACT_ATTACK_ITEM_COUNT).map((item, index) => ({
      rank: index + 1,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      mode: item.mode || (item.kind === "extra" && /Hit$/.test(String(item.key || "")) ? "hit" : "avoid"),
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
      codeCount: item.codeCount,
      codes: Array.isArray(item.codes) ? item.codes.slice(0, recentExactCodeCodeLimit(item)) : undefined,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || "",
      heweiScopeIndexes: (item.key === "anyTwoHeweiHit" || item.key === "anyThreeHeweiHit") && heweiScopeIndexes.length >= 2
        ? heweiScopeIndexes.slice()
        : undefined
    })),
    repairReport
  };
}

function ensurePredictionCompactAttackSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  const assist = resolveStableMathAssist(prediction);
  if (
    compactAttackSnapshotIsComplete(prediction.compactAttack)
    && prediction.compactAttack.stableMathAssist
    && !snapshotNeedsStableMathAssistRefresh(prediction.compactAttack, assist)
  ) {
    return prediction.compactAttack;
  }
  prediction.compactAttack = buildCompactAttackSnapshot(prediction, history);
  attachStableMathAssistToSnapshot(prediction.compactAttack, assist, "三定框架");
  prediction.compactAttackItems = prediction.compactAttack.items;
  prediction.compactAttackRepairReport = prediction.compactAttack.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.compactAttackSnapshotVersion = prediction.compactAttack.version;
  return prediction.compactAttack;
}

function buildThreeDingTwoSnapshot(prediction, history, options) {
  // 三定框架A：千百十合尾 + 千百十挑1位除1码 + 二数合(千百十) + 排除形态1 + 一对数避开
  const ctx = buildThreeDingVariantHelpers(prediction, history, Object.assign({
    preferredPositionSides: ["backup", "main", "recommend"]
  }, options || {}));
  ctx.pickWanQianBai("三定框架A", ["recommend", "main", "backup"]);
  // 合尾入框后再选定位，避免备用定位码占满合尾可用码
  ctx.pickPositions(1, "三定框架A");
  ctx.pickReverseShape("三定框架A");
  ctx.chosen.forEach((item) => {
    if (item && item.kind === "derivedReverseShape") item.shapeScopeLength = 3;
  });
  const heweiScopeIndexes = [0, 1, 2];
  ctx.repairReport.push("三定框架A二数合作用位：千百十");
  ctx.pickThreeDingTwoAnyTwo("三定框架A", heweiScopeIndexes);
  ctx.pickTwoDigitAvoid("三定框架A");
  return {
    version: THREE_DING_TWO_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(ctx.chosen.length, THREE_DING_TWO_ITEM_COUNT),
    framework: {
      positionCount: 1,
      heweiCount: 1,
      heweiComboCount: 1,
      reverseShapeCount: 1,
      pairAvoidCount: 1,
      pool: "qianBaiShi",
      pickOrder: ["hewei.wanQianBai", "positionx1", "derivedReverseShape", "anyTwoHeweiHit", "twoDigitAvoid"],
      positionSidePreference: ["backup", "main", "recommend"],
      heweiScopeIndexes: heweiScopeIndexes.slice()
    },
    heweiScopeIndexes: heweiScopeIndexes.slice(),
    items: mapFrameworkSnapshotItems(ctx.chosen, heweiScopeIndexes, THREE_DING_TWO_ITEM_COUNT),
    repairReport: ctx.repairReport
  };
}

function ensurePredictionThreeDingTwoSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (predictionSnapshotsAreImmutable(prediction)) return prediction.threeDingTwo || null;
  const assist = resolveStableMathAssist(prediction);
  if (
    threeDingTwoSnapshotIsComplete(prediction.threeDingTwo)
    && prediction.threeDingTwo.stableMathAssist
    && !snapshotNeedsStableMathAssistRefresh(prediction.threeDingTwo, assist)
  ) {
    return prediction.threeDingTwo;
  }
  // A 与原三定/更稳算法独立选码：避开原三定已选单码 + 更稳排除单码
  const avoidDigits = mergeStableMathAvoidDigits(
    frameworkSingleExcludeDigits(prediction.compactAttack && prediction.compactAttack.items),
    assist
  );
  const avoidAnyTwoCodes = frameworkAnyTwoCodesFromItems(prediction.compactAttack && prediction.compactAttack.items);
  prediction.threeDingTwo = buildThreeDingTwoSnapshot(prediction, history, { avoidDigits, avoidAnyTwoCodes });
  attachStableMathAssistToSnapshot(prediction.threeDingTwo, assist, "三定框架A");
  prediction.threeDingTwoItems = prediction.threeDingTwo.items;
  prediction.threeDingTwoRepairReport = prediction.threeDingTwo.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.threeDingTwoSnapshotVersion = prediction.threeDingTwo.version;
  return prediction.threeDingTwo;
}

function buildThreeDingOneSnapshot(prediction, history, options) {
  // 三定框架B：先合尾(优先备用)，再从千百十挑2位避开合尾同码 + 二数合(千百十) + 对数避开
  const ctx = buildThreeDingVariantHelpers(prediction, history, Object.assign({
    preferredPositionSides: ["recommend", "main", "backup"]
  }, options || {}));
  ctx.pickWanQianBai("三定框架B", ["backup", "main", "recommend"]);
  ctx.pickPositions(2, "三定框架B");
  ctx.pickReverseShape("三定框架B");
  ctx.chosen.forEach((item) => {
    if (item && item.kind === "derivedReverseShape") item.shapeScopeLength = 3;
  });
  const heweiScopeIndexes = [0, 1, 2];
  ctx.repairReport.push("三定框架B二数合作用位：千百十");
  ctx.pickAnyTwo("三定框架B", heweiScopeIndexes);
  ctx.pickTwoDigitAvoid("三定框架B");
  return {
    version: THREE_DING_ONE_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(ctx.chosen.length, THREE_DING_ONE_ITEM_COUNT),
    framework: {
      positionCount: 2,
      heweiCount: 1,
      heweiComboCount: 1,
      reverseShapeCount: 1,
      pairAvoidCount: 1,
      pool: "qianBaiShi",
      pickOrder: ["hewei.wanQianBai", "positionx2", "derivedReverseShape", "anyTwoHeweiHit", "twoDigitAvoid"],
      heweiSidePreference: ["backup", "main", "recommend"],
      heweiScopeIndexes: heweiScopeIndexes.slice()
    },
    heweiScopeIndexes: heweiScopeIndexes.slice(),
    items: mapFrameworkSnapshotItems(ctx.chosen, heweiScopeIndexes, THREE_DING_ONE_ITEM_COUNT),
    repairReport: ctx.repairReport
  };
}

function ensurePredictionThreeDingOneSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (predictionSnapshotsAreImmutable(prediction)) return prediction.threeDingOne || null;
  const assist = resolveStableMathAssist(prediction);
  if (
    threeDingOneSnapshotIsComplete(prediction.threeDingOne)
    && prediction.threeDingOne.stableMathAssist
    && !snapshotNeedsStableMathAssistRefresh(prediction.threeDingOne, assist)
  ) {
    return prediction.threeDingOne;
  }
  // B 与 A/原三定/更稳算法独立选码：尽量错开已选单码 + 更稳排除单码
  const avoidDigits = mergeStableMathAvoidDigits(new Set([
    ...frameworkSingleExcludeDigits(prediction.compactAttack && prediction.compactAttack.items),
    ...frameworkSingleExcludeDigits(prediction.threeDingTwo && prediction.threeDingTwo.items)
  ]), assist);
  const avoidAnyTwoCodes = new Set([
    ...frameworkAnyTwoCodesFromItems(prediction.compactAttack && prediction.compactAttack.items),
    ...frameworkAnyTwoCodesFromItems(prediction.threeDingTwo && prediction.threeDingTwo.items)
  ]);
  const avoidTwoDigitCodes = frameworkTwoDigitCodesFromItems(prediction.threeDingTwo && prediction.threeDingTwo.items);
  prediction.threeDingOne = buildThreeDingOneSnapshot(prediction, history, { avoidDigits, avoidAnyTwoCodes, avoidTwoDigitCodes });
  attachStableMathAssistToSnapshot(prediction.threeDingOne, assist, "三定框架B");
  prediction.threeDingOneItems = prediction.threeDingOne.items;
  prediction.threeDingOneRepairReport = prediction.threeDingOne.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.threeDingOneSnapshotVersion = prediction.threeDingOne.version;
  return prediction.threeDingOne;
}

function buildSideStableSnapshot(prediction, history) {
  // 旁路稳筛 v5：1定位 + 总合尾 + 千百十合尾 + 二码对数 + 和值避开 + 二数合02468排1码 + 三数合5码 + 固定近1300旧码回删
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const periodTarget = sideStablePeriodTarget(prediction && prediction.basePeriod);

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  function pushItem(item, requiredLabel) {
    if (!item || !cleanAutoTop6Code(item.code)) {
      if (requiredLabel) repairReport.push(`旁路稳筛缺少：${requiredLabel}`);
      return false;
    }
    const id = autoTop6ItemId(item);
    if (used.has(id)) return false;
    used.add(id);
    chosen.push(item);
    return true;
  }

  function pickFixedExtra(extraKey, label) {
    const pool = candidates
      .filter((item) => item && item.kind === "extra" && item.key === extraKey && item.side !== "backup")
      .sort((a, b) => {
        if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
        return order(a, b);
      });
    const picked = pool[0] || null;
    if (!picked) {
      repairReport.push(`旁路稳筛固定模型缺失：${label || extraKey}`);
      return null;
    }
    if (picked.lowConfidence) {
      const rate = picked.safetyRate === null ? "样本不足" : `${Number(picked.safetyRate).toFixed(2)}%`;
      repairReport.push(`旁路稳筛观察加入：${picked.name} ${picked.code}，安全率 ${rate}`);
      picked.repairedIntoTop = true;
    }
    pushItem(picked, label || extraKey);
    return picked;
  }

  const positionHealthy = healthy.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  const positionPool = candidates.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  let positionPicked = positionHealthy[0] || positionPool[0] || null;
  if (positionPicked) {
    if (!positionPicked.stabilityGate) {
      positionPicked.repairedIntoTop = true;
      repairReport.push(`旁路稳筛定位健康项不足，已用观察项：${positionPicked.name} ${positionPicked.code}`);
    }
    pushItem(positionPicked, "定位排除");
  } else {
    repairReport.push("旁路稳筛定位正式项不足1个");
  }

  const totalMeta = resolveHeweiAlgorithmMeta(prediction, "total");
  const totalCode = cleanAutoTop6Code(totalMeta.recommend || (prediction && prediction.totalHeweiKill));
  const totalItem = autoTop6DecorateItem(safeHistory, {
    kind: "hewei",
    key: "total",
    side: "recommend",
    name: "总合尾综合排除",
    code: totalCode,
    mode: "avoid",
    algorithmStability: totalMeta.stability || null,
    confidence: totalMeta.confidence || null,
    recommendable: totalMeta.recommendable,
    algorithmReason: totalMeta.reason || "",
    algorithm: totalMeta.algorithm || ""
  }, evaluatedCount);
  if (totalItem && totalItem.lowConfidence) {
    totalItem.repairedIntoTop = true;
    repairReport.push(`旁路稳筛总合尾观察加入：${totalItem.code}`);
  }
  pushItem(totalItem, "总合尾综合排除");

  const wanQianBaiPool = candidates
    .filter((item) => item && item.kind === "hewei" && item.key === "wanQianBai" && item.side !== "backup")
    .sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    });
  const wanQianBai = wanQianBaiPool[0] || null;
  if (wanQianBai && wanQianBai.lowConfidence) {
    wanQianBai.repairedIntoTop = true;
    repairReport.push(`旁路稳筛千百十合尾观察加入：${wanQianBai.code}`);
  }
  pushItem(wanQianBai, "千百十合尾");

  // 二码对数：前四位同时出现这两个数字才杀。
  const twoDigit = pickFixedExtra("twoDigitAvoid", "二码对数避开");
  if (twoDigit) {
    twoDigit.name = "二码对数避开";
    twoDigit.mode = "avoid";
  }

  pickFixedExtra("sumAvoid", "前四位和值避开");
  const sideAnyTwo = pickStructuralAnyTwoHeweiHit(
    safeHistory,
    evaluatedCount,
    heweiScopeIndexesFromItems(chosen),
    chosen,
    prediction,
    new Set(),
    resolveStableMathAssist(prediction)
  );
  if (sideAnyTwo) {
    repairReport.push(`旁路稳筛二数合结构对齐：${sideAnyTwo.code}`);
    pushItem(sideAnyTwo, "二数合尾02468排1码命中");
  } else {
    repairReport.push("旁路稳筛缺少：二数合尾02468排1码命中");
  }

  const sideStableAnyThree = digitSetCandidatesMixedParity(5)
    .map((code) => autoTop6DecorateItem(safeHistory, {
      kind: "extra",
      key: "anyThreeHeweiHit",
      side: "recommend",
      name: "全码池三数合尾5码命中",
      code,
      mode: "hit",
      algorithm: "side-stable-any-three-hewei-selected-five",
      hitMode: true,
      matchMode: "hit-if-any-three-hewei-tail-in-selected-five",
      dynamicEveryPrediction: true
    }, evaluatedCount))
    .sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    })[0] || null;
  if (sideStableAnyThree) {
    if (sideStableAnyThree.lowConfidence) {
      const rate = sideStableAnyThree.safetyRate === null ? "样本不足" : `${Number(sideStableAnyThree.safetyRate).toFixed(2)}%`;
      repairReport.push(`旁路稳筛三数合5码观察加入：${sideStableAnyThree.code}，安全率 ${rate}`);
      sideStableAnyThree.repairedIntoTop = true;
    }
    pushItem(sideStableAnyThree, "全码池三数合尾5码命中");
  } else {
    repairReport.push("旁路稳筛缺少：全码池三数合尾5码命中");
  }

  const recent1300Base = Object.assign({}, recentExactCodeCandidate(1300, safeHistory), {
    kind: "extra",
    key: "recentExactCodeAvoid1300",
    side: "recommend",
    name: "近1300重复四码避开",
    code: "1300",
    mode: "avoid"
  });
  const recent1300 = coldFrameworkDecorateRecentExactItem(safeHistory, recent1300Base, evaluatedCount, 1300);
  const recentPool = recent1300 ? [recent1300] : [];
  const baseForRecent = chosen.filter((item) => item && !/^recentExactCodeAvoid/.test(String(item.key || "")));
  const recentChoice = sideStableChooseRecentExactCandidate(baseForRecent, recentPool, [], periodTarget);
  if (recentChoice) {
    if (recentChoice.lowConfidence) {
      const rate = recentChoice.safetyRate === null ? "样本不足" : `${Number(recentChoice.safetyRate).toFixed(2)}%`;
      repairReport.push(`旁路稳筛近1300旧码观察：${recentChoice.name} ${recentChoice.code}，安全率 ${rate}`);
    }
    pushItem(recentChoice, "旧码回删");
    if (recentChoice.choiceReason) repairReport.push(recentChoice.choiceReason);
  } else {
    repairReport.push("旁路稳筛缺少近1300旧码回删项");
  }

  return {
    version: SIDE_STABLE_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, SIDE_STABLE_ITEM_COUNT),
    framework: {
      positionCount: 1,
      totalHeweiCount: 1,
      wanQianBaiCount: 1,
      twoDigitAvoidCount: 1,
      sumAvoidCount: 1,
      anyTwoHeweiHitCount: 1,
      anyThreeHeweiHitCount: 1,
      recentExactChoiceCount: 1,
      recentExactChoiceTargetGroupCount: periodTarget,
      targetBandMin: SIDE_STABLE_TARGET_MIN,
      targetBandMax: SIDE_STABLE_TARGET_MAX,
      threeHeweiSelectedDigitCount: 5,
      recentExactWindow: 1300,
      semantics: "side-stable-v5=1pos+totalHewei+wanQianBai+twoDigitAvoid+sumAvoid+anyTwo4-pool02468+anyThree5+oldCode1300"
    },
    txtSelection: {
      strategy: recentChoice && recentChoice.trimStrategy || "ascending-first-n",
      targetCount: Number(recentChoice && recentChoice.targetGroupCount || periodTarget),
      targetBandMin: SIDE_STABLE_TARGET_MIN,
      targetBandMax: SIDE_STABLE_TARGET_MAX,
      rawCount: Number(recentChoice && recentChoice.rawGeneratedGroupCount || 0),
      selectedCount: Number(recentChoice && recentChoice.generatedGroupCount || 0),
      cutoffCode: String(recentChoice && recentChoice.generatedCodeCutoff || "")
    },
    items: chosen.slice(0, SIDE_STABLE_ITEM_COUNT).map((item, index) => coldFrameworkSerializeItem(item, index)),
    repairReport
  };
}

function ensurePredictionSideStableSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (sideStableSnapshotIsComplete(prediction.sideStable)) return prediction.sideStable;
  prediction.sideStable = buildSideStableSnapshot(prediction, history);
  prediction.sideStableItems = prediction.sideStable.items;
  prediction.sideStableRepairReport = prediction.sideStable.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.sideStableSnapshotVersion = prediction.sideStable.version;
  return prediction.sideStable;
}

function buildColdFrameworkSnapshot(prediction, history) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  function add(item, requiredLabel) {
    if (!item || !item.code) {
      if (requiredLabel) repairReport.push(`冷码框架缺少：${requiredLabel}`);
      return false;
    }
    const id = autoTop6ItemId(item);
    if (used.has(id)) return false;
    used.add(id);
    chosen.push(item);
    return true;
  }
  function noteLow(item, label) {
    if (!item || !item.lowConfidence) return;
    const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
    repairReport.push(`${label}观察加入：${item.name} ${item.code}，安全率 ${rate}${item.stabilityReason ? "，原因 " + item.stabilityReason : ""}`);
  }

  const positionItems = coldFrameworkPositionCandidates(safeHistory, evaluatedCount).slice(0, 2);
  positionItems.forEach((item) => { noteLow(item, "位置冷码"); add(item, "位置冷码"); });
  if (positionItems.length < 2) repairReport.push(`位置冷码不足2个，当前 ${positionItems.length} 个。`);

  const heweiItems = coldFrameworkHeweiCandidates(safeHistory, evaluatedCount).slice(0, 2);
  heweiItems.forEach((item) => { noteLow(item, "合尾冷码"); add(item, "合尾冷码"); });
  if (heweiItems.length < 2) repairReport.push(`合尾冷码不足2个，当前 ${heweiItems.length} 个。`);

  const reversePool = (autoTop6ReverseShapeCandidatesFromPositions(positionItems, safeHistory) || [])
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(coldFrameworkOrder);
  const reversePicked = reversePool.filter((item) => item && item.stabilityGate === true)[0] || reversePool[0] || null;

  const anyTwo = coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, "anyTwoHeweiHit");
  const anyThree = coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, "anyThreeHeweiHit");
  const recent500 = coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, "recentExactCodeAvoid500");
  const recent800 = coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, "recentExactCodeAvoid800");
  const recentChoice = coldFrameworkChooseRecentExactCandidate(chosen, [recent500, recent800], [reversePicked, anyTwo, anyThree]);
  noteLow(recentChoice, "近500/近800近期码择优");
  add(recentChoice, "近500/近800近期码择优");
  if (recentChoice && recentChoice.choiceReason) {
    const trimmed = Number(recentChoice.trimmedGroupCount || 0);
    if (trimmed > 0) {
      repairReport.push(`近期码择优：${recentChoice.name} ${recentChoice.code}，攻击语完整展开 ${recentChoice.rawGeneratedGroupCount} 组；${recentChoice.generatedGroupCount} 组及截止 ${recentChoice.generatedCodeCutoff} 仅作选型参考，不裁 TXT。`);
    } else {
      repairReport.push(`近期码择优：${recentChoice.name} ${recentChoice.code}，TXT约 ${recentChoice.generatedGroupCount} 组，距5000差 ${recentChoice.groupCountDistance}。`);
    }
  }

  noteLow(reversePicked, "反向形态");
  add(reversePicked, "反向形态");

  noteLow(anyTwo, "二数合");
  add(anyTwo, "二数合");
  noteLow(anyThree, "三数合");
  add(anyThree, "三数合");

  return {
    version: COLD_FRAMEWORK_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, COLD_FRAMEWORK_ITEM_COUNT),
    framework: {
      positionColdCount: 2,
      heweiColdCount: 2,
      recentExactChoiceCount: 1,
      recentExactChoiceTargetGroupCount: COLD_FRAMEWORK_TARGET_GROUP_COUNT,
      reverseShapeCount: 1,
      heweiComboCount: 2
    },
    txtSelection: {
      strategy: recentChoice && recentChoice.trimStrategy || "ascending-first-n",
      targetCount: COLD_FRAMEWORK_TARGET_GROUP_COUNT,
      rawCount: Number(recentChoice && recentChoice.rawGeneratedGroupCount || 0),
      selectedCount: Number(recentChoice && recentChoice.generatedGroupCount || 0),
      cutoffCode: String(recentChoice && recentChoice.generatedCodeCutoff || "")
    },
    items: chosen.slice(0, COLD_FRAMEWORK_ITEM_COUNT).map(coldFrameworkSerializeItem),
    repairReport
  };
}

function ensurePredictionColdFrameworkSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (coldFrameworkSnapshotIsComplete(prediction.coldFramework)) return prediction.coldFramework;
  prediction.coldFramework = buildColdFrameworkSnapshot(prediction, history);
  prediction.coldFrameworkItems = prediction.coldFramework.items;
  prediction.coldFrameworkRepairReport = prediction.coldFramework.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.coldFrameworkSnapshotVersion = prediction.coldFramework.version;
  return prediction.coldFramework;
}

function buildDingKillSnapshot(prediction, history) {
  // 四定定位杀码：2定位 + 1合尾 + 1反向形态 + 二数合 + 三数合 + 旧码回删（剩组约 5000-5010，期数目标不固定）
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(MODEL_STABLE_WINDOW, safeHistory.length));
  const periodTarget = dingKillPeriodTarget(prediction && prediction.basePeriod);

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
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
      if (!item || !item.lowConfidence) return;
      const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
      const reason = item.stabilityReason ? `，原因 ${item.stabilityReason}` : "";
      repairReport.push(`${label}观察：${item.name} ${item.code}，安全率 ${rate}${reason}`);
    });
  }

  const positionPool = candidates.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  const heweiPool = candidates.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  noteLow(positionPool, "四定定位杀码定位池");
  noteLow(heweiPool, "四定定位杀码合尾池");

  const positionHealthy = healthy.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  let before = chosen.length;
  while (chosen.length - before < 2 && pickBest(positionHealthy, 1, false)) {}
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, false)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
    }
    if (chosen.length - before >= 2) repairReport.push("四定定位杀码定位健康项不足，已用正式观察项补齐。");
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, true)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
      repairReport.push(`四定定位杀码定位同码相碰无替代，被迫保留：${chosen[chosen.length - 1].name} ${chosen[chosen.length - 1].code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`四定定位杀码定位不足2个，当前 ${chosen.length - before} 个。`);

  const heweiHealthy = healthy.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  before = chosen.length;
  while (chosen.length - before < 1 && pickBest(heweiHealthy, 1, false)) {}
  if (chosen.length - before < 1) {
    while (chosen.length - before < 1 && pickBest(heweiPool, 1, false)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
    }
    if (chosen.length - before >= 1) repairReport.push("四定定位杀码合尾健康项不足，已用正式观察项补齐。");
  }
  if (chosen.length - before < 1) {
    while (chosen.length - before < 1 && pickBest(heweiPool, 1, true)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
      repairReport.push(`四定定位杀码合尾与定位同码相碰无替代，被迫保留：${chosen[chosen.length - 1].name} ${chosen[chosen.length - 1].code}。`);
    }
  }
  if (chosen.length - before < 1) repairReport.push("四定定位杀码合尾正式项不足1个。");

  const reverseBase = chosen.filter((item) => item.kind === "position");
  const reversePool = autoTop6ReverseShapeCandidatesFromPositions(reverseBase, safeHistory)
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(order);
  noteLow(reversePool, "四定定位杀码反向形态池");
  const reversePicked = reversePool.filter((item) => item && item.stabilityGate === true)[0] || reversePool[0] || null;

  const anyTwo = candidates.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit").sort(order)[0] || null;
  const anyThree = candidates.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit").sort(order)[0] || null;
  const recentPool = ["recentExactCodeAvoid300", "recentExactCodeAvoid500", "recentExactCodeAvoid800", "recentExactCodeAvoid1000"]
    .map((key) => coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, key))
    .filter(Boolean);
  const recentChoice = dingKillChooseRecentExactCandidate(chosen, recentPool, [reversePicked, anyTwo, anyThree], periodTarget);

  if (recentChoice) {
    if (recentChoice.lowConfidence) {
      const rate = recentChoice.safetyRate === null ? "样本不足" : `${Number(recentChoice.safetyRate).toFixed(2)}%`;
      repairReport.push(`旧码回删观察：${recentChoice.name} ${recentChoice.code}，安全率 ${rate}`);
    }
    if (!used.has(autoTop6ItemId(recentChoice))) {
      used.add(autoTop6ItemId(recentChoice));
      chosen.push(recentChoice);
    }
    if (recentChoice.choiceReason) repairReport.push(recentChoice.choiceReason);
  } else {
    repairReport.push("四定定位杀码缺少旧码回删项。");
  }

  if (reversePicked) {
    if (!reversePicked.stabilityGate) {
      reversePicked.repairedIntoTop = true;
      repairReport.push(`四定定位杀码反向形态未达安全阈值，暂用观察项：${reversePicked.name} ${reversePicked.code}。`);
    }
    if (!used.has(autoTop6ItemId(reversePicked))) {
      used.add(autoTop6ItemId(reversePicked));
      chosen.push(reversePicked);
    }
  } else {
    repairReport.push("四定定位杀码反向形态不足1个。");
  }

  [anyTwo, anyThree].forEach((picked, index) => {
    const label = index === 0 ? "二数合" : "三数合";
    if (!picked) {
      repairReport.push(`四定定位杀码固定模型缺失：${label}`);
      return;
    }
    if (picked.lowConfidence) {
      const rate = picked.safetyRate === null ? "样本不足" : `${Number(picked.safetyRate).toFixed(2)}%`;
      repairReport.push(`${label}观察加入：${picked.name} ${picked.code}，安全率 ${rate}`);
    }
    if (!used.has(autoTop6ItemId(picked))) {
      used.add(autoTop6ItemId(picked));
      chosen.push(picked);
    }
  });

  return {
    version: DING_KILL_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, DING_KILL_ITEM_COUNT),
    framework: {
      positionCount: 2,
      heweiCount: 1,
      reverseShapeCount: 1,
      heweiComboCount: 2,
      recentExactChoiceCount: 1,
      recentExactChoiceTargetGroupCount: periodTarget,
      targetBandMin: DING_KILL_TARGET_MIN,
      targetBandMax: DING_KILL_TARGET_MAX
    },
    txtSelection: {
      strategy: recentChoice && recentChoice.trimStrategy || "ascending-first-n",
      targetCount: Number(recentChoice && recentChoice.targetGroupCount || periodTarget),
      targetBandMin: DING_KILL_TARGET_MIN,
      targetBandMax: DING_KILL_TARGET_MAX,
      rawCount: Number(recentChoice && recentChoice.rawGeneratedGroupCount || 0),
      selectedCount: Number(recentChoice && recentChoice.generatedGroupCount || 0),
      cutoffCode: String(recentChoice && recentChoice.generatedCodeCutoff || "")
    },
    items: chosen.slice(0, DING_KILL_ITEM_COUNT).map(coldFrameworkSerializeItem),
    repairReport
  };
}

function ensurePredictionDingKillSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (dingKillSnapshotIsComplete(prediction.dingKill)) return prediction.dingKill;
  prediction.dingKill = buildDingKillSnapshot(prediction, history);
  prediction.dingKillItems = prediction.dingKill.items;
  prediction.dingKillRepairReport = prediction.dingKill.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.dingKillSnapshotVersion = prediction.dingKill.version;
  return prediction.dingKill;
}

function buildPick5000Snapshot(prediction, history) {
  // 看好5000组：2冷定位 + 2冷合尾 + 固定近500 + 反向 + 二数合 + 三数合；5000仅作选型参考，TXT完整展开
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  function add(item, requiredLabel) {
    if (!item || !item.code) {
      if (requiredLabel) repairReport.push(`看好5000缺少：${requiredLabel}`);
      return false;
    }
    const id = autoTop6ItemId(item);
    if (used.has(id)) return false;
    used.add(id);
    chosen.push(item);
    return true;
  }
  function noteLow(item, label) {
    if (!item || !item.lowConfidence) return;
    const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
    repairReport.push(`${label}观察加入：${item.name} ${item.code}，安全率 ${rate}${item.stabilityReason ? "，原因 " + item.stabilityReason : ""}`);
  }

  const positionItems = coldFrameworkPositionCandidates(safeHistory, evaluatedCount).slice(0, 2);
  positionItems.forEach((item) => { noteLow(item, "位置冷码"); add(item, "位置冷码"); });
  if (positionItems.length < 2) repairReport.push(`位置冷码不足2个，当前 ${positionItems.length} 个。`);

  const heweiItems = coldFrameworkHeweiCandidates(safeHistory, evaluatedCount).slice(0, 2);
  heweiItems.forEach((item) => { noteLow(item, "合尾冷码"); add(item, "合尾冷码"); });
  if (heweiItems.length < 2) repairReport.push(`合尾冷码不足2个，当前 ${heweiItems.length} 个。`);

  const reversePool = (autoTop6ReverseShapeCandidatesFromPositions(positionItems, safeHistory) || [])
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(coldFrameworkOrder);
  const reversePicked = reversePool.filter((item) => item && item.stabilityGate === true)[0] || reversePool[0] || null;

  const coldAnyTwoCodes = frameworkAnyTwoCodesFromItems(prediction && prediction.coldFramework && prediction.coldFramework.items);
  const coldChosen = positionItems.concat(heweiItems);
  const anyTwo = pickStructuralAnyTwoHeweiHit(
    safeHistory,
    evaluatedCount,
    null,
    coldChosen,
    prediction,
    coldAnyTwoCodes,
    resolveStableMathAssist(prediction)
  ) || coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, "anyTwoHeweiHit");
  if (anyTwo && coldAnyTwoCodes.size && !coldAnyTwoCodes.has(String(anyTwo.code || ""))) {
    repairReport.push(`看好5000二数合与冷门框错开：${anyTwo.code}`);
  }
  const anyThree = coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, "anyThreeHeweiHit");
  const recent500 = coldFrameworkExtraCandidate(prediction, safeHistory, evaluatedCount, "recentExactCodeAvoid500");
  const recentChoice = coldFrameworkChooseRecentExactCandidate(chosen, [recent500], [reversePicked, anyTwo, anyThree]);
  if (recentChoice) {
    recentChoice.targetGroupCount = PICK5000_TARGET_GROUP_COUNT;
    if (Number(recentChoice.trimmedGroupCount || 0) > 0) {
      recentChoice.choiceReason = `固定近500，目标${PICK5000_TARGET_GROUP_COUNT}组，攻击语完整展开${recentChoice.rawGeneratedGroupCount}组；${recentChoice.generatedGroupCount}组及截止${recentChoice.generatedCodeCutoff}仅作选型参考，不裁TXT`;
    } else {
      recentChoice.choiceReason = `固定近500，目标${PICK5000_TARGET_GROUP_COUNT}组，当前${recentChoice.generatedGroupCount}组`;
    }
  }
  noteLow(recentChoice, "近500旧码");
  add(recentChoice, "近500旧码");
  if (recentChoice && recentChoice.choiceReason) repairReport.push(`看好5000：${recentChoice.choiceReason}`);

  noteLow(reversePicked, "反向形态");
  add(reversePicked, "反向形态");
  noteLow(anyTwo, "二数合");
  add(anyTwo, "二数合");
  noteLow(anyThree, "三数合");
  add(anyThree, "三数合");

  return {
    version: PICK5000_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, PICK5000_ITEM_COUNT),
    framework: {
      positionColdCount: 2,
      heweiColdCount: 2,
      recentExactFixedKey: "recentExactCodeAvoid500",
      recentExactTargetGroupCount: PICK5000_TARGET_GROUP_COUNT,
      reverseShapeCount: 1,
      heweiComboCount: 2
    },
    txtSelection: {
      strategy: recentChoice && recentChoice.trimStrategy || "ascending-first-n",
      targetCount: PICK5000_TARGET_GROUP_COUNT,
      rawCount: Number(recentChoice && recentChoice.rawGeneratedGroupCount || 0),
      selectedCount: Number(recentChoice && recentChoice.generatedGroupCount || 0),
      cutoffCode: String(recentChoice && recentChoice.generatedCodeCutoff || "")
    },
    items: chosen.slice(0, PICK5000_ITEM_COUNT).map(coldFrameworkSerializeItem),
    repairReport
  };
}

function ensurePredictionPick5000Snapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (pick5000SnapshotIsComplete(prediction.pick5000)) return prediction.pick5000;
  prediction.pick5000 = buildPick5000Snapshot(prediction, history);
  prediction.pick5000Items = prediction.pick5000.items;
  prediction.pick5000RepairReport = prediction.pick5000.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.pick5000SnapshotVersion = prediction.pick5000.version;
  return prediction.pick5000;
}

function buildThreeDuishuSnapshot(prediction) {
  const rec = getThreeDuishuRecommendation();
  if (!rec || !rec.ok || !rec.code) return null;
  const pairs = (Array.isArray(rec.pairs) ? rec.pairs : []).map(String).filter((key) => /^\d{2}$/.test(key)).slice(0, 3);
  if (pairs.length !== 3) return null;
  return {
    version: THREE_DUISHU_SNAPSHOT_VERSION,
    kind: "threeDuishu",
    key: "threeDuishu",
    side: "recommend",
    name: "三对数排除",
    code: String(rec.code || ""),
    shot: threeDuishuShotText(pairs),
    pairs,
    pairCodes: (Array.isArray(rec.pairCodes) ? rec.pairCodes : pairs.slice()).map(String).slice(0, 3),
    quality: rec.quality || "",
    confidence: rec.confidence || "normal",
    recommendable: rec.recommendable !== false,
    rate30: rec.rate30,
    rate40: rec.rate40,
    rate120: rec.rate120,
    rate300: rec.rate300,
    sample40: rec.sample40,
    walkForwardRate: rec.walkForward && rec.walkForward.rate,
    stableMathOverlap: Number(rec.stableMathOverlap || 0),
    selectedReason: rec.selectedReason || "",
    selectedLocked: !!rec.selectedLocked,
    sealed: false
  };
}

function ensurePredictionThreeDuishuSnapshot(prediction) {
  if (!prediction || typeof prediction !== "object") return null;
  // 一旦三对数码生成完整就冻结：只允许后续 seal，禁止未封存时被重算覆盖
  if (threeDuishuSnapshotIsComplete(prediction.threeDuishu)) {
    return prediction.threeDuishu;
  }
  const snapshot = buildThreeDuishuSnapshot(prediction);
  if (!snapshot) return prediction.threeDuishu || null;
  prediction.threeDuishu = snapshot;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.threeDuishuSnapshotVersion = THREE_DUISHU_SNAPSHOT_VERSION;
  return prediction.threeDuishu;
}

function recomputeThreeDuishuRecommendation() {
  const history = (Array.isArray(state && state.history) ? state.history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const period = state.latestRecord && state.latestRecord.period ? state.latestRecord.period : "";
  const signature = threeDuishuHistorySignature(history, period);
  if (!history.length) {
    const empty = { ok: false, code: "", shot: "", pairs: [], error: "history is empty", signature, computedAt: isoNow() };
    state.threeDuishuRecommendationCache = empty;
    return empty;
  }
  const payload = buildThreeDuishuRecommendationPayload(history, period);
  state.threeDuishuRecommendationCache = Object.assign({ signature, computedAt: isoNow() }, payload);
  invalidatePanelStateCache();
  return state.threeDuishuRecommendationCache;
}

module.exports = {
  bindRuntime,
  buildAutoTop6Snapshot,
  ensurePredictionAutoTop6Snapshot,
  buildKillFrameworkSnapshot,
  ensurePredictionKillFrameworkSnapshot,
  autoTop6CandidateItems,
  buildRecommend2Snapshot,
  ensurePredictionRecommend2Snapshot,
  buildCompactAttackSnapshot,
  ensurePredictionCompactAttackSnapshot,
  buildThreeDingTwoSnapshot,
  ensurePredictionThreeDingTwoSnapshot,
  buildThreeDingOneSnapshot,
  ensurePredictionThreeDingOneSnapshot,
  buildSideStableSnapshot,
  ensurePredictionSideStableSnapshot,
  buildColdFrameworkSnapshot,
  ensurePredictionColdFrameworkSnapshot,
  buildDingKillSnapshot,
  ensurePredictionDingKillSnapshot,
  buildPick5000Snapshot,
  ensurePredictionPick5000Snapshot,
  buildThreeDuishuSnapshot,
  ensurePredictionThreeDuishuSnapshot,
  recomputeThreeDuishuRecommendation
};
