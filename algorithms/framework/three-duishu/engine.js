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

const THREE_DUISHU_SNAPSHOT_VERSION = "backend-three-duishu-snapshot-v3-single-pair-rotation";





const THREE_DUISHU_MIN_TRAIN = Number(process.env.THREE_DUISHU_MIN_TRAIN || 80);

const THREE_DUISHU_WALK_WINDOW = Number(process.env.THREE_DUISHU_WALK_WINDOW || 120);


const THREE_DUISHU_MIN_HOLD = Number(process.env.THREE_DUISHU_MIN_HOLD || 3);
const THREE_DUISHU_PAIR_MAX_HOLD = Math.max(THREE_DUISHU_MIN_HOLD, Number(process.env.THREE_DUISHU_PAIR_MAX_HOLD || 8));

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

function getStableMathExcludeDigitSet(){ return heweiFormula.getStableMathExcludeDigitSet(); }

function invalidatePanelStateCache(options) {
  if (typeof _runtime.invalidatePanelStateCache === "function") {
    return _runtime.invalidatePanelStateCache(options);
  }
}

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

function selectThreeDuishuPair(records, candidate, options) {
  const opts = options && typeof options === "object" ? options : {};
  const applyLock = opts.applyLock === true;
  const period = String(opts.period || "");
  const pairs = resolveThreeDuishuPairs(candidate && candidate._pairs ? candidate._pairs : candidate && candidate.pairs);
  if (!pairs.length) return null;
  const scored = pairs.map((pair) => {
    const windows = threeDuishuComboRateMultiWindow(records, [pair], [30, MODEL_NEAR_WINDOW, 120, MODEL_STABLE_WINDOW]);
    const r30 = windows[30] || { total: 0, hit: 0, fail: 0, rate: null };
    const r40 = windows[MODEL_NEAR_WINDOW] || r30;
    const r120 = windows[120] || r40;
    const r300 = windows[MODEL_STABLE_WINDOW] || r120;
    const lower30 = wilsonLowerPercent(r30.hit, r30.total);
    const lower40 = wilsonLowerPercent(r40.hit, r40.total);
    const lower120 = wilsonLowerPercent(r120.hit, r120.total);
    const lower300 = wilsonLowerPercent(r300.hit, r300.total);
    const score = lower30 * 0.2 + lower40 * 0.25 + lower120 * 0.3 + lower300 * 0.15 + Number(r40.rate || 0) * 0.1;
    return {
      pair: pair.key,
      pairCode: pair.codes.join(""),
      rate30: r30.rate,
      rate40: r40.rate,
      rate120: r120.rate,
      rate300: r300.rate,
      lower30,
      lower40,
      lower120,
      lower300,
      sample30: r30.total,
      sample40: r40.total,
      sample120: r120.total,
      sample300: r300.total,
      fail30: r30.fail,
      fail40: r40.fail,
      score: Number(score.toFixed(4))
    };
  }).sort((left, right) => {
    if (right.score !== left.score) return right.score - left.score;
    if (right.lower120 !== left.lower120) return right.lower120 - left.lower120;
    if (Number(right.rate120 || 0) !== Number(left.rate120 || 0)) return Number(right.rate120 || 0) - Number(left.rate120 || 0);
    return left.pair.localeCompare(right.pair);
  });
  const primary = scored[0];
  if (!primary) return null;
  let selected = primary;
  let selectionReason = "pair-primary-initial";
  let holdCount = 1;
  if (applyLock) {
    if (!state.threeDuishuPairSelectionLock || typeof state.threeDuishuPairSelectionLock !== "object") {
      state.threeDuishuPairSelectionLock = {};
    }
    const lock = state.threeDuishuPairSelectionLock;
    const oldKey = String(lock.pairKey || "");
    const old = oldKey ? scored.find((item) => item.pair === oldKey) : null;
    const oldHold = Math.max(0, Number(lock.holdCount || 0));
    const samePeriod = String(lock.period || "") === period;
    if (old) {
      const oldScore = Number(old.score || 0);
      const primScore = Number(primary.score || 0);
      selected = primary;
      if (old.pair === primary.pair) {
        selectionReason = "pair-vote-stick";
      } else {
        selectionReason = primScore > oldScore ? "pair-vote-high-take" : "pair-vote-switch";
      }
    }
    holdCount = oldKey && selected.pair === oldKey
      ? (samePeriod ? Math.max(1, oldHold) : oldHold + 1)
      : 1;
    state.threeDuishuPairSelectionLock = {
      pairKey: selected.pair,
      pairCode: selected.pairCode,
      period,
      holdCount,
      reason: selectionReason,
      selectedScore: Number(selected.score || 0),
      primaryPair: primary.pair,
      primaryScore: Number(primary.score || 0),
      switchMargin: THREE_DUISHU_SWITCH_MARGIN,
      minHold: THREE_DUISHU_MIN_HOLD,
      maxHold: THREE_DUISHU_PAIR_MAX_HOLD,
      updatedAt: isoNow()
    };
  }
  return {
    pair: selected.pair,
    pairCode: selected.pairCode,
    score: selected.score,
    reason: `对子择一：${selected.pair}（当期票第一，持有${holdCount}期）`,
    selectionReason,
    holdCount,
    minHold: THREE_DUISHU_MIN_HOLD,
    maxHold: THREE_DUISHU_PAIR_MAX_HOLD,
    locked: selected.pair !== primary.pair,
    primaryPair: primary.pair,
    primaryScore: primary.score,
    rate30: selected.rate30,
    rate40: selected.rate40,
    rate120: selected.rate120,
    rate300: selected.rate300,
    lower30: selected.lower30,
    lower40: selected.lower40,
    lower120: selected.lower120,
    lower300: selected.lower300,
    sample30: selected.sample30,
    sample40: selected.sample40,
    sample120: selected.sample120,
    sample300: selected.sample300,
    fail30: selected.fail30,
    fail40: selected.fail40,
    candidates: scored
  };
}

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
    const pairSelection = selectThreeDuishuPair(train, selected);
    if (!pairSelection) continue;
    const success = !threeDuishuRecordHits(rows[index], [pairSelection.pair]);
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
      selectedPair: pairSelection.pair,
      selectedPairCode: pairSelection.pairCode,
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

