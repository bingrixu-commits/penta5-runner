"use strict";
/**
 * 四定框架：1定位（投票换码）+ 千百十个合（投票换码）+ 二数合（投票换码）+ 三数合（投票换码）
 * + 除对数两组三数（投票换码，最多共享1码）+ 除对数一组俩数（投票换码，与两组三数错开粘码）
 * + 形态固定杀 单单单单/双双双双 + 1条投票形态（只在单双或大小家族内跟票走；混合不进正框）。
 * 计票：30/60/120 各窗只把 1 票投给该窗第一名；新码票数必须严格更高才换，否则粘住。
 * 攻击语末尾 除{投票形态} 必须与第10项 shapeAvoidData 同码。
 * 整框 AND：10 项全过才算中。referenceModels 仅供参考，不进 AND。
 */

const shapeLib = require("../../ai/_shape_lib");
const extraFormula = require("../../extra-kills/formula");
const { wilsonLowerRate } = require("../../_shared/formula_kit");


const FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION = "backend-four-ding-framework-snapshot-v3-pos1-wqbs-shape-fixed2-data1";

function isAllowedShapeKey(shapeKey) {
  return shapeKey === "shapeSize" || shapeKey === "shapeParity" || shapeKey === "shapeMixed";
}
const FOUR_DING_FRAMEWORK_ITEM_COUNT = 10;
const THREE_DIGIT_KEYS = Object.freeze(["threeDigitAvoid", "threeDigitHit"]);
const THREE_DIGIT_KEYS_LEGACY = Object.freeze(["threeDigitAvoid", "threeDigitHit", "threeDigitAvoid2"]);
const TWO_DIGIT_JOINT_KEY = "duishuJointTwo";
const THREE_DIGIT_LABELS = Object.freeze({
  threeDigitAvoid: "除对数第1组（三数）",
  threeDigitHit: "除对数第2组（三数）"
});
const TWO_DIGIT_AVOID_KEYS = Object.freeze(["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"]);
const TWO_DIGIT_JOINT_LABEL = "除对数（俩数·合选投票）";
const MAX_DUISHU_SHARE = 1;
const POSITIONS = Object.freeze([
  { key: "wan", label: "千位", index: 0 },
  { key: "qian", label: "百位", index: 1 },
  { key: "bai", label: "十位", index: 2 },
  { key: "shi", label: "个位", index: 3 }
]);
const FIXED_SHAPE_CODES = Object.freeze(["单单单单", "双双双双"]);
const VOTE_PICK_RULE = "backend-vote-first-place-stagger";
const VOTE_WINDOW = 120;
const VOTE_WINDOWS = Object.freeze([
  { win: 30, rateKey: "rate30", sampleKey: "sample30", minSample: 20 },
  { win: 60, rateKey: "rate60", sampleKey: "sample60", minSample: 40 },
  { win: 120, rateKey: "rate120", sampleKey: "sample120", minSample: 60 }
]);
const VOTE_CHECKS = VOTE_WINDOWS;
const FLEX_MIN_HOLD = Math.max(1, Number(process.env.FOUR_DING_FLEX_MIN_HOLD || 2));
const FLEX_MAX_HOLD = Math.max(FLEX_MIN_HOLD, Number(process.env.FOUR_DING_FLEX_MAX_HOLD || 6));
const FLEX_MISS_SWITCH = Math.max(1, Number(process.env.FOUR_DING_FLEX_MISS_SWITCH || 1));

let _runtime = { getState: () => ({}) };

function bindRuntime(runtime) {
  _runtime = Object.assign({}, _runtime, runtime || {});
  if (typeof extraFormula.bindRuntime === "function") {
    extraFormula.bindRuntime(_runtime);
  }
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function cleanCode(value) {
  return String(value === undefined || value === null ? "" : value).trim();
}

function isAnyTwoHeweiCode(code) {
  if (typeof extraFormula.isAnyTwoHeweiCode === "function") {
    return !!extraFormula.isAnyTwoHeweiCode(code);
  }
  const text = String(code || "").replace(/\D/g, "");
  if (text.length !== 4 || new Set(text.split("")).size !== 4) return false;
  return text.split("").every((d) => "02468".includes(d));
}

function isThreeDigitCode(code) {
  return /^\d{3}$/.test(String(code || "")) && new Set(String(code).split("")).size === 3;
}

function isTwoDigitCode(code) {
  const text = String(code || "");
  return /^\d{2}$/.test(text) && new Set(text.split("")).size === 2;
}

function isShapeCode(code) {
  return /^[大小单双]{4}$/.test(String(code || ""));
}

function extraCode(extra) {
  if (!extra || typeof extra !== "object") return cleanCode(extra);
  if (extra.value !== undefined && extra.value !== null && String(extra.value).trim() !== "") {
    return cleanCode(extra.value);
  }
  return cleanCode(extra.code);
}

function resolveJointTwoDigit(prediction, extras) {
  const joint = asObject(prediction && prediction.duishuJointPush);
  let pair = String(joint.pair || joint.code || "").replace(/\D/g, "").slice(0, 2);
  if (isTwoDigitCode(pair)) {
    return {
      code: pair,
      votes: Number(joint.score || joint.rate120 || 0),
      rate120: Number(joint.rate120 || 0),
      lower120: Number(joint.lower120 || 0),
      source: joint.source || "duishuJointPush",
      sourceLabel: joint.label || (joint.source === "threeDuishu" ? "三对数" : "四定对数"),
      flexReason: flexReasonFromPush(joint.reason),
      flexHoldCount: Number(joint.holdCount || 0),
      label: TWO_DIGIT_JOINT_LABEL
    };
  }
  const push = asObject(prediction && prediction.twoDigitAvoidPush);
  pair = String(push.pair || push.code || "").replace(/\D/g, "").slice(0, 2);
  if (isTwoDigitCode(pair)) {
    return {
      code: pair,
      votes: Number(push.score || push.rate120 || 0),
      rate120: Number(push.rate120 || 0),
      lower120: Number(push.lower120 || 0),
      source: "twoDigitAvoidPush",
      sourceLabel: "四定对数",
      flexReason: flexReasonFromPush(push.reason),
      flexHoldCount: Number(push.holdCount || 0),
      label: TWO_DIGIT_JOINT_LABEL
    };
  }
  const rows = ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].map((key) => {
    const extra = asObject(extras && extras[key]);
    const code = extraCode(extra).replace(/\D/g, "").slice(0, 2);
    return {
      code,
      extra,
      rate120: Number(extra.rate120 || 0),
      lower120: Number(extra.lower120 || 0),
      score: Number(extra.hardnessScore || extra.rate120 || 0)
    };
  }).filter((row) => isTwoDigitCode(row.code)).sort((left, right) => {
    if (right.score !== left.score) return right.score - left.score;
    if (right.rate120 !== left.rate120) return right.rate120 - left.rate120;
    return left.code.localeCompare(right.code);
  });
  if (!rows[0]) return null;
  return {
    code: rows[0].code,
    votes: rows[0].score,
    rate120: rows[0].rate120,
    lower120: rows[0].lower120,
    source: "extraTop",
    label: TWO_DIGIT_JOINT_LABEL
  };
}

