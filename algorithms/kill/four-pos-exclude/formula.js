"use strict";

/**
 * 四位各排1码 + 旁挂2个三定形态（千百十）。
 * 定位与形态各跑各的、各自真实回测，不算整组。
 * 只用共享 RDS history，不读其它算法 state。
 */

const {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  MODEL_LOW_HIT_RATE,
  stableWindowStage,
  stableWindowScore,
  wilsonLowerRate
} = require("../../_shared/formula_kit");

const VERSION = "four-pos-exclude-v7-show-all";
const LABEL = "四位各排1码·近60";
const LONG_RATE_FLOOR = 88.5;
const LONG_LOWER_FLOOR = 88;
const MISS_LEAVE = 1;
const LEGACY_VERSIONS = Object.freeze([
  "four-pos-exclude-v7-show-all",
  "four-pos-exclude-v6-primary-only",
  "four-pos-exclude-v5-top1-r60",
  "four-pos-exclude-v4-flex-short",
  "four-pos-exclude-v3-long5k"
]);
const SHAPE_COUNT = 2;
const SHAPE_SCOPE = Object.freeze([0, 1, 2]); // 千百十
const SHAPE_SCOPE_LABEL = "千百十";

const POSITION_LIST = Object.freeze([
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 }
]);

const RATE_WINDOWS = Object.freeze([
  30,
  60,
  120,
  MODEL_STABLE_WINDOW,
  MODEL_LONG_WINDOW
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
  const weighted = stableWindowScore(r30, r60, r120, r300, rLong, 3.2);
  const lower120 = wilsonLowerRate(r120.hit, r120.total, 1.44);
  const lower300 = wilsonLowerRate(r300.hit, r300.total, 1.44);
  const lowerLong = wilsonLowerRate(rLong.hit, rLong.total, 1.44);
  let score = weighted.score;
  if (Number(r30.rate || 0) < MODEL_LOW_HIT_RATE) score -= 2.4;
  if (Number(r30.missStreak || 0) >= 1) score -= 3.2;
  // 长窗只作资格，近窗决定谁上场，避免 5k 窗把同一码粘死
  score += Math.max(0, Number(r30.rate || 0) - 88) * 0.14;
  score += Math.max(0, Number(r60.rate || 0) - 88) * 0.08;
  score += Math.max(0, lowerLong - 88) * 0.05;
  score += Math.max(0, Number(rLong.rate || 0) - 90) * 0.03;
  score += Math.max(0, lower300 - 88) * 0.04;
  score += Math.max(0, lower120 - 88) * 0.06;
  return {
    digit: String(digit),
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    r30,
    r60,
    r120,
    r300,
    rLong,
    lower120: Number(lower120.toFixed(2)),
    lower300: Number(lower300.toFixed(2)),
    lowerLong: Number(lowerLong.toFixed(2))
  };
}

function leadingHitStreak(digit, records, index, limit) {
  const cap = Math.max(1, Number(limit || 40));
  const sliced = (records || []).slice(0, cap);
  let n = 0;
  for (let i = 0; i < sliced.length; i += 1) {
    const actual = actualDigit(sliced[i], index);
    if (!/^\d$/.test(actual)) continue;
    if (actual !== String(digit)) n += 1;
    else break;
  }
  return n;
}

function compareShortWindow(a, b) {
  const am = Number(a && a.r30 && a.r30.missStreak || 0);
  const bm = Number(b && b.r30 && b.r30.missStreak || 0);
  if (am !== bm) return am - bm;
  const ar60 = Number(a && a.r60 && a.r60.rate || 0);
  const br60 = Number(b && b.r60 && b.r60.rate || 0);
  if (ar60 !== br60) return br60 - ar60;
  const ar30 = Number(a && a.r30 && a.r30.rate || 0);
  const br30 = Number(b && b.r30 && b.r30.rate || 0);
  if (ar30 !== br30) return br30 - ar30;
  const al120 = Number(a && a.lower120 || 0);
  const bl120 = Number(b && b.lower120 || 0);
  if (al120 !== bl120) return bl120 - al120;
  return String(a && a.digit || "").localeCompare(String(b && b.digit || ""));
}

function pickFromSafePool(candidates) {
  const safe = (candidates || []).filter((item) => item && /^\d$/.test(item.digit));
  const eligible = safe.filter((item) => (
    Number(item.rLong && item.rLong.rate || 0) >= LONG_RATE_FLOOR
    && Number(item.lowerLong || 0) >= LONG_LOWER_FLOOR
  ));
  const pool = eligible.length >= 2 ? eligible.slice() : safe.slice();
  pool.sort(compareShortWindow);
  const fresh = pool.filter((item) => Number(item.r30 && item.r30.missStreak || 0) < MISS_LEAVE);
  const ranked = fresh.length ? fresh : pool;
  const best = ranked[0] || null;
  const reason = eligible.length >= 2 ? "long-eligible-r60" : "r60-fallback";
  const backup = ranked.find((item) => best && item.digit !== best.digit)
    || safe.find((item) => best && item.digit !== best.digit)
    || null;
  return { best, backup, reason };
}

function pickPositionExclude(history, position) {
  const safeHistory = asHistory(history);
  const index = Number(position.index);
  const candidates = DIGIT_POOL.map((digit) => {
    const windows = digitRateMultiWindow(digit, safeHistory, index, RATE_WINDOWS);
    const scored = scoreDigit(digit, windows);
    scored.rideStreak = leadingHitStreak(digit, safeHistory, index, 40);
    return scored;
  });
  const picked = pickFromSafePool(candidates);
  const best = picked.best;
  const backup = picked.backup;
  return {
    key: position.key,
    label: position.label,
    index,
    code: best && /^\d$/.test(best.digit) ? best.digit : "",
    backup: backup && /^\d$/.test(backup.digit) ? backup.digit : "",
    score: best ? best.score : null,
    stage: best ? best.stage : stableWindowStage(0),
    rate30: best ? Number(Number(best.r30.rate || 0).toFixed(2)) : null,
    rate60: best ? Number(Number(best.r60.rate || 0).toFixed(2)) : null,
    rate120: best ? Number(Number(best.r120.rate || 0).toFixed(2)) : null,
    rate300: best ? Number(Number(best.r300.rate || 0).toFixed(2)) : null,
    rateLong: best ? Number(Number(best.rLong.rate || 0).toFixed(2)) : null,
    lower120: best ? best.lower120 : null,
    lower300: best ? best.lower300 : null,
    lowerLong: best ? best.lowerLong : null,
    sample120: best ? Number(best.r120.total || 0) : 0,
    sampleLong: best ? Number(best.rLong.total || 0) : 0,
    longWindow: MODEL_LONG_WINDOW,
    missStreak30: best ? Number(best.r30.missStreak || 0) : 0,
    rideStreak: best ? Number(best.rideStreak || 0) : 0,
    algorithm: "four-pos-exclude",
    reason: picked.reason || "long-eligible-r60"
  };
}

function pickPrimaryKey(items) {
  const rows = (items || []).filter((item) => item && /^\d$/.test(String(item.code || "")));
  rows.sort((a, b) => {
    const am = Number(a.missStreak30 || 0);
    const bm = Number(b.missStreak30 || 0);
    if (am !== bm) return am - bm;
    const a60 = Number(a.rate60 || 0);
    const b60 = Number(b.rate60 || 0);
    if (a60 !== b60) return b60 - a60;
    const a30 = Number(a.rate30 || 0);
    const b30 = Number(b.rate30 || 0);
    if (a30 !== b30) return b30 - a30;
    return String(a.key || "").localeCompare(String(b.key || ""));
  });
  return rows[0] ? String(rows[0].key) : "";
}

function actualShapeCode(record, shapeKey, scopeIndexes) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  const scope = Array.isArray(scopeIndexes) && scopeIndexes.length ? scopeIndexes : SHAPE_SCOPE;
  const parts = [];
  for (let i = 0; i < scope.length; i += 1) {
    const n = Number(nums[scope[i]]);
    if (!Number.isFinite(n)) return "";
    if (shapeKey === "shapeSize") parts.push(n >= 5 ? "大" : "小");
    else if (shapeKey === "shapeParity") parts.push(n % 2 ? "单" : "双");
    else return "";
  }
  return parts.join("");
}

