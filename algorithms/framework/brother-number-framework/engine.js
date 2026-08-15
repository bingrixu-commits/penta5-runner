"use strict";
/**
 * 取二兄弟框架（L1 封存）：
 * 除定位1 + 除合尾1 + 二数合02468排1码 + 三数合 + 固定取二兄弟 + 除单双形态1
 * 整框 AND：6 项全过才算中。
 * 攻击语：千…百…十…个…四定二数合xxxx三数合xxxxx百十个合…取二兄弟
 * （位/合尾只按本框 items 对应位减码，不用 fourPos 乱填；二兄弟固定取「有」，不投票有/否）
 */

const extraFormula = require("../../extra-kills/formula");

const BROTHER_NUMBER_FRAMEWORK_SNAPSHOT_VERSION =
  "backend-brother-number-framework-snapshot-v1-pos1-hewei1-hewei2-brother-parity";
const BROTHER_NUMBER_FRAMEWORK_ITEM_COUNT = 6;
const BROTHER_NUMBER_PAIRS = Object.freeze(["01", "12", "23", "34", "45", "56", "67", "78", "89", "09"]);
const POSITIONS = Object.freeze([
  { key: "wan", label: "千位", index: 0 },
  { key: "qian", label: "百位", index: 1 },
  { key: "bai", label: "十位", index: 2 },
  { key: "shi", label: "个位", index: 3 }
]);
const HEWEI_KEYS = Object.freeze([
  { key: "wanQianBai", label: "千百十合尾" },
  { key: "qianBaiShi", label: "百十个合尾" },
  { key: "wanQianGe", label: "千百个合尾" },
  { key: "wanBaiShi", label: "千十个合尾" },
  { key: "wanQianBaiShi", label: "千百十个合尾" }
]);

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

function extraCode(extra) {
  if (!extra || typeof extra !== "object") return cleanCode(extra);
  if (extra.value !== undefined && extra.value !== null && String(extra.value).trim() !== "") {
    return cleanCode(extra.value);
  }
  return cleanCode(extra.code);
}

function firstFourCode(record) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers.map(String) : [];
  if (nums.length < 4) return "";
  return nums.slice(0, 4).join("");
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

function brotherNumberActual(record) {
  return brotherNumberResultFromCode(firstFourCode(record));
}

function heweiDigitFromRecord(record, key) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers.map(Number) : [];
  if (nums.length < 4 || nums.some((n) => !Number.isFinite(n))) return null;
  const a = nums[0];
  const b = nums[1];
  const c = nums[2];
  const d = nums[3];
  if (key === "wanQianBai") return String((a + b + c) % 10);
  if (key === "qianBaiShi") return String((b + c + d) % 10);
  if (key === "wanQianGe") return String((a + b + d) % 10);
  if (key === "wanBaiShi") return String((a + c + d) % 10);
  if (key === "wanQianBaiShi") return String((a + b + c + d) % 10);
  return null;
}

function parityPatternOf(record) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers.map(Number) : [];
  if (nums.length < 4) return "";
  return nums.slice(0, 4).map((n) => (Number(n) % 2 ? "单" : "双")).join("");
}

function positionAvoidOk(record, key, code) {
  const pos = POSITIONS.find((entry) => entry.key === key);
  const nums = record && Array.isArray(record.numbers) ? record.numbers.map(String) : [];
  if (!pos || nums[pos.index] === undefined || !/^\d$/.test(String(code))) return null;
  return String(nums[pos.index]) !== String(code);
}

function heweiAvoidOk(record, key, code) {
  const actual = heweiDigitFromRecord(record, key);
  if (actual === null || !/^\d$/.test(String(code))) return null;
  return actual !== String(code);
}

function shapeAvoidOk(record, code) {
  if (!/^[单双]{4}$/.test(String(code || ""))) return null;
  const actual = parityPatternOf(record);
  if (!actual) return null;
  return actual !== String(code);
}

function anyTwoHitOk(record, code) {
  if (!isAnyTwoHeweiCode(code)) return null;
  const nums = record && Array.isArray(record.numbers) ? record.numbers.map(Number).slice(0, 4) : [];
  if (nums.length < 4) return null;
  const set = new Set(String(code).split(""));
  const tails = [];
  for (let i = 0; i < 4; i += 1) {
    for (let j = i + 1; j < 4; j += 1) {
      tails.push(String((nums[i] + nums[j]) % 10));
    }
  }
  return tails.some((t) => set.has(t));
}