function ensureExtraKills(prediction, history) {
  const existing = asObject(prediction && prediction.extraKills);
  const needKeys = ["anyTwoHeweiHit", "anyThreeHeweiHit"].concat(THREE_DIGIT_KEYS, TWO_DIGIT_AVOID_KEYS);
  const missing = needKeys.some((key) => !extraCode(existing[key]));
  if (!missing) return existing;
  if (typeof extraFormula.calculateExtraAvoidKills !== "function") return existing;
  const computed = extraFormula.calculateExtraAvoidKills(history) || {};
  const merged = Object.assign({}, existing);
  needKeys.forEach((key) => {
    if (!extraCode(merged[key]) && computed[key]) merged[key] = computed[key];
  });
  prediction.extraKills = merged;
  if (!prediction.detail || typeof prediction.detail !== "object") prediction.detail = {};
  prediction.detail.extraKills = merged;
  return merged;
}

function digitRemainPool(killDigit) {
  const kill = cleanCode(killDigit);
  return "1234567890".split("").filter((d) => d !== kill).join("");
}

function fourPosDigit(prediction, key) {
  const snap = asObject(prediction && prediction.fourPosExclude);
  const items = Array.isArray(snap.items) ? snap.items : [];
  for (let i = 0; i < items.length; i += 1) {
    const item = items[i];
    if (item && item.key === key && /^\d$/.test(cleanCode(item.code))) return cleanCode(item.code);
  }
  const pk = asObject(prediction && prediction.positionKills);
  const entry = asObject(pk[key]);
  const code = cleanCode(
    entry.recommendDigit != null
      ? entry.recommendDigit
      : (entry.selectedDigit != null
        ? entry.selectedDigit
        : (entry.main != null ? entry.main : (entry.killDigits && entry.killDigits[0])))
  );
  return /^\d$/.test(code) ? code : "";
}

function wanQianBaiShiDigit(prediction) {
  const ih = asObject(prediction && prediction.independentHeweiKills);
  const entry = asObject(ih.wanQianBaiShi);
  const code = cleanCode(entry.killDigit != null ? entry.killDigit : (entry.value != null ? entry.value : entry.code));
  if (/^\d$/.test(code)) return code;
  const total = cleanCode(prediction && prediction.totalHeweiKill);
  return /^\d$/.test(total) ? total : "";
}

function parsePanelRate(key, prediction) {
  const st = asObject(_runtime.getState && _runtime.getState());
  const counters = asObject(st.counters);
  const c = asObject(counters[key]);
  const total = Number(c.total || 0);
  const match = Number(c.match || 0);
  if (total > 0) return { rate: match / total, total, match, source: "counters" };
  const raw = String((st.rates && st.rates[key]) || "").replace("%", "").trim();
  const n = Number(raw);
  if (Number.isFinite(n) && n >= 0) return { rate: n > 1 ? n / 100 : n, total: 0, match: 0, source: "rates" };
  const entry = asObject(asObject(prediction && prediction.positionKills)[key]);
  const fallback = Number(entry.rate120 || entry.stableScore || entry.rate || 0);
  return { rate: fallback > 1 ? fallback / 100 : fallback, total: 0, match: 0, source: "positionKills" };
}

function pickHighestRatePosition(prediction) {
  const ranked = POSITIONS.map((pos) => {
    const stats = parsePanelRate(pos.key, prediction);
    const digit = fourPosDigit(prediction, pos.key);
    return Object.assign({}, pos, { stats, digit });
  }).sort((a, b) => {
    if (b.stats.rate !== a.stats.rate) return b.stats.rate - a.stats.rate;
    if (b.stats.total !== a.stats.total) return b.stats.total - a.stats.total;
    return a.index - b.index;
  });
  const withDigit = ranked.find((row) => /^\d$/.test(row.digit));
  return withDigit || ranked[0] || null;
}

function firstFourNums(record) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers.map(Number) : [];
  return nums.length >= 4 ? nums.slice(0, 4) : [];
}

function wanQianBaiShiOf(record) {
  const nums = firstFourNums(record);
  if (nums.length < 4) return "";
  return String((nums[0] + nums[1] + nums[2] + nums[3]) % 10);
}

function voteStatsFromOk(history, okFn) {
  const sliced = Array.isArray(history) ? history.slice(0, VOTE_WINDOW) : [];
  const windows = {
    30: { total: 0, hit: 0, missStreak: 0, _run: 0, _lead: true },
    60: { total: 0, hit: 0, missStreak: 0, _run: 0, _lead: true },
    120: { total: 0, hit: 0, missStreak: 0, _run: 0, _lead: true }
  };
  for (let i = 0; i < sliced.length; i += 1) {
    let ok = false;
    try { ok = !!okFn(sliced[i]); } catch (err) { ok = false; }
    [30, 60, 120].forEach((limit) => {
      if (i >= limit) return;
      const row = windows[limit];
      row.total += 1;
      if (ok) {
        row.hit += 1;
        row._run = 0;
        row._lead = false;
      } else {
        row._run += 1;
        if (row._lead) row.missStreak += 1;
      }
    });
  }
  [30, 60, 120].forEach((limit) => {
    const row = windows[limit];
    row.rate = row.total ? (row.hit * 100) / row.total : 0;
    delete row._run;
    delete row._lead;
  });
  const r30 = windows[30];
  const r60 = windows[60];
  const r120 = windows[120];
  return {
    votes: 0,
    rate30: Number(Number(r30.rate || 0).toFixed(2)),
    rate60: Number(Number(r60.rate || 0).toFixed(2)),
    rate120: Number(Number(r120.rate || 0).toFixed(2)),
    lower120: Number(wilsonLowerRate(r120.hit, r120.total, 1.44).toFixed(2)),
    missStreak30: Number(r30.missStreak || 0),
    sample30: Number(r30.total || 0),
    sample60: Number(r60.total || 0),
    sample120: Number(r120.total || 0)
  };
}

function pickVotedRow(rows) {
  const list = (Array.isArray(rows) ? rows : []).filter(Boolean);
  if (!list.length) return null;
  assignFirstPlaceVotes(list);
  const voted = list.filter((row) => Number(row.votes || 0) > 0);
  const pool = voted.length ? voted : list;
  pool.sort(compareShapeStable);
  return pool[0] || null;
}

function uniqueDigits(code) {
  return Array.from(new Set(String(code || "").replace(/\D/g, "").split("").filter(Boolean)));
}

function sharedDigitCount(left, right) {
  const seen = new Set(uniqueDigits(left));
  let n = 0;
  uniqueDigits(right).forEach((digit) => {
    if (seen.has(digit)) n += 1;
  });
  return n;
}

function overlapsBlocked(code, blockedSet) {
  return uniqueDigits(code).some((digit) => blockedSet && blockedSet.has(digit));
}

function twoDigitPairCandidates() {
  const out = [];
  for (let a = 0; a <= 8; a += 1) {
    for (let b = a + 1; b <= 9; b += 1) out.push(String(a) + String(b));
  }
  return out;
}