function enumerateShapeCodes(shapeKey, length) {
  const pool = shapeKey === "shapeSize" ? ["大", "小"] : ["单", "双"];
  const len = Math.max(1, Number(length || 3));
  let codes = [""];
  for (let i = 0; i < len; i += 1) {
    const next = [];
    codes.forEach((prefix) => {
      pool.forEach((ch) => next.push(prefix + ch));
    });
    codes = next;
  }
  return codes;
}

function shapeRateMultiWindow(code, shapeKey, records, scopeIndexes, limits) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((n) => Math.max(1, Number(n || 120))))];
  const maxLimit = Math.max(...lims);
  const sliced = (records || []).slice(0, maxLimit);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0, _currentRun: 0, _leading: true };
  });
  for (let i = 0; i < sliced.length; i += 1) {
    const actual = actualShapeCode(sliced[i], shapeKey, scopeIndexes);
    if (!actual || actual.length !== String(code).length) continue;
    const ok = actual !== String(code);
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

function scoreShapeCandidate(code, shapeKey, rateWindows) {
  const r30 = rateWindows[30] || { total: 0, hit: 0, rate: 0, missStreak: 0 };
  const r60 = rateWindows[60] || r30;
  const r120 = rateWindows[120] || r60;
  const r300 = rateWindows[MODEL_STABLE_WINDOW] || r120;
  const rLong = rateWindows[MODEL_LONG_WINDOW] || r300;
  const weighted = stableWindowScore(r30, r60, r120, r300, rLong, 2.8);
  const lower120 = wilsonLowerRate(r120.hit, r120.total, 1.44);
  const lower300 = wilsonLowerRate(r300.hit, r300.total, 1.44);
  let score = weighted.score;
  if (Number(r30.rate || 0) < 85) score -= 2.0;
  if (Number(r30.missStreak || 0) >= 2) score -= 1.6;
  score += Math.max(0, lower120 - 85) * 0.08;
  score += Math.max(0, lower300 - 85) * 0.05;
  return {
    code: String(code),
    shapeKey,
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    r30,
    r60,
    r120,
    r300,
    rLong,
    lower120: Number(lower120.toFixed(2)),
    lower300: Number(lower300.toFixed(2))
  };
}

function pickStableShapes(history, count) {
  const safeHistory = asHistory(history);
  const want = Math.max(1, Number(count || SHAPE_COUNT));
  const scope = SHAPE_SCOPE.slice();
  const len = scope.length;
  const candidates = [];
  ["shapeSize", "shapeParity"].forEach((shapeKey) => {
    enumerateShapeCodes(shapeKey, len).forEach((code) => {
      const windows = shapeRateMultiWindow(code, shapeKey, safeHistory, scope, RATE_WINDOWS);
      candidates.push(scoreShapeCandidate(code, shapeKey, windows));
    });
  });
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
    if (Number(b.r120.rate || 0) !== Number(a.r120.rate || 0)) return Number(b.r120.rate || 0) - Number(a.r120.rate || 0);
    if (Number(b.rLong.rate || 0) !== Number(a.rLong.rate || 0)) return Number(b.rLong.rate || 0) - Number(a.rLong.rate || 0);
    return String(a.code).localeCompare(String(b.code), "zh-CN");
  });
  const picked = [];
  const used = new Set();
  candidates.forEach((item) => {
    if (picked.length >= want) return;
    if (!item || !item.code || used.has(item.shapeKey + ":" + item.code)) return;
    used.add(item.shapeKey + ":" + item.code);
    picked.push({
      key: "shape" + (picked.length + 1),
      rank: picked.length + 1,
      label: item.shapeKey === "shapeSize" ? "大小形态" : "单双形态",
      shapeKey: item.shapeKey,
      code: item.code,
      scopeIndexes: scope.slice(),
      scopeLabel: SHAPE_SCOPE_LABEL,
      score: item.score,
      stage: item.stage,
      rate30: Number(Number(item.r30.rate || 0).toFixed(2)),
      rate60: Number(Number(item.r60.rate || 0).toFixed(2)),
      rate120: Number(Number(item.r120.rate || 0).toFixed(2)),
      rate300: Number(Number(item.r300.rate || 0).toFixed(2)),
      rateLong: Number(Number(item.rLong.rate || 0).toFixed(2)),
      lower120: item.lower120,
      sample120: Number(item.r120.total || 0),
      sampleLong: Number(item.rLong.total || 0),
      missStreak30: Number(item.r30.missStreak || 0),
      algorithm: "four-pos-exclude-shape",
      reason: "top-stable-any-shape-type"
    });
  });
  return picked;
}

