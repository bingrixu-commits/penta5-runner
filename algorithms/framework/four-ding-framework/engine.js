"use strict";
/**
 * 四定框架：二数合 + 三数合 + 除对数三组三位数 + 形态三组杀码。
 * 攻击语：二数合xxxx三数合xxxxx除对数xxx-xxx-xxx。除xxxx除xxxx除xxxx
 * 整框 AND：8 项全过才算中。
 */

const shapeLib = require("../../ai/_shape_lib");
const extraFormula = require("../../extra-kills/formula");


const FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION = "backend-four-ding-framework-snapshot-v2-hewei-duishu3-shape3-mixed-vote";

function isAllowedShapeKey(shapeKey) {
  return shapeKey === "shapeSize" || shapeKey === "shapeParity" || shapeKey === "shapeMixed";
}
const FOUR_DING_FRAMEWORK_ITEM_COUNT = 8;
const THREE_DIGIT_KEYS = Object.freeze(["threeDigitAvoid", "threeDigitHit", "threeDigitAvoid2"]);
const THREE_DIGIT_LABELS = Object.freeze({
  threeDigitAvoid: "除对数第1组（三数）",
  threeDigitHit: "除对数第2组（三数）",
  threeDigitAvoid2: "除对数第3组（三数）"
});

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

function ensureExtraKills(prediction, history) {
  const existing = asObject(prediction && prediction.extraKills);
  const needKeys = ["anyTwoHeweiHit", "anyThreeHeweiHit"].concat(THREE_DIGIT_KEYS);
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

function buildShot(items) {
  const list = Array.isArray(items) ? items : [];
  const two = list.find((item) => item && item.key === "anyTwoHeweiHit");
  const three = list.find((item) => item && item.key === "anyThreeHeweiHit");
  const duishu = THREE_DIGIT_KEYS.map((key) => list.find((item) => item && item.key === key)).filter(Boolean);
  const shapes = list.filter((item) => item && item.kind === "derivedReverseShape");
  const twoCode = cleanCode(two && two.code);
  const threeCode = cleanCode(three && three.code);
  const duishuPart = duishu.map((item) => cleanCode(item.code)).filter(Boolean).join("-");
  const shapePart = shapes.map((item) => "除" + cleanCode(item.code)).join("");
  return "二数合" + twoCode + "三数合" + threeCode + "除对数" + duishuPart + "。" + shapePart;
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

function buildFourDingFrameworkSnapshot(prediction, history) {
  const safeHistory = Array.isArray(history)
    ? history.filter((record) => record && Array.isArray(record.numbers))
    : [];
  const extras = ensureExtraKills(prediction, safeHistory);
  const repairReport = [];
  const items = [];

  const two = extras.anyTwoHeweiHit;
  const twoCode = extraCode(two);
  if (!isAnyTwoHeweiCode(twoCode)) {
    repairReport.push("四定框架缺二数合02468四码：" + (twoCode || "--"));
  } else {
    items.push(decorateExtraItem(two, "anyTwoHeweiHit", "二数合尾02468排1码命中", "hit", items.length + 1));
  }

  const three = extras.anyThreeHeweiHit;
  const threeCode = extraCode(three);
  if (!/^\d{5}$/.test(threeCode) || new Set(threeCode.split("")).size !== 5) {
    repairReport.push("四定框架缺三数合五码：" + (threeCode || "--"));
  } else {
    items.push(decorateExtraItem(three, "anyThreeHeweiHit", "三数合尾宽命中", "hit", items.length + 1));
  }

  THREE_DIGIT_KEYS.forEach((key) => {
    const extra = extras[key];
    const code = extraCode(extra);
    if (!isThreeDigitCode(code)) {
      repairReport.push("四定框架缺" + THREE_DIGIT_LABELS[key] + "：" + (code || "--"));
      return;
    }
    items.push(decorateExtraItem(extra, key, THREE_DIGIT_LABELS[key], "avoid", items.length + 1));
  });

  let shapeGroups = [];
  try {
    const picked = shapeLib.pickJointShapeAvoidTrio(safeHistory, 3, 4);
    shapeGroups = (picked && Array.isArray(picked.groups) ? picked.groups : []).slice(0, 3);
  } catch (err) {
    repairReport.push("形态三组选码失败：" + String(err && err.message || err));
  }

  if (shapeGroups.length < 3) {
    repairReport.push("四定框架形态三组不足：" + shapeGroups.length);
  }
  shapeGroups.slice(0, 3).forEach((group, index) => {
    const code = cleanCode(group && group.code);
    const shapeKey = String(group && group.shapeKey || "");
    if (!isShapeCode(code) || !isAllowedShapeKey(shapeKey)) {
      repairReport.push("形态第" + (index + 1) + "组无效：" + code + "/" + shapeKey);
      return;
    }
    items.push({
      rank: items.length + 1,
      kind: "derivedReverseShape",
      key: "shapeAvoid" + (index + 1),
      side: "recommend",
      name: "形态杀码第" + (index + 1) + "组",
      code,
      shapeKey,
      mode: "avoid",
      shapeScopeLength: 4,
      attack: "除" + code,
      rate30: Number(group.rate30 || 0),
      rate60: Number(group.rate60 || 0),
      rate120: Number(group.rate120 || 0),
      rate300: Number(group.rate300 || 0),
      lower120: Number(group.lower120 || 0),
      stableScore: Number(group.score || group.rate120 || 0),
      votes: Number(group.votes || 0),
      stageLabel: group.stage || "",
      missStreak30: Number(group.missStreak30 || 0),
      derivedFrom: "shape-avoid-3-joint-mixed-vote"
    });
  });

  items.forEach((item, index) => {
    item.rank = index + 1;
  });

  const shot = buildShot(items);
  return {
    version: FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION,
    kind: "fourDingFramework",
    label: "四定框架",
    itemCount: items.length,
    framework: {
      anyTwoHeweiCount: 1,
      anyThreeHeweiCount: 1,
      threeDigitAvoidCount: 3,
      shapeAvoidCount: 3,
      total: FOUR_DING_FRAMEWORK_ITEM_COUNT
    },
    shot,
    items,
    repairReport,
    predictionGeneratedAt: new Date().toISOString()
  };
}

function fourDingFrameworkSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const twoCount = formal.filter((item) => item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const threeCount = formal.filter((item) => item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  const duishuCount = formal.filter((item) => item.kind === "extra" && THREE_DIGIT_KEYS.includes(item.key) && isThreeDigitCode(item.code)).length;
  const shapeCount = formal.filter((item) => (
    item.kind === "derivedReverseShape"
    && isShapeCode(item.code)
    && isAllowedShapeKey(item.shapeKey)
  )).length;
  return !!(
    snapshot
    && snapshot.version === FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION
    && formal.length === FOUR_DING_FRAMEWORK_ITEM_COUNT
    && items.length === FOUR_DING_FRAMEWORK_ITEM_COUNT
    && twoCount === 1
    && threeCount === 1
    && duishuCount === 3
    && shapeCount === 3
    && typeof snapshot.shot === "string"
    && snapshot.shot.indexOf("二数合") === 0
    && snapshot.shot.indexOf("三数合") > 0
    && snapshot.shot.indexOf("除对数") > 0
  );
}

function ensurePredictionFourDingFrameworkSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (fourDingFrameworkSnapshotIsComplete(prediction.fourDingFramework)) {
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
  ensurePredictionFourDingFrameworkSnapshot
};