function normalizeVoteRow(row) {
  if (!row || typeof row !== "object") return row;
  row.sample30 = Number(row.sample30 || row.total30 || 0);
  row.sample60 = Number(row.sample60 || row.total60 || 0);
  row.sample120 = Number(row.sample120 || row.total120 || 0);
  row.rate60 = Number(row.rate60 || 0);
  return row;
}

function assignFirstPlaceVotes(rows) {
  const list = (Array.isArray(rows) ? rows : []).filter(Boolean);
  list.forEach((row) => {
    normalizeVoteRow(row);
    row.votes = 0;
  });
  VOTE_WINDOWS.forEach((spec) => {
    let best = null;
    list.forEach((row) => {
      if (Number(row[spec.sampleKey] || 0) < spec.minSample) return;
      if (!best) {
        best = row;
        return;
      }
      const challengerRate = Number(row[spec.rateKey] || 0);
      const incumbentRate = Number(best[spec.rateKey] || 0);
      if (challengerRate > incumbentRate) {
        best = row;
        return;
      }
      if (challengerRate < incumbentRate) return;
      if (Number(row.lower120 || 0) > Number(best.lower120 || 0)) {
        best = row;
        return;
      }
      if (Number(row.lower120 || 0) < Number(best.lower120 || 0)) return;
      if (String(rowCode(row)).localeCompare(String(rowCode(best)), "zh-CN") < 0) best = row;
    });
    if (best) best.votes += 1;
  });
  list.sort(compareShapeStable);
  return list;
}

function readFlexLocks() {
  const st = asObject(_runtime.getState && _runtime.getState());
  if (!st.fourDingFlexLocks || typeof st.fourDingFlexLocks !== "object") st.fourDingFlexLocks = {};
  return st.fourDingFlexLocks;
}

function rowCode(row) {
  if (!row) return "";
  if (row.digit != null && String(row.digit).length) return String(row.digit);
  return cleanCode(row.code);
}

function applyAuxBoost(ranked, auxCode, minRate120) {
  const code = cleanCode(auxCode);
  const list = Array.isArray(ranked) ? ranked : [];
  if (!code) return list;
  const floor = Number(minRate120 || 88);
  list.forEach((row) => {
    if (rowCode(row) !== code) return;
    if (Number(row.rate120 || 0) < floor) return;
    row.auxBoost = true;
    row.auxHits = Number(row.auxHits || 0) + 1;
  });
  return list;
}

function applyManyAuxBoost(ranked, auxCodes, minRate120) {
  const seen = {};
  (Array.isArray(auxCodes) ? auxCodes : []).forEach((code) => {
    const c = cleanCode(code);
    if (!c || seen[c]) return;
    seen[c] = true;
    applyAuxBoost(ranked, c, minRate120);
  });
  return ranked;
}

function collectAuxSingleDigits(prediction) {
  const out = [];
  function add(value) {
    const code = cleanCode(value);
    if (/^\d$/.test(code)) out.push(code);
  }
  const pk = asObject(prediction && prediction.positionKills);
  POSITIONS.forEach((pos) => {
    const entry = asObject(pk[pos.key]);
    add(entry.recommendDigit || entry.selectedDigit || entry.main);
    add(entry.backup);
    add(entry.mainAlgorithmDigit);
  });
  const fpe = asObject(prediction && prediction.fourPosExclude);
  (Array.isArray(fpe.items) ? fpe.items : []).forEach((item) => add(item && item.code));
  const ih = asObject(prediction && prediction.independentHeweiKills);
  Object.keys(ih).forEach((key) => {
    const entry = asObject(ih[key]);
    add(entry.killDigit || entry.code || entry.value);
  });
  add(prediction && prediction.totalHeweiKill);
  const hewei = asObject(prediction && prediction.hewei);
  add(hewei.killDigit || hewei.code);
  const codex = asObject(prediction && prediction.codexStable);
  POSITIONS.forEach((pos) => {
    const entry = asObject(codex[pos.key] || asObject(codex.positionKills)[pos.key]);
    add(entry.digit || entry.code || entry.killDigit || entry.recommendDigit);
  });
  return out;
}

function collectAuxSetCodes(extras, testFn) {
  const out = [];
  const bag = asObject(extras);
  Object.keys(bag).forEach((key) => {
    const code = extraCode(bag[key]);
    if (code && (!testFn || testFn(code))) out.push(code);
  });
  return out;
}

function applyFlexSwitch(slot, ranked, period, options) {
  const list = (Array.isArray(ranked) ? ranked : []).filter((row) => rowCode(row));
  if (!list.length) return { picked: null, reason: "flex-empty", holdCount: 0 };
  if (!(options && options.votesReady)) assignFirstPlaceVotes(list);
  else list.sort(compareShapeStable);
  const primary = list[0];
  const locks = readFlexLocks();
  const old = asObject(locks[slot]);
  const oldCode = String(old.code || "");
  const oldRow = oldCode ? list.find((row) => rowCode(row) === oldCode) : null;
  const oldHold = Math.max(0, Number(old.holdCount || 0));
  const samePeriod = String(old.period || "") === String(period || "");
  let selected = primary;
  let reason = "flex-primary-vote";
  if (oldRow) {
    const oldVotes = Number(oldRow.votes || 0);
    const primVotes = Number(primary.votes || 0);
    if (rowCode(primary) === oldCode || primVotes <= oldVotes) {
      selected = oldRow;
      reason = "flex-vote-stick";
    } else {
      selected = primary;
      reason = "flex-vote-high-take";
    }
  }
  const newCode = rowCode(selected);
  locks[slot] = {
    code: newCode,
    holdCount: oldCode && newCode === oldCode ? (samePeriod ? Math.max(1, oldHold) : oldHold + 1) : 1,
    period: String(period || ""),
    reason,
    votes: Number(selected && selected.votes || 0),
    missStreak30: Number(selected && selected.missStreak30 || 0),
    shapeKey: String((selected && selected.shapeKey) || old.shapeKey || "")
  };
  if (selected) {
    selected.flexReason = reason;
    selected.flexHoldCount = locks[slot].holdCount;
  }
  return { picked: selected, reason, holdCount: locks[slot].holdCount };
}

function flexNameTag(row) {
  const reason = String(row && row.flexReason || "");
  const bits = [];
  if (reason === "flex-vote-high-take" || reason === "flex-primary-vote" || reason === "flex-vote-switch") bits.push("投票换码");
  else if (reason === "flex-vote-stick") bits.push("票多粘");
  return bits.length ? (" · " + bits.join(" · ")) : "";
}

function pickVotedPosition(prediction, history, period) {
  const ranked = POSITIONS.map((pos) => {
    const digit = fourPosDigit(prediction, pos.key);
    const stats = /^\d$/.test(digit)
      ? voteStatsFromOk(history, (record) => {
        const nums = firstFourNums(record);
        return nums.length >= 4 && String(nums[pos.index]) !== digit;
      })
      : { votes: 0, rate30: 0, rate120: 0, lower120: 0, missStreak30: 0, sample120: 0 };
    const panel = parsePanelRate(pos.key, prediction);
    return Object.assign({}, pos, { digit, stats: panel }, stats);
  }).filter((row) => /^\d$/.test(row.digit));
  ranked.sort(compareShapeStable);
  applyManyAuxBoost(ranked, collectAuxSingleDigits(prediction), 88);
  const flexed = applyFlexSwitch("position", ranked, period);
  return flexed.picked || pickVotedRow(ranked) || pickHighestRatePosition(prediction);
}