function threeDuishuComboRate(records, pairs, limit) {
  const masks = preprocessThreeDuishuHistoryMasks(records, limit);
  if (!masks.length) return { total: 0, hit: 0, fail: 0, rate: null, streak: 0 };
  const pairMasks = (Array.isArray(pairs) ? pairs : []).map((pair) => threeDuishuPairMask(pair)).filter((mask) => mask > 0);
  return threeDuishuComboRateFromMasks(masks, pairMasks, masks.length);
}

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
        // 软筛选：组合级 Risk Score（多重违规降权，替代「命中即死」）
        let softRiskScore = 0;
        let softRiskLevel = "low";
        if (typeof stableMathFilterBridge.scoreCombination === "function") {
          const risk = stableMathFilterBridge.scoreCombination(String(code).slice(0, 4));
          softRiskScore = Number(risk && risk.riskScore || 0);
          softRiskLevel = (risk && risk.riskLevel) || "low";
        }
        const recentFailPenalty = Number((r40.fail != null ? r40.fail : r30.fail) || 0) * 0.28 + Math.max(0, 3 - Number((r40.streak != null ? r40.streak : r30.streak) || 0)) * 0.6;
        const weakBlockPenalty = Number(block.weakBlocks || 0) * 0.9;
        const softRiskPenalty = softRiskScore * 0.45 + (softRiskLevel === "high" ? 1.2 : softRiskLevel === "mid" ? 0.4 : 0);
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
          stableMathOverlap * 1.25 -
          softRiskPenalty +
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
  const pairSelection = selectThreeDuishuPair(history, best, { applyLock: true, period });
  if (!pairSelection) {
    return { ok: false, code: "", shot: "", pairs: [], error: "no pair candidate" };
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
    shot: threeDuishuShotText([pairSelection.pair]),
    pairs: best.pairs,
    pairCodes: best.pairCodes,
    selectedPair: pairSelection.pair,
    selectedPairCode: pairSelection.pairCode,
    pairSelection,
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
    THREE_DUISHU_SNAPSHOT_VERSION,
    String(period || ""),
    rows.length,
    first && first.uid ? first.uid : "",
    first && Array.isArray(first.numbers) ? first.numbers.slice(0, 4).join("") : ""
  ].join("|");
}