function anyThreeHitOk(record, code) {
  const text = String(code || "").replace(/\D/g, "");
  if (text.length !== 5 || new Set(text.split("")).size !== 5) return null;
  const nums = record && Array.isArray(record.numbers) ? record.numbers.map(Number).slice(0, 4) : [];
  if (nums.length < 4) return null;
  const set = new Set(text.split(""));
  const tails = [];
  for (let i = 0; i < 4; i += 1) {
    for (let j = i + 1; j < 4; j += 1) {
      for (let k = j + 1; k < 4; k += 1) {
        tails.push(String((nums[i] + nums[j] + nums[k]) % 10));
      }
    }
  }
  return tails.some((t) => set.has(t));
}

function rateOf(history, checker, limit) {
  const rows = (Array.isArray(history) ? history : []).slice(0, Math.max(1, Number(limit || 120)));
  let total = 0;
  let hit = 0;
  rows.forEach((record) => {
    const ok = checker(record);
    if (typeof ok !== "boolean") return;
    total += 1;
    if (ok) hit += 1;
  });
  return {
    total,
    hit,
    rate: total ? Number(((hit / total) * 100).toFixed(2)) : null
  };
}

function brotherNumberWindowRate(rows, limit) {
  const source = (rows || []).slice(0, Math.max(1, Number(limit || 30)));
  let total = 0;
  let yes = 0;
  source.forEach((row) => {
    const actual = brotherNumberActual(row);
    if (actual === null) return;
    total += 1;
    if (actual === "有") yes += 1;
  });
  return {
    total,
    yes,
    no: total - yes,
    yesRate: total ? (yes * 100) / total : null,
    noRate: total ? ((total - yes) * 100) / total : null
  };
}

function brotherNumberConditionalRate(rows, limit, referenceChoice) {
  const source = rows || [];
  const max = Math.min(Math.max(1, Number(limit || 30)), Math.max(0, source.length - 1));
  let total = 0;
  let yes = 0;
  for (let i = 0; i < max; i += 1) {
    const context = brotherNumberActual(source[i + 1]);
    if (context === null || context !== referenceChoice) continue;
    const actual = brotherNumberActual(source[i]);
    if (actual === null) continue;
    total += 1;
    if (actual === "有") yes += 1;
  }
  return {
    total,
    yes,
    no: total - yes,
    yesRate: total ? (yes * 100) / total : null,
    noRate: total ? ((total - yes) * 100) / total : null
  };
}

function brotherNumberRecommendFromRows(previousRows) {
  const source = (previousRows || []).filter((row) => !!firstFourCode(row));
  if (!source.length) return null;
  const referenceChoice = brotherNumberActual(source[0]);
  const windows = [30, 120, 300, 1000];
  const weights = [5, 3, 2, 1];
  let yesScore = 0;
  let baselineYesScore = 0;
  let weightTotal = 0;
  windows.forEach((limit, index) => {
    const base = brotherNumberWindowRate(source, limit);
    if (!base.total) return;
    const cond = referenceChoice
      ? brotherNumberConditionalRate(source, limit, referenceChoice)
      : { total: 0, yesRate: null };
    const weight = weights[index] || 1;
    const enoughCondition = cond.total >= 8 && cond.yesRate !== null;
    const yesRate = enoughCondition
      ? cond.yesRate * 0.7 + (base.yesRate || 0) * 0.3
      : (base.yesRate || 0);
    yesScore += yesRate * weight;
    baselineYesScore += (base.yesRate || 0) * weight;
    weightTotal += weight;
  });
  if (!weightTotal) return null;
  const finalYes = yesScore / weightTotal;
  const finalNo = 100 - finalYes;
  const baselineYes = baselineYesScore / weightTotal;
  const decisionBoundary = baselineYes - 1.5;
  const choice = finalYes >= decisionBoundary ? "有" : "否";
  return {
    choice,
    score: choice === "有" ? finalYes : finalNo,
    yesScore: finalYes,
    noScore: finalNo,
    referenceChoice,
    baselineYes,
    decisionBoundary
  };
}