function pickVotedWanQianBaiShi(history, fallbackDigit, period, auxCodes) {
  const ranked = "0123456789".split("").map((digit) => {
    const stats = voteStatsFromOk(history, (record) => wanQianBaiShiOf(record) !== digit);
    return Object.assign({ code: digit }, stats);
  });
  ranked.sort(compareShapeStable);
  applyManyAuxBoost(ranked, [fallbackDigit].concat(Array.isArray(auxCodes) ? auxCodes : []), 88);
  const flexed = applyFlexSwitch("fourHewei", ranked, period);
  const picked = flexed.picked || pickVotedRow(ranked);
  if (picked && /^\d$/.test(picked.code)) return picked;
  if (/^\d$/.test(fallbackDigit)) {
    return Object.assign({ code: fallbackDigit, votes: 0, rate120: 0, lower120: 0 }, ranked.find((row) => row.code === fallbackDigit) || {});
  }
  return null;
}

function extraHitOk(record, code, size) {
  if (typeof extraFormula.anyHeweiCombinationHit === "function") {
    return extraFormula.anyHeweiCombinationHit(record, code, size, 1);
  }
  return false;
}

function capVoteCodes(codes, extra, limit) {
  const out = [];
  const seen = {};
  function add(value) {
    const code = cleanCode(value);
    if (!code || seen[code] || out.length >= limit) return;
    seen[code] = true;
    out.push(code);
  }
  add(extraCode(extra));
  if (extra && extra.primary != null) add(extra.primary);
  if (extra && extra.backup != null) add(extra.backup);
  (Array.isArray(codes) ? codes : []).forEach(add);
  return out;
}

function capVoteCodesMany(codes, extraList, limit, validate) {
  const out = [];
  const seen = {};
  function add(value) {
    const code = cleanCode(value);
    if (!code || seen[code] || out.length >= limit) return;
    if (validate && !validate(code)) return;
    seen[code] = true;
    out.push(code);
  }
  (Array.isArray(extraList) ? extraList : []).forEach((extra) => {
    add(extraCode(extra));
    if (extra && extra.primary != null) add(extra.primary);
    if (extra && extra.backup != null) add(extra.backup);
  });
  (Array.isArray(codes) ? codes : []).forEach(add);
  return out;
}

function threeDigitAvoidCandidates() {
  const out = [];
  for (let a = 0; a < 8; a += 1) {
    for (let b = a + 1; b < 9; b += 1) {
      for (let c = b + 1; c < 10; c += 1) out.push(String(a) + String(b) + String(c));
    }
  }
  return out;
}

function threeDigitCooccurs(record, candidate) {
  const actual = firstFourNums(record).map(String);
  return String(candidate).split("").every((digit) => actual.indexOf(String(digit)) >= 0);
}

function rankVotedExtraAvoid(history, candidates) {
  const ranked = (Array.isArray(candidates) ? candidates : [])
    .filter(isThreeDigitCode)
    .map((code) => Object.assign(
      { code: String(code) },
      voteStatsFromOk(history, (record) => !threeDigitCooccurs(record, code))
    ));
  assignFirstPlaceVotes(ranked);
  return ranked;
}

function flexReasonFromPush(reason) {
  const text = String(reason || "");
  if (text.indexOf("high-take") >= 0) return "flex-vote-high-take";
  if (text.indexOf("stick") >= 0) return "flex-vote-stick";
  if (text.indexOf("switch") >= 0) return "flex-vote-switch";
  return "flex-primary-vote";
}

function pickVotedJointTwo(prediction, extras, history, period) {
  const four = asObject(prediction && prediction.twoDigitAvoidPush);
  const three = asObject(prediction && prediction.threeDuishu);
  const pairSelection = asObject(three.pairSelection);
  const joint = asObject(prediction && prediction.duishuJointPush);
  const ranked = [];
  function addPair(code, source, sourceLabel, rate120, lower120) {
    const pair = String(code || "").replace(/\D/g, "").slice(0, 2);
    if (!isTwoDigitCode(pair) || ranked.some((row) => rowCode(row) === pair)) return;
    ranked.push({
      code: pair,
      source,
      sourceLabel,
      rate120: Number(rate120 || 0),
      lower120: Number(lower120 || 0),
      score: Number(rate120 || 0),
      votes: 0
    });
  }
  addPair(four.pair || four.code, "fourDing", "四定对数", four.rate120, four.lower120);
  addPair(
    three.selectedPair || three.selectedPairCode,
    "threeDuishu",
    "三对数",
    pairSelection.rate120,
    pairSelection.lower120
  );
  ranked.sort((left, right) => {
    if (Number(right.score || 0) !== Number(left.score || 0)) return Number(right.score || 0) - Number(left.score || 0);
    if (Number(right.lower120 || 0) !== Number(left.lower120 || 0)) return Number(right.lower120 || 0) - Number(left.lower120 || 0);
    return String(left.code || "").localeCompare(String(right.code || ""));
  });
  ranked.forEach((row, index) => {
    row.votes = index === 0 ? 1 : 0;
  });
  const jointPair = String(joint.pair || joint.code || "").replace(/\D/g, "").slice(0, 2);
  let picked = isTwoDigitCode(jointPair)
    ? (ranked.find((row) => rowCode(row) === jointPair) || {
      code: jointPair,
      source: joint.source || "duishuJointPush",
      sourceLabel: joint.label || "合选",
      rate120: Number(joint.rate120 || 0),
      lower120: Number(joint.lower120 || 0),
      score: Number(joint.score || joint.rate120 || 0),
      votes: 1
    })
    : (ranked[0] || null);
  if (!picked) return null;
  picked.flexReason = flexReasonFromPush(joint.reason);
  if (!joint.reason) picked.flexReason = "flex-primary-vote";
  picked.flexHoldCount = Number(joint.holdCount || 1);
  picked.sourceLabel = picked.sourceLabel || (picked.source === "threeDuishu" ? "三对数" : "四定对数");
  return picked;
}

function pickVotedExtraHit(history, candidates, size, fallbackCode, validate, period, slot, auxCodes) {
  const list = (Array.isArray(candidates) ? candidates : []).filter((code) => !validate || validate(code));
  const ranked = list.map((code) => {
    const stats = voteStatsFromOk(history, (record) => extraHitOk(record, code, size));
    return Object.assign({ code: String(code) }, stats);
  });
  ranked.sort(compareShapeStable);
  applyManyAuxBoost(ranked, [fallbackCode].concat(Array.isArray(auxCodes) ? auxCodes : []), 88);
  const flexed = slot ? applyFlexSwitch(slot, ranked, period) : { picked: pickVotedRow(ranked) };
  const picked = flexed.picked;
  if (picked && picked.code) return picked;
  if (fallbackCode && (!validate || validate(fallbackCode))) {
    return Object.assign({ code: fallbackCode, votes: 0, rate120: 0, lower120: 0 }, ranked.find((row) => row.code === fallbackCode) || {});
  }
  return ranked[0] || null;
}