function positionsComplete(snapshot) {
  const items = Array.isArray(snapshot && snapshot.items) ? snapshot.items : [];
  if (items.length !== 4) return false;
  return items.every((item) => item && /^\d$/.test(String(item.code || "")) && POSITION_LIST.some((p) => p.key === item.key));
}

function shapesComplete(snapshot) {
  const shapes = Array.isArray(snapshot && snapshot.shapeItems) ? snapshot.shapeItems : [];
  if (shapes.length !== SHAPE_COUNT) return false;
  return shapes.every((item) => {
    const code = String(item && item.code || "");
    const key = String(item && item.shapeKey || "");
    if (key === "shapeSize") return /^[大小]{3}$/.test(code);
    if (key === "shapeParity") return /^[单双]{3}$/.test(code);
    return false;
  });
}

function snapshotIsComplete(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return false;
  const ver = String(snapshot.version || "");
  if (LEGACY_VERSIONS.indexOf(ver) < 0) return false;
  return positionsComplete(snapshot) && shapesComplete(snapshot);
}

function buildSnapshot(history, options) {
  const opts = options && typeof options === "object" ? options : {};
  const safeHistory = asHistory(history);
  const items = POSITION_LIST.map((position) => pickPositionExclude(safeHistory, position));
  const shapeItems = pickStableShapes(safeHistory, SHAPE_COUNT);
  const primaryKey = pickPrimaryKey(items);
  items.forEach((item) => {
    if (item && typeof item === "object") item.featured = item.key === primaryKey;
  });
  return {
    version: VERSION,
    kind: "fourPosExclude",
    label: LABEL,
    itemCount: 4,
    shapeCount: SHAPE_COUNT,
    primaryKey,
    sealed: false,
    sealedAt: null,
    sealedBaseUid: null,
    items,
    shapeItems,
    source: {
      officialHistory: "rds-shared-read-only",
      historyCount: safeHistory.length,
      builtAt: new Date().toISOString()
    },
    note: opts.note || "四位都显示杀码；主推位数看面板设置"
  };
}