function ensureExtraKills(prediction, history) {
  const existing = asObject(prediction && prediction.extraKills);
  const needKeys = ["anyTwoHeweiHit", "anyThreeHeweiHit"];
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

function positionDigitFromEntry(entry) {
  const row = asObject(entry);
  return cleanCode(
    row.recommendDigit != null
      ? row.recommendDigit
      : (row.selectedDigit != null
        ? row.selectedDigit
        : (row.main != null ? row.main : (row.killDigits && row.killDigits[0])))
  );
}

function pickBestPosition(prediction, history) {
  const pk = asObject(prediction && prediction.positionKills);
  // 取二兄弟门道固定球二/百位；缺码才回退其它位
  const order = ["qian", "wan", "bai", "shi"];
  for (let i = 0; i < order.length; i += 1) {
    const key = order[i];
    const pos = POSITIONS.find((entry) => entry.key === key);
    if (!pos) continue;
    const code = positionDigitFromEntry(pk[key]);
    if (!/^\d$/.test(code)) continue;
    const stats = rateOf(history, (record) => positionAvoidOk(record, key, code), 120);
    return {
      kind: "position",
      key,
      side: "recommend",
      name: pos.label + "推荐",
      code,
      mode: "avoid",
      rate120: stats.rate,
      fixedSlot: key === "qian"
    };
  }
  return null;
}

function heweiDigitFromEntry(entry) {
  const row = asObject(entry);
  return cleanCode(row.killDigit != null ? row.killDigit : (row.value != null ? row.value : row.code));
}

function pickBestHewei(prediction, history) {
  const ih = asObject(prediction && prediction.independentHeweiKills);
  // 取二兄弟门道固定百十个合尾；缺码才回退其它合尾
  const order = ["qianBaiShi", "wanQianBai", "wanQianGe", "wanBaiShi", "wanQianBaiShi"];
  for (let i = 0; i < order.length; i += 1) {
    const key = order[i];
    const meta = HEWEI_KEYS.find((entry) => entry.key === key);
    if (!meta) continue;
    const code = heweiDigitFromEntry(ih[key]);
    if (!/^\d$/.test(code)) continue;
    const stats = rateOf(history, (record) => heweiAvoidOk(record, key, code), 120);
    return {
      kind: "hewei",
      key,
      side: "recommend",
      name: meta.label + "推荐",
      code,
      mode: "avoid",
      rate120: stats.rate,
      fixedSlot: key === "qianBaiShi"
    };
  }
  return null;
}

function pickParityShape(history) {
  const pool = [];
  const bits = ["单", "双"];
  for (let a = 0; a < 2; a += 1) {
    for (let b = 0; b < 2; b += 1) {
      for (let c = 0; c < 2; c += 1) {
        for (let d = 0; d < 2; d += 1) {
          pool.push(bits[a] + bits[b] + bits[c] + bits[d]);
        }
      }
    }
  }
  let best = null;
  pool.forEach((code) => {
    const stats = rateOf(history, (record) => shapeAvoidOk(record, code), 120);
    if (!best || Number(stats.rate || 0) > Number(best.rate120 || 0)) {
      best = {
        kind: "shape",
        key: "shapeParity",
        side: "recommend",
        name: "单双形态推荐",
        code,
        mode: "avoid",
        shapeKey: "shapeParity",
        rate120: stats.rate
      };
    }
  });
  return best;
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
    stableScore: Number(extra && extra.hardnessScore || extra && extra.rate120 || 0),
    stageLabel: extra && extra.stage || "",
    confidence: extra && extra.confidence || "",
    recommendable: extra && extra.recommendable !== false
  };
}

function digitRemainPool(killDigit) {
  const digit = cleanCode(killDigit).replace(/\D/g, "").charAt(0);
  return "1234567890".split("").filter((d) => d !== digit).join("");
}

/** 攻击语：千百十个保留池 + 四定二数合 + 三数合 + 百十个合保留池；减码只跟本框 items 走 */
function buildShot(items) {
  const list = Array.isArray(items) ? items : [];
  const pos = list.find((item) => item && item.kind === "position");
  const hewei = list.find((item) => item && item.kind === "hewei");
  const two = list.find((item) => item && item.key === "anyTwoHeweiHit");
  const three = list.find((item) => item && item.key === "anyThreeHeweiHit");
  const pools = { wan: "1234567890", qian: "1234567890", bai: "1234567890", shi: "1234567890" };
  if (pos && pools[pos.key] != null && /^\d$/.test(cleanCode(pos.code))) {
    pools[pos.key] = digitRemainPool(pos.code);
  }
  let heweiRemain = "1234567890";
  if (hewei && /^\d$/.test(cleanCode(hewei.code))) {
    heweiRemain = digitRemainPool(hewei.code);
  }
  return (
    "千" + pools.wan
    + "百" + pools.qian
    + "十" + pools.bai
    + "个" + pools.shi
    + "四定二数合" + cleanCode(two && two.code)
    + "三数合" + cleanCode(three && three.code)
    + "百十个合" + heweiRemain
    + "取二兄弟"
  );
}