function compareShapeStable(a, b) {
  if (Number(b && b.votes || 0) !== Number(a && a.votes || 0)) {
    return Number(b && b.votes || 0) - Number(a && a.votes || 0);
  }
  if (Number(b && b.lower120 || 0) !== Number(a && a.lower120 || 0)) {
    return Number(b && b.lower120 || 0) - Number(a && a.lower120 || 0);
  }
  if (Number(b && b.score || 0) !== Number(a && a.score || 0)) {
    return Number(b && b.score || 0) - Number(a && a.score || 0);
  }
  if (Number(b && b.rate120 || 0) !== Number(a && a.rate120 || 0)) {
    return Number(b && b.rate120 || 0) - Number(a && a.rate120 || 0);
  }
  const missA = Number(a && a.missStreak30 || 0);
  const missB = Number(b && b.missStreak30 || 0);
  if (missA !== missB) return missA - missB;
  return String((a && a.code) || "").localeCompare(String((b && b.code) || ""), "zh-CN");
}

function rankShapeFamily(history, shapeKey, topN) {
  try {
    if (typeof shapeLib.rankShapes !== "function") return [];
    return shapeLib.rankShapes(history, shapeKey, topN, 4) || [];
  } catch (err) {
    return [];
  }
}

function slimShapeCandidate(group, selectedCode, selectedKey) {
  const code = cleanCode(group && group.code);
  const shapeKey = String(group && group.shapeKey || "");
  return {
    code,
    shapeKey,
    label: String(group && group.label || ""),
    votes: Number(group && group.votes || 0),
    score: Number(group && group.score || 0),
    rate30: Number(group && group.rate30 || 0),
    rate120: Number(group && group.rate120 || 0),
    lower120: Number(group && group.lower120 || 0),
    missStreak30: Number(group && group.missStreak30 || 0),
    sample120: Number(group && group.sample120 || 0),
    selected: !!(selectedCode && code === selectedCode && shapeKey === String(selectedKey || shapeKey))
  };
}

function extraReferenceRow(extra, key, name) {
  return {
    key,
    name,
    code: extraCode(extra),
    backup: extra && extra.backup != null ? cleanCode(extra.backup) : "",
    rate120: Number(extra && extra.rate120 || 0),
    lower120: Number(extra && extra.lower120 || 0),
    votes: Number(extra && extra.votes || 0),
    stableScore: Number(extra && extra.hardnessScore || extra && extra.rate120 || 0),
    selected: true
  };
}

/** 近120 跑单双/大小/混合三路；正框只用单双或大小，混合仅进参考。 */
function rankReferenceShapes(history) {
  const sliced = Array.isArray(history) ? history.slice(0, 120) : [];
  const ranked = [];
  ranked.push.apply(ranked, rankShapeFamily(sliced, "shapeParity", 8));
  ranked.push.apply(ranked, rankShapeFamily(sliced, "shapeSize", 8));
  ranked.push.apply(ranked, rankShapeFamily(sliced, "shapeMixed", 8));
  const usable = ranked.filter((group) => {
    const code = cleanCode(group && group.code);
    return isShapeCode(code) && isAllowedShapeKey(group.shapeKey) && FIXED_SHAPE_CODES.indexOf(code) < 0;
  });
  usable.forEach(normalizeVoteRow);
  usable.sort(compareShapeStable);
  return usable;
}

function inferShapeKeyFromCode(code) {
  if (typeof shapeLib.inferShapeKey === "function") {
    const inferred = shapeLib.inferShapeKey(code);
    if (isAllowedShapeKey(inferred)) return inferred;
  }
  const text = cleanCode(code);
  const hasSize = /[大小]/.test(text);
  const hasParity = /[单双]/.test(text);
  if (hasSize && hasParity) return "shapeMixed";
  if (hasSize) return "shapeSize";
  if (hasParity) return "shapeParity";
  return "";
}

function resolveVotedShapeKey(shapeKey, code) {
  if (isAllowedShapeKey(shapeKey)) return String(shapeKey);
  const inferred = inferShapeKeyFromCode(code);
  return isAllowedShapeKey(inferred) ? inferred : "shapeMixed";
}

function shapeFamilyLabel(shapeKey) {
  if (shapeKey === "shapeSize") return "大小";
  if (shapeKey === "shapeParity") return "单双";
  if (shapeKey === "shapeMixed") return "混合";
  return "形态";
}

function isFormalShapeKey(shapeKey) {
  return shapeKey === "shapeSize" || shapeKey === "shapeParity";
}

function familyShapePool(ranked, shapeKey) {
  return (Array.isArray(ranked) ? ranked : []).filter((row) => String(row && row.shapeKey || "") === shapeKey);
}

function pickVotedShape(history, period) {
  const ranked = rankReferenceShapes(history);
  if (!ranked.length) return { picked: null, ranked };
  const parity = assignFirstPlaceVotes(familyShapePool(ranked, "shapeParity"));
  const size = assignFirstPlaceVotes(familyShapePool(ranked, "shapeSize"));
  const locks = readFlexLocks();
  const oldKey = String(asObject(locks.shape).shapeKey || "");
  let pool;
  if (oldKey === "shapeParity" && parity.length) pool = parity;
  else if (oldKey === "shapeSize" && size.length) pool = size;
  else {
    const parityTop = parity[0];
    const sizeTop = size[0];
    if (!parityTop) pool = size;
    else if (!sizeTop) pool = parity;
    else if (Number(parityTop.votes || 0) !== Number(sizeTop.votes || 0)) {
      pool = Number(parityTop.votes || 0) > Number(sizeTop.votes || 0) ? parity : size;
    } else {
      pool = Number(parityTop.rate120 || 0) >= Number(sizeTop.rate120 || 0) ? parity : size;
    }
  }
  const flexed = applyFlexSwitch("shape", pool, period, { votesReady: true });
  const picked = flexed.picked || pool[0] || null;
  if (picked && locks.shape) locks.shape.shapeKey = String(picked.shapeKey || "");
  return { picked, ranked };
}

function pickDataShape(history) {
  return pickVotedShape(history).picked;
}

function buildReferenceModels(extras, shapeRanked, selectedShape) {
  const selectedCode = cleanCode(selectedShape && selectedShape.code);
  const selectedKey = String(selectedShape && selectedShape.shapeKey || "");
  const byFamily = { shapeParity: [], shapeSize: [], shapeMixed: [] };
  (Array.isArray(shapeRanked) ? shapeRanked : []).forEach((group) => {
    const key = String(group && group.shapeKey || "");
    if (!byFamily[key]) return;
    if (byFamily[key].length >= 3) return;
    byFamily[key].push(slimShapeCandidate(group, selectedCode, selectedKey));
  });
  const shapes = []
    .concat(byFamily.shapeParity, byFamily.shapeSize, byFamily.shapeMixed);
  if (selectedCode && !shapes.some((row) => row.selected)) {
    shapes.unshift(slimShapeCandidate(selectedShape, selectedCode, selectedKey));
    shapes[0].selected = true;
  }
  return {
    pickRule: VOTE_PICK_RULE,
    historyWindow: 120,
    shapes,
    extras: [
      extraReferenceRow(extras && extras.anyTwoHeweiHit, "anyTwoHeweiHit", "二数合"),
      extraReferenceRow(extras && extras.anyThreeHeweiHit, "anyThreeHeweiHit", "三数合"),
      extraReferenceRow(extras && extras.threeDigitAvoid, "threeDigitAvoid", "除对数第1组"),
      extraReferenceRow(extras && extras.threeDigitHit, "threeDigitHit", "除对数第2组"),
      extraReferenceRow(extras && extras[TWO_DIGIT_JOINT_KEY], TWO_DIGIT_JOINT_KEY, "除对数俩数")
    ]
  };
}