function sealSnapshot(snapshot, prediction) {
  if (!snapshot || typeof snapshot !== "object") return snapshot;
  // 已有完整封签则不动；半成品（缺 hash/期号）允许重签
  if (snapshot.sealed === true && snapshot.sealHash && snapshot.sealedBasePeriod && snapshot.predictionGeneratedAt) {
    return snapshot;
  }
  snapshot.sealed = false;
  snapshot.sealedAt = new Date().toISOString();
  snapshot.predictionGeneratedAt = prediction && prediction.generatedAt != null ? String(prediction.generatedAt) : "";
  snapshot.sealedBaseUid = prediction && prediction.baseUid != null ? String(prediction.baseUid) : "";
  snapshot.sealedBasePeriod = prediction && prediction.basePeriod != null ? String(prediction.basePeriod) : "";
  snapshot.sealedTargetPeriod = prediction && prediction.targetPeriod != null ? String(prediction.targetPeriod) : null;
  // sealHash 由宿主 runner 的 snapshotSealHash 写入；此处先占位，避免半成品 sealed=true
  if (!snapshot.sealHash) snapshot.sealHash = "";
  snapshot.sealed = true;
  return snapshot;
}

function evaluatePositions(snapshot, actualRecord) {
  const numbers = actualRecord && Array.isArray(actualRecord.numbers)
    ? actualRecord.numbers.map(String)
    : [];
  const items = Array.isArray(snapshot && snapshot.items) ? snapshot.items : [];
  const primaryKey = String(snapshot && snapshot.primaryKey || "");
  const positions = {};
  let hit = 0;
  let judged = 0;
  POSITION_LIST.forEach((pos) => {
    const item = items.find((row) => row && row.key === pos.key) || null;
    const code = item && /^\d$/.test(String(item.code || "")) ? String(item.code) : "";
    const actual = numbers[pos.index] != null ? String(numbers[pos.index]) : "";
    const featured = primaryKey ? pos.key === primaryKey : !!(item && item.featured);
    let success = null;
    if (/^\d$/.test(code) && /^\d$/.test(actual)) {
      success = code !== actual;
      judged += 1;
      if (success) hit += 1;
    }
    positions[pos.key] = {
      label: pos.label,
      code,
      backup: item && item.backup != null ? String(item.backup) : "",
      actualDigit: actual,
      featured,
      ignored: false,
      success
    };
  });
  const complete = positionsComplete(snapshot);
  const sealed = !!(snapshot && snapshot.sealed === true);
  return {
    version: VERSION,
    kind: "fourPosExclude",
    label: LABEL,
    total: 4,
    primaryKey,
    hit,
    passed: hit,
    judged,
    success: judged === 4 ? hit === 4 : null,
    rate: judged ? Number(((hit * 100) / judged).toFixed(2)) : null,
    positions,
    sealed,
    sealedAt: snapshot && snapshot.sealedAt ? String(snapshot.sealedAt) : null,
    truthEligible: complete && sealed && judged === 4,
    complete
  };
}