function getThreeDuishuRecommendation() {
  getState(); // prime module `state` from runtime (required for process workers)
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
  const selectedPair = String(snapshot.selectedPair || "");
  return pairs.length === 3 && /^\d{3}$/.test(code) && /^\d{2}$/.test(selectedPair) && pairs.includes(selectedPair);
}

function firstFourNumbers(record) {
  return (record && Array.isArray(record.numbers) ? record.numbers : []).slice(0, 4).map(String);
}

function isoNow() {
  return new Date().toISOString();
}

function buildThreeDuishuSnapshot(prediction) {
  const rec = getThreeDuishuRecommendation();
  if (!rec || !rec.ok || !rec.code) return null;
  const pairs = (Array.isArray(rec.pairs) ? rec.pairs : []).map(String).filter((key) => /^\d{2}$/.test(key)).slice(0, 3);
  if (pairs.length !== 3) return null;
  const selectedPair = String(rec.selectedPair || "");
  if (!/^\d{2}$/.test(selectedPair) || !pairs.includes(selectedPair)) return null;
  const pairSelection = rec.pairSelection && typeof rec.pairSelection === "object" ? rec.pairSelection : {};
  return {
    version: THREE_DUISHU_SNAPSHOT_VERSION,
    kind: "threeDuishu",
    key: "threeDuishu",
    side: "recommend",
    name: "三对数排除",
    code: String(rec.code || ""),
    shot: threeDuishuShotText([selectedPair]),
    pairs,
    pairCodes: (Array.isArray(rec.pairCodes) ? rec.pairCodes : pairs.slice()).map(String).slice(0, 3),
    selectedPair,
    selectedPairCode: String(rec.selectedPairCode || selectedPair),
    quality: rec.quality || "",
    confidence: rec.confidence || "normal",
    recommendable: rec.recommendable !== false,
    rate30: pairSelection.rate30,
    rate40: pairSelection.rate40,
    rate120: pairSelection.rate120,
    rate300: pairSelection.rate300,
    sample40: pairSelection.sample40,
    walkForwardRate: rec.walkForward && rec.walkForward.rate,
    stableMathOverlap: Number(rec.stableMathOverlap || 0),
    selectedReason: rec.selectedReason || "",
    pairSelectionReason: pairSelection.reason || "",
    pairSelectionMode: "vote-follow-primary",
    pairSelectionHoldCount: Number(pairSelection.holdCount || 0),
    pairSelectionMaxHold: Number(pairSelection.maxHold || THREE_DUISHU_PAIR_MAX_HOLD),
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
  getState(); // prime module `state` from runtime (required for process workers)
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

function clearScoreThreeDuishuCandidatesCache() {
  try { scoreThreeDuishuCandidatesLru.clear(); } catch (_) {}
}

module.exports = {
  bindRuntime,
  buildThreeDuishuSnapshot,
  ensurePredictionThreeDuishuSnapshot,
  recomputeThreeDuishuRecommendation,
  getThreeDuishuRecommendation,
  threeDuishuSnapshotIsComplete,
  resolveThreeDuishuPairs,
  threeDuishuRecordHits,
  selectThreeDuishuPair,
  clearScoreThreeDuishuCandidatesCache,
  buildThreeDuishuRecommendationPayload
};