function buildBrotherNumberFrameworkSnapshot(prediction, history) {
  const safeHistory = Array.isArray(history)
    ? history.filter((record) => record && Array.isArray(record.numbers))
    : [];
  const extras = ensureExtraKills(prediction, safeHistory);
  const repairReport = [];
  const items = [];

  const position = pickBestPosition(prediction, safeHistory);
  if (!position) repairReport.push("取二兄弟框架缺定位1码");
  else items.push(Object.assign({ rank: items.length + 1 }, position));

  const hewei = pickBestHewei(prediction, safeHistory);
  if (!hewei) repairReport.push("取二兄弟框架缺合尾1码");
  else items.push(Object.assign({ rank: items.length + 1 }, hewei));

  const two = extras.anyTwoHeweiHit;
  const twoCode = extraCode(two);
  if (!isAnyTwoHeweiCode(twoCode)) repairReport.push("取二兄弟框架缺二数合02468四码：" + (twoCode || "--"));
  else items.push(decorateExtraItem(two, "anyTwoHeweiHit", "二数合尾02468排1码命中", "hit", items.length + 1));

  const three = extras.anyThreeHeweiHit;
  const threeCode = extraCode(three);
  if (!/^\d{5}$/.test(threeCode) || new Set(threeCode.split("")).size !== 5) {
    repairReport.push("取二兄弟框架缺三数合五码：" + (threeCode || "--"));
  } else {
    items.push(decorateExtraItem(three, "anyThreeHeweiHit", "三数合尾宽命中", "hit", items.length + 1));
  }

  const brotherChoice = "有";
  items.push({
    rank: items.length + 1,
    kind: "brotherNumber",
    key: "brotherNumber",
    side: "recommend",
    name: "固定取二兄弟",
    code: brotherChoice,
    mode: "hit",
    rate30: rateOf(safeHistory, (row) => {
      const actual = brotherNumberActual(row);
      return actual === null ? null : actual === brotherChoice;
    }, 30).rate,
    rate120: rateOf(safeHistory, (row) => {
      const actual = brotherNumberActual(row);
      return actual === null ? null : actual === brotherChoice;
    }, 120).rate,
    score: 100,
    referenceChoice: "",
    defaulted: false,
    fixed: true
  });

  const shape = pickParityShape(safeHistory);
  if (!shape || !/^[单双]{4}$/.test(String(shape.code || ""))) repairReport.push("取二兄弟框架缺单双形态");
  else items.push(Object.assign({ rank: items.length + 1 }, shape));

  items.forEach((item, index) => {
    item.rank = index + 1;
  });

  return {
    version: BROTHER_NUMBER_FRAMEWORK_SNAPSHOT_VERSION,
    kind: "brotherNumberFramework",
    label: "取二兄弟框架",
    itemCount: items.length,
    framework: {
      positionCount: 1,
      heweiCount: 1,
      anyTwoHeweiCount: 1,
      anyThreeHeweiCount: 1,
      brotherCount: 1,
      shapeParityCount: 1,
      total: BROTHER_NUMBER_FRAMEWORK_ITEM_COUNT
    },
    shot: buildShot(items),
    items,
    repairReport,
    predictionGeneratedAt: new Date().toISOString()
  };
}

function brotherNumberFrameworkSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const posCount = formal.filter((item) => item.kind === "position" && /^\d$/.test(String(item.code || ""))).length;
  const heweiCount = formal.filter((item) => item.kind === "hewei" && /^\d$/.test(String(item.code || ""))).length;
  const twoCount = formal.filter((item) => item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const threeCount = formal.filter((item) => item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  const brotherCount = formal.filter((item) => item.kind === "brotherNumber" && (item.code === "有" || item.code === "否")).length;
  const shapeCount = formal.filter((item) => item.kind === "shape" && item.key === "shapeParity" && /^[单双]{4}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot
    && snapshot.version === BROTHER_NUMBER_FRAMEWORK_SNAPSHOT_VERSION
    && formal.length === BROTHER_NUMBER_FRAMEWORK_ITEM_COUNT
    && items.length === BROTHER_NUMBER_FRAMEWORK_ITEM_COUNT
    && posCount === 1
    && heweiCount === 1
    && twoCount === 1
    && threeCount === 1
    && brotherCount === 1
    && shapeCount === 1
    && typeof snapshot.shot === "string"
    && snapshot.shot.length > 8
  );
}

function ensurePredictionBrotherNumberFrameworkSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (brotherNumberFrameworkSnapshotIsComplete(prediction.brotherNumberFramework)) {
    return prediction.brotherNumberFramework;
  }
  prediction.brotherNumberFramework = buildBrotherNumberFrameworkSnapshot(prediction, history);
  prediction.brotherNumberFrameworkItems = prediction.brotherNumberFramework.items;
  prediction.brotherNumberFrameworkRepairReport = prediction.brotherNumberFramework.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") {
    prediction.recommendationMeta = {};
  }
  prediction.recommendationMeta.brotherNumberFrameworkSnapshotVersion = prediction.brotherNumberFramework.version;
  return prediction.brotherNumberFramework;
}

module.exports = {
  BROTHER_NUMBER_FRAMEWORK_SNAPSHOT_VERSION,
  BROTHER_NUMBER_FRAMEWORK_ITEM_COUNT,
  bindRuntime,
  buildShot,
  brotherNumberResultFromCode,
  brotherNumberRecommendFromRows,
  buildBrotherNumberFrameworkSnapshot,
  brotherNumberFrameworkSnapshotIsComplete,
  ensurePredictionBrotherNumberFrameworkSnapshot
};