function snapshotHasReferenceModels(snapshot) {
  const ref = snapshot && snapshot.referenceModels;
  return !!(ref
    && ref.pickRule === VOTE_PICK_RULE
    && Array.isArray(ref.shapes) && ref.shapes.length
    && Array.isArray(ref.extras) && ref.extras.length);
}

function snapshotHasVotedDuishu(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const threeOk = THREE_DIGIT_KEYS.every((key) => {
    const item = items.find((row) => row && row.key === key);
    return !!(item && isThreeDigitCode(item.code) && String(item.name || "").indexOf("投票") >= 0);
  });
  const joint = items.find((row) => row && row.key === TWO_DIGIT_JOINT_KEY);
  const jointOk = !!(joint && isTwoDigitCode(joint.code));
  if (threeOk && jointOk) return true;
  return THREE_DIGIT_KEYS_LEGACY.every((key) => {
    const item = items.find((row) => row && row.key === key);
    return !!(item && isThreeDigitCode(item.code) && String(item.name || "").indexOf("投票") >= 0);
  });
}

/** 只对入选的那一个定位抽掉 1 码；其余三位保留 1234567890 */
function buildShot(items) {
  const list = Array.isArray(items) ? items : [];
  const two = list.find((item) => item && item.key === "anyTwoHeweiHit");
  const three = list.find((item) => item && item.key === "anyThreeHeweiHit");
  const pos = list.find((item) => item && item.kind === "position");
  const wqbs = list.find((item) => item && item.kind === "hewei" && item.key === "wanQianBaiShi");
  const shapes = list.filter((item) => item && item.kind === "derivedReverseShape");
  const duishu = THREE_DIGIT_KEYS.map((key) => {
    const item = list.find((row) => row && row.key === key);
    return extraCode(item);
  }).filter((code) => isThreeDigitCode(code));
  const joint = list.find((item) => item && item.key === TWO_DIGIT_JOINT_KEY);
  const jointCode = extraCode(joint).replace(/\D/g, "").slice(0, 2);
  if (isTwoDigitCode(jointCode)) duishu.push(jointCode);
  const votedShapeItem = list.find((item) => item && item.key === "shapeAvoidData");
  let dataShape = cleanCode(votedShapeItem && votedShapeItem.code);
  if (!isShapeCode(dataShape)) {
    dataShape = shapes.map((item) => cleanCode(item.code)).find((code) => FIXED_SHAPE_CODES.indexOf(code) < 0) || "";
  }
  function remainFor(key) {
    if (pos && pos.key === key) return digitRemainPool(pos.code);
    return digitRemainPool("");
  }
  return (
    "千" + remainFor("wan")
    + "百" + remainFor("qian")
    + "十" + remainFor("bai")
    + "个" + remainFor("shi")
    + "四定二数合" + cleanCode(two && two.code)
    + "三数合" + cleanCode(three && three.code)
    + "千百十个合" + digitRemainPool(wqbs && wqbs.code)
    + (duishu.length ? ("除对数" + duishu.join("-")) : "")
    + "除单单单单除双双双双"
    + (dataShape ? ("除" + dataShape) : "")
    + "各"
  );
}

function decorateExtraItem(extra, key, name, mode, rank) {
  const code = extraCode(extra);
  return {
    rank,
    kind: "extra",
    key,
    side: "recommend",
    name,
    code,
    mode,
    rate30: Number(extra && extra.rate30 || 0),
    rate60: Number(extra && extra.rate60 || 0),
    rate120: Number(extra && extra.rate120 || 0),
    rate300: Number(extra && extra.rate300 || 0),
    lower120: Number(extra && extra.lower120 || 0),
    votes: Number(extra && extra.votes || 0),
    stableScore: Number(extra && extra.hardnessScore || extra && extra.rate120 || 0),
    stageLabel: extra && extra.stage || "",
    confidence: extra && extra.confidence || "",
    recommendable: extra && extra.recommendable !== false
  };
}

function decoratePositionItem(pos, rank) {
  const pct = Number(pos && pos.stats && pos.stats.rate || 0) * 100;
  const votes = Number(pos && pos.votes || 0);
  return {
    rank,
    kind: "position",
    key: pos.key,
    side: "recommend",
    name: pos.label + "排除（投票 · 票" + votes + flexNameTag(pos) + "）",
    code: pos.digit,
    mode: "avoid",
    votes,
    rate120: Number(pos && pos.rate120 || 0),
    lower120: Number(pos && pos.lower120 || 0),
    panelRate: Number(pct.toFixed(2)),
    panelRateSource: pos.stats && pos.stats.source || "",
    panelSample: Number(pos.stats && pos.stats.total || 0)
  };
}

function decorateHeweiItem(code, rank, voteRow) {
  const votes = Number(voteRow && voteRow.votes || 0);
  return {
    rank,
    kind: "hewei",
    key: "wanQianBaiShi",
    side: "recommend",
    name: "千百十个合尾排除（投票 · 票" + votes + flexNameTag(voteRow) + "）",
    code,
    mode: "avoid",
    votes,
    rate120: Number(voteRow && voteRow.rate120 || 0),
    lower120: Number(voteRow && voteRow.lower120 || 0)
  };
}

function decorateShapeItem(code, shapeKey, name, key, rank, group) {
  return {
    rank,
    kind: "derivedReverseShape",
    key,
    side: "recommend",
    name,
    code,
    shapeKey,
    mode: "avoid",
    shapeScopeLength: 4,
    attack: "除" + code,
    rate30: Number(group && group.rate30 || 0),
    rate60: Number(group && group.rate60 || 0),
    rate120: Number(group && group.rate120 || 0),
    rate300: Number(group && group.rate300 || 0),
    lower120: Number(group && group.lower120 || 0),
    stableScore: Number((group && (group.score || group.rate120)) || 0),
    votes: Number(group && group.votes || 0),
    stageLabel: (group && group.stage) || "",
    missStreak30: Number(group && group.missStreak30 || 0),
    derivedFrom: key === "shapeAvoidData" ? "shape-backend-vote" : "shape-fixed-parity"
  };
}