function evaluateShapes(snapshot, actualRecord) {
  const shapeItems = Array.isArray(snapshot && snapshot.shapeItems) ? snapshot.shapeItems : [];
  const items = [];
  let hit = 0;
  let judged = 0;
  shapeItems.forEach((row, index) => {
    const shapeKey = String(row && row.shapeKey || "");
    const code = String(row && row.code || "");
    const scope = Array.isArray(row && row.scopeIndexes) && row.scopeIndexes.length
      ? row.scopeIndexes.map(Number)
      : SHAPE_SCOPE.slice();
    const actual = actualShapeCode(actualRecord, shapeKey, scope);
    let success = null;
    const validCode = (shapeKey === "shapeSize" && /^[大小]{3}$/.test(code))
      || (shapeKey === "shapeParity" && /^[单双]{3}$/.test(code));
    if (validCode && actual && actual.length === code.length) {
      success = actual !== code;
      judged += 1;
      if (success) hit += 1;
    }
    items.push({
      key: row && row.key ? String(row.key) : ("shape" + (index + 1)),
      rank: Number(row && row.rank || index + 1),
      label: row && row.label ? String(row.label) : (shapeKey === "shapeSize" ? "大小形态" : "单双形态"),
      shapeKey,
      code,
      scopeLabel: row && row.scopeLabel ? String(row.scopeLabel) : SHAPE_SCOPE_LABEL,
      actualShape: actual,
      success
    });
  });
  const complete = shapesComplete(snapshot);
  const sealed = !!(snapshot && snapshot.sealed === true);
  return {
    version: VERSION,
    kind: "fourPosShapes",
    label: "三定形态排除",
    total: SHAPE_COUNT,
    hit,
    passed: hit,
    judged,
    // 各跑各的：不给出“两项全过”整组结论，只保留分项
    success: null,
    rate: judged ? Number(((hit * 100) / judged).toFixed(2)) : null,
    items,
    sealed,
    sealedAt: snapshot && snapshot.sealedAt ? String(snapshot.sealedAt) : null,
    truthEligible: complete && sealed && judged === SHAPE_COUNT,
    complete
  };
}

function evaluateSnapshot(snapshot, actualRecord) {
  const positions = evaluatePositions(snapshot, actualRecord);
  const shapes = evaluateShapes(snapshot, actualRecord);
  return Object.assign({}, positions, {
    shapes
  });
}

module.exports = {
  VERSION,
  LABEL,
  POSITION_LIST,
  SHAPE_COUNT,
  SHAPE_SCOPE,
  pickPositionExclude,
  pickStableShapes,
  buildSnapshot,
  snapshotIsComplete,
  positionsComplete,
  shapesComplete,
  sealSnapshot,
  evaluateSnapshot,
  evaluatePositions,
  evaluateShapes,
  asHistory
};