function buildFourDingFrameworkSnapshot(prediction, history) {
  const safeHistory = Array.isArray(history)
    ? history.filter((record) => record && Array.isArray(record.numbers))
    : [];
  const period = String((prediction && prediction.period) || (safeHistory[0] && safeHistory[0].period) || "");
  const extras = ensureExtraKills(prediction, safeHistory);
  const repairReport = [];
  const items = [];

  const posPick = pickVotedPosition(prediction, safeHistory, period);
  if (!posPick || !/^\d$/.test(posPick.digit)) {
    repairReport.push("四定框架缺投票定位码");
  } else {
    items.push(decoratePositionItem(posPick, items.length + 1));
  }

  const auxDigits = collectAuxSingleDigits(prediction);
  const wqbsVote = pickVotedWanQianBaiShi(safeHistory, wanQianBaiShiDigit(prediction), period, auxDigits);
  const wqbs = wqbsVote && wqbsVote.code;
  if (!/^\d$/.test(wqbs)) {
    repairReport.push("四定框架缺四数合尾：" + (wqbs || "--"));
  } else {
    items.push(decorateHeweiItem(wqbs, items.length + 1, wqbsVote));
  }

  const twoVote = pickVotedExtraHit(
    safeHistory,
    typeof extraFormula.anyTwoHeweiCandidates === "function" ? extraFormula.anyTwoHeweiCandidates() : [],
    2,
    extraCode(extras.anyTwoHeweiHit),
    isAnyTwoHeweiCode,
    period,
    "twoHewei",
    collectAuxSetCodes(extras, isAnyTwoHeweiCode)
  );
  const two = Object.assign({}, extras.anyTwoHeweiHit || {}, {
    code: twoVote && twoVote.code,
    value: twoVote && twoVote.code,
    votes: twoVote && twoVote.votes,
    rate120: twoVote && twoVote.rate120,
    lower120: twoVote && twoVote.lower120
  });
  const twoCode = extraCode(two);
  if (!isAnyTwoHeweiCode(twoCode)) {
    repairReport.push("四定框架缺二数合02468四码：" + (twoCode || "--"));
  } else {
    items.push(decorateExtraItem(two, "anyTwoHeweiHit", "二数合尾02468排1码命中（投票 · 票" + Number(two.votes || 0) + flexNameTag(twoVote) + "）", "hit", items.length + 1));
  }

  const threeVote = pickVotedExtraHit(
    safeHistory,
    capVoteCodes(
      typeof extraFormula.digitSetCandidatesMixedParity === "function" ? extraFormula.digitSetCandidatesMixedParity(5) : [],
      extras.anyThreeHeweiHit,
      8
    ),
    3,
    extraCode(extras.anyThreeHeweiHit),
    (code) => /^\d{5}$/.test(String(code || "")) && new Set(String(code).split("")).size === 5,
    period,
    "threeHewei",
    collectAuxSetCodes(extras, (code) => /^\d{5}$/.test(String(code || "")))
  );
  const three = Object.assign({}, extras.anyThreeHeweiHit || {}, {
    code: threeVote && threeVote.code,
    value: threeVote && threeVote.code,
    votes: threeVote && threeVote.votes,
    rate120: threeVote && threeVote.rate120,
    lower120: threeVote && threeVote.lower120
  });
  const threeCode = extraCode(three);
  if (!/^\d{5}$/.test(threeCode) || new Set(threeCode.split("")).size !== 5) {
    repairReport.push("四定框架缺三数合五码：" + (threeCode || "--"));
  } else {
    items.push(decorateExtraItem(three, "anyThreeHeweiHit", "三数合尾宽命中（投票 · 票" + Number(three.votes || 0) + flexNameTag(threeVote) + "）", "hit", items.length + 1));
  }

  const duishuVoted = {};
  const duishuRanked = rankVotedExtraAvoid(safeHistory, threeDigitAvoidCandidates());
  const firstDuishu = applyFlexSwitch("duishu1", duishuRanked, period, { votesReady: true }).picked;
  const firstDuishuCode = rowCode(firstDuishu);
  const secondPool = duishuRanked.filter((row) => {
    const code = rowCode(row);
    return code && code !== firstDuishuCode && sharedDigitCount(code, firstDuishuCode) <= MAX_DUISHU_SHARE;
  });
  const secondFallback = duishuRanked.filter((row) => {
    const code = rowCode(row);
    return code && code !== firstDuishuCode;
  });
  const secondDuishu = applyFlexSwitch(
    "duishu2",
    secondPool.length ? secondPool : secondFallback,
    period,
    { votesReady: true }
  ).picked;
  if (firstDuishuCode && rowCode(secondDuishu) && sharedDigitCount(firstDuishuCode, rowCode(secondDuishu)) > MAX_DUISHU_SHARE) {
    repairReport.push("对数两组仍共享2码，已尽量错开：" + firstDuishuCode + "/" + rowCode(secondDuishu));
  }
  const pickedDuishu = [firstDuishu, secondDuishu];
  THREE_DIGIT_KEYS.forEach((key, index) => {
    const extra = extras[key];
    const vote = pickedDuishu[index] || null;
    const code = (vote && vote.code) || extraCode(extra);
    if (!isThreeDigitCode(code)) {
      repairReport.push("四定框架缺" + THREE_DIGIT_LABELS[key] + "：" + (code || "--"));
      return;
    }
    const merged = Object.assign({}, extra || {}, {
      code,
      value: code,
      votes: Number(vote && vote.votes || 0),
      rate120: Number(vote && vote.rate120 || extra && extra.rate120 || 0),
      lower120: Number(vote && vote.lower120 || extra && extra.lower120 || 0),
      flexReason: vote && vote.flexReason,
      flexHoldCount: vote && vote.flexHoldCount
    });
    duishuVoted[key] = merged;
    items.push(decorateExtraItem(
      merged,
      key,
      THREE_DIGIT_LABELS[key] + "（投票 · 票" + Number(merged.votes || 0) + flexNameTag(vote) + "）",
      "avoid",
      items.length + 1
    ));
  });

  const jointTwo = pickVotedJointTwo(prediction, extras, safeHistory, period)
    || resolveJointTwoDigit(prediction, extras);
  if (!jointTwo || !isTwoDigitCode(jointTwo.code)) {
    repairReport.push("四定框架缺合选俩数对数");
  } else {
    const mergedJoint = {
      code: jointTwo.code,
      value: jointTwo.code,
      votes: Number(jointTwo.votes || 0),
      rate120: Number(jointTwo.rate120 || 0),
      lower120: Number(jointTwo.lower120 || 0),
      flexReason: jointTwo.flexReason,
      flexHoldCount: jointTwo.flexHoldCount
    };
    items.push(decorateExtraItem(
      mergedJoint,
      TWO_DIGIT_JOINT_KEY,
      TWO_DIGIT_JOINT_LABEL + "（来自" + String(jointTwo.sourceLabel || jointTwo.source || "合选") + flexNameTag(jointTwo) + "）",
      "avoid",
      items.length + 1
    ));
  }

  FIXED_SHAPE_CODES.forEach((code, index) => {
    items.push(decorateShapeItem(
      code,
      "shapeParity",
      "形态杀码固定 · " + code,
      "shapeAvoidFixed" + (index + 1),
      items.length + 1,
      null
    ));
  });

  let dataShape = null;
  let shapeRanked = [];
  try {
    const votePack = pickVotedShape(safeHistory, period);
    shapeRanked = votePack.ranked;
    dataShape = votePack.picked;
  } catch (err) {
    repairReport.push("形态投票选码失败：" + String(err && err.message || err));
  }
  if (!dataShape || !isShapeCode(cleanCode(dataShape.code))) {
    repairReport.push("四定框架缺投票形态");
  } else {
    const votedCode = cleanCode(dataShape.code);
    const votedKey = resolveVotedShapeKey(dataShape.shapeKey, votedCode);
    items.push(decorateShapeItem(
      votedCode,
      votedKey,
      "形态杀码投票 · " + shapeFamilyLabel(votedKey) + " · 票" + Number(dataShape.votes || 0) + flexNameTag(dataShape),
      "shapeAvoidData",
      items.length + 1,
      dataShape
    ));
  }

  items.forEach((item, index) => {
    item.rank = index + 1;
  });

  const shot = buildShot(items, prediction);
  return {
    version: FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION,
    kind: "fourDingFramework",
    label: "四定框架",
    itemCount: items.length,
    framework: {
      positionCount: 1,
      wanQianBaiShiCount: 1,
      anyTwoHeweiCount: 1,
      anyThreeHeweiCount: 1,
      threeDigitAvoidCount: 2,
      twoDigitJointCount: 1,
      shapeFixedCount: 2,
      shapeDataCount: 1,
      total: FOUR_DING_FRAMEWORK_ITEM_COUNT
    },
    shot,
    items,
    referenceModels: Object.assign(
      buildReferenceModels(Object.assign({}, extras, {
        anyTwoHeweiHit: two,
        anyThreeHeweiHit: three,
        threeDigitAvoid: duishuVoted.threeDigitAvoid || extras.threeDigitAvoid,
        threeDigitHit: duishuVoted.threeDigitHit || extras.threeDigitHit,
        [TWO_DIGIT_JOINT_KEY]: jointTwo || extras[TWO_DIGIT_JOINT_KEY]
      }), shapeRanked, dataShape),
      {
        position: posPick ? {
          key: posPick.key,
          label: posPick.label,
          code: posPick.digit,
          votes: Number(posPick.votes || 0),
          rate120: Number(posPick.rate120 || 0),
          lower120: Number(posPick.lower120 || 0),
          selected: true
        } : null,
        hewei: wqbsVote ? {
          key: "wanQianBaiShi",
          name: "千百十个合",
          code: wqbsVote.code,
          votes: Number(wqbsVote.votes || 0),
          rate120: Number(wqbsVote.rate120 || 0),
          lower120: Number(wqbsVote.lower120 || 0),
          selected: true
        } : null
      }
    ),
    repairReport,
    predictionGeneratedAt: new Date().toISOString()
  };
}

function fourDingFrameworkShotLooksValid(shot) {
  const text = String(shot || "");
  if (!text || text.charAt(0) !== "千") return false;
  return text.indexOf("四定二数合") > 0
    && text.indexOf("三数合") > 0
    && text.indexOf("千百十个合") > 0
    && text.indexOf("百十个合") === text.indexOf("千百十个合") + 1
    && (/除对数\d{3}-\d{3}-\d{3}/.test(text) || /除对数\d{3}-\d{3}-\d{2}/.test(text))
    && text.indexOf("除单单单单除双双双双") > 0
    && /各$/.test(text);
}

function fourDingFrameworkSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const posCount = formal.filter((item) => item.kind === "position" && POSITIONS.some((pos) => pos.key === item.key) && /^\d$/.test(String(item.code || ""))).length;
  const wqbsCount = formal.filter((item) => item.kind === "hewei" && item.key === "wanQianBaiShi" && /^\d$/.test(String(item.code || ""))).length;
  const twoCount = formal.filter((item) => item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const threeCount = formal.filter((item) => item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  const threeDigitCount = formal.filter((item) => item.kind === "extra" && THREE_DIGIT_KEYS.includes(item.key) && isThreeDigitCode(item.code)).length;
  const jointTwoCount = formal.filter((item) => item.kind === "extra" && item.key === TWO_DIGIT_JOINT_KEY && isTwoDigitCode(item.code)).length;
  const legacyThreeCount = formal.filter((item) => item.kind === "extra" && THREE_DIGIT_KEYS_LEGACY.includes(item.key) && isThreeDigitCode(item.code)).length;
  const duishuOk = (threeDigitCount === 2 && jointTwoCount === 1) || (legacyThreeCount === 3 && jointTwoCount === 0);
  const shapeItems = formal.filter((item) => (
    item.kind === "derivedReverseShape"
    && isShapeCode(item.code)
    && isAllowedShapeKey(item.shapeKey)
  ));
  const shapeCodes = shapeItems.map((item) => cleanCode(item.code));
  const hasFixed = FIXED_SHAPE_CODES.every((code) => shapeCodes.indexOf(code) >= 0);
  const hasData = shapeCodes.some((code) => FIXED_SHAPE_CODES.indexOf(code) < 0);
  return !!(
    snapshot
    && snapshot.version === FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION
    && formal.length === FOUR_DING_FRAMEWORK_ITEM_COUNT
    && items.length === FOUR_DING_FRAMEWORK_ITEM_COUNT
    && posCount === 1
    && wqbsCount === 1
    && twoCount === 1
    && threeCount === 1
    && duishuOk
    && shapeItems.length === 3
    && hasFixed
    && hasData
    && fourDingFrameworkShotLooksValid(snapshot.shot)
  );
}

function ensurePredictionFourDingFrameworkSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (fourDingFrameworkSnapshotIsComplete(prediction.fourDingFramework)) {
    const sealed = prediction.allSnapshotsSealed === true || !!prediction.snapshotsSealedAt;
    if (!sealed && (!snapshotHasReferenceModels(prediction.fourDingFramework) || !snapshotHasVotedDuishu(prediction.fourDingFramework))) {
      prediction.fourDingFramework = buildFourDingFrameworkSnapshot(prediction, history);
      prediction.fourDingFrameworkItems = prediction.fourDingFramework.items;
      prediction.fourDingFrameworkRepairReport = prediction.fourDingFramework.repairReport;
      if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") {
        prediction.recommendationMeta = {};
      }
      prediction.recommendationMeta.fourDingFrameworkSnapshotVersion = prediction.fourDingFramework.version;
    }
    return prediction.fourDingFramework;
  }
  prediction.fourDingFramework = buildFourDingFrameworkSnapshot(prediction, history);
  prediction.fourDingFrameworkItems = prediction.fourDingFramework.items;
  prediction.fourDingFrameworkRepairReport = prediction.fourDingFramework.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") {
    prediction.recommendationMeta = {};
  }
  prediction.recommendationMeta.fourDingFrameworkSnapshotVersion = prediction.fourDingFramework.version;
  return prediction.fourDingFramework;
}

module.exports = {
  FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION,
  FOUR_DING_FRAMEWORK_ITEM_COUNT,
  bindRuntime,
  buildShot,
  buildFourDingFrameworkSnapshot,
  fourDingFrameworkSnapshotIsComplete,
  ensurePredictionFourDingFrameworkSnapshot,
  rankReferenceShapes,
  pickDataShape
};
