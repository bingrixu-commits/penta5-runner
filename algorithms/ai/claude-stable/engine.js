"use strict";
/**
 * claude-stable — 更稳定位杀+合尾投票（从 claude-ensemble 分叉，不改原模型）。
 *
 * 思路：不发明新指标。取当期六个 L1 框架（autoTop6 / recommend2 / killFramework /
 * compactAttack / threeDingTwo / threeDingOne）+ threeDuishu 的提案条目，按两层
 * 独立证据加权投票，选出被数据验证最可靠的一组：
 *   1) 条目层：该杀码在开奖历史上的真实避开率（本引擎直接对 history 重算，
 *      不信任来源模块自报的 rate/lower 字段）；
 *   2) 模块层：来源模块在已评估预测中的真实战绩（evaluation.<field> 的
 *      passed/total 摘要，历史快照体被压缩后仍保留）。
 * 两层都用 Wilson 下界防小样本虚高，多模块共识再给少量加分。
 * 本模块只读兄弟快照与历史，自身失败不影响主流程。
 */

const CLAUDE_ENSEMBLE_SNAPSHOT_VERSION = "backend-claude-stable-snapshot-v1-pos-hewei-only";
const CLAUDE_ENSEMBLE_ITEM_COUNT = Number(process.env.CLAUDE_ENSEMBLE_ITEM_COUNT || 6);
const CLAUDE_ENSEMBLE_NEAR_WINDOW = Number(process.env.CLAUDE_ENSEMBLE_NEAR_WINDOW || 120);
const CLAUDE_ENSEMBLE_LONG_WINDOW = Number(process.env.CLAUDE_ENSEMBLE_LONG_WINDOW || 400);
const CLAUDE_ENSEMBLE_MODULE_WINDOW = Number(process.env.CLAUDE_ENSEMBLE_MODULE_WINDOW || 300);
const CLAUDE_ENSEMBLE_MIN_ITEM_SAMPLE = Number(process.env.CLAUDE_ENSEMBLE_MIN_ITEM_SAMPLE || 100);
const CLAUDE_ENSEMBLE_MIN_ITEM_LOWER = Number(process.env.CLAUDE_ENSEMBLE_MIN_ITEM_LOWER || 86);
const CLAUDE_ENSEMBLE_MIN_POSITION_LOWER = Number(process.env.CLAUDE_ENSEMBLE_MIN_POSITION_LOWER || 88);
const CLAUDE_ENSEMBLE_MIN_DUISHU_SAMPLE = Number(process.env.CLAUDE_ENSEMBLE_MIN_DUISHU_SAMPLE || 30);
const CLAUDE_ENSEMBLE_VOTE_BONUS = Number(process.env.CLAUDE_ENSEMBLE_VOTE_BONUS || 0.8);
const CLAUDE_ENSEMBLE_VOTE_BONUS_CAP = Number(process.env.CLAUDE_ENSEMBLE_VOTE_BONUS_CAP || 3);
const CLAUDE_ENSEMBLE_ITEM_WEIGHT = Number(process.env.CLAUDE_ENSEMBLE_ITEM_WEIGHT || 0.75);

const SOURCE_MODULES = ["autoTop6", "recommend2", "killFramework", "compactAttack", "threeDingTwo", "threeDingOne"];

const POSITION_INDEX = { wan: 0, qian: 1, bai: 2, shi: 3 };
const POSITION_LABELS = { wan: "千位", qian: "百位", bai: "十位", shi: "个位" };
const HEWEI_INDICES = {
  wanQianBai: [0, 1, 2],
  qianBaiShi: [1, 2, 3]
};
const HEWEI_LABELS = {
  wanQianBai: "千百十合尾",
  qianBaiShi: "百十个合尾"
};

/** 主站正式二数合：仅从 02468 挑 4 码（等价排除其中 1 个）。 */
const FORMAL_ANY_TWO_SOURCE = "02468";
const FORMAL_ANY_TWO_SIZE = 4;
const FORMAL_ANY_TWO_CANDIDATES = (function buildFormalAnyTwoCandidates() {
  const src = FORMAL_ANY_TWO_SOURCE.split("");
  const out = [];
  function walk(start, acc) {
    if (acc.length === FORMAL_ANY_TWO_SIZE) {
      out.push(acc.join(""));
      return;
    }
    for (let i = start; i < src.length; i += 1) {
      acc.push(src[i]);
      walk(i + 1, acc);
      acc.pop();
    }
  }
  walk(0, []);
  return out;
})();

function isFormalAnyTwoCode(value) {
  const code = String(value || "").replace(/\D/g, "");
  if (code.length !== FORMAL_ANY_TWO_SIZE) return false;
  if (new Set(code.split("")).size !== FORMAL_ANY_TWO_SIZE) return false;
  return code.split("").every((digit) => FORMAL_ANY_TWO_SOURCE.indexOf(digit) >= 0);
}

let _runtime = { getState: () => ({}) };
let state = {};

function bindRuntime(runtime) { _runtime = Object.assign({}, _runtime, runtime || {}); }

function getState() {
  const s = (typeof _runtime.getState === "function" ? _runtime.getState() : {}) || {};
  state = s;
  return s;
}

function isoNow() { return new Date().toISOString(); }

function wilsonLowerPercent(hit, total, z) {
  const n = Number(total || 0);
  if (!n) return 0;
  const zz = Number(z || 1.6449);
  const p = Number(hit || 0) / n;
  const denom = 1 + (zz * zz) / n;
  const centre = p + (zz * zz) / (2 * n);
  const margin = zz * Math.sqrt((p * (1 - p) + (zz * zz) / (4 * n)) / n);
  return Number((((centre - margin) / denom) * 100).toFixed(2));
}

function recordFirstFourDigits(record) {
  const numbers = record && Array.isArray(record.numbers) ? record.numbers : null;
  if (!numbers || numbers.length < 4) return null;
  const digits = numbers.slice(0, 4).map((value) => {
    const n = Number(String(value).trim());
    return Number.isInteger(n) && n >= 0 && n <= 9 ? n : null;
  });
  return digits.every((d) => d !== null) ? digits : null;
}

function sizeShape(digits) { return digits.map((d) => (d >= 5 ? "大" : "小")).join(""); }
function parityShape(digits) { return digits.map((d) => (d % 2 === 1 ? "单" : "双")).join(""); }

/**
 * 判定一个 avoid 条目对给定四位开奖是否避开成功。
 * 返回 true=避开成功 / false=被打中 / null=类型不支持（不参与）。
 */
function itemOutcome(item, digits) {
  if (!item || !Array.isArray(digits) || digits.length < 4) return null;
  const kind = String(item.kind || "");
  const key = String(item.key || "");
  const code = String(item.code === undefined || item.code === null ? "" : item.code).trim();
  if (!code) return null;
  if (String(item.mode || "avoid") !== "avoid" && kind !== "duishu" && kind !== "anyTwoHewei" && kind !== "trioAnyTwoTail" && kind !== "formalAnyTwo") return null;
  if (kind === "position") {
    const index = POSITION_INDEX[key];
    if (index === undefined || !/^\d$/.test(code)) return null;
    return digits[index] !== Number(code);
  }
  if (kind === "hewei") {
    const indices = HEWEI_INDICES[key];
    if (!indices || !/^\d$/.test(code)) return null;
    const tail = indices.reduce((sum, i) => sum + digits[i], 0) % 10;
    return tail !== Number(code);
  }
  if (kind === "derivedReverseShape") {
    // 形态码可为 3 位（前三位形态）或 4 位（四位形态），按码长取前缀比较
    const len = code.length;
    if (len < 3 || len > 4) return null;
    if (key === "reverseSize") {
      if (!/^[大小]+$/.test(code)) return null;
      return sizeShape(digits.slice(0, len)) !== code;
    }
    if (key === "reverseParity") {
      if (!/^[单双]+$/.test(code)) return null;
      return parityShape(digits.slice(0, len)) !== code;
    }
    return null;
  }
  if (kind === "duishu") {
    if (!/^\d{2}$/.test(code)) return null;
    const a = Number(code[0]);
    const b = Number(code[1]);
    return !(digits.includes(a) && digits.includes(b));
  }
  if (kind === "anyTwoHewei") {
    // 千百十三数中任两数之和的尾数，落在 5 码集合内即命中（hit 口径）
    if (!/^\d{5}$/.test(code)) return null;
    const tails = [
      (digits[0] + digits[1]) % 10,
      (digits[0] + digits[2]) % 10,
      (digits[1] + digits[2]) % 10
    ];
    return tails.some((t) => code.indexOf(String(t)) >= 0);
  }
  if (kind === "trioSumTail") {
    // 指定三个位置之和的尾数杀（avoid），位置随三定动态对齐
    const pos = Array.isArray(item.positions) ? item.positions : null;
    if (!pos || pos.length !== 3 || !/^\d$/.test(code)) return null;
    if (pos.some((p) => !(p >= 0 && p <= 3))) return null;
    const tail = (digits[pos[0]] + digits[pos[1]] + digits[pos[2]]) % 10;
    return tail !== Number(code);
  }
  if (kind === "trioAnyTwoTail") {
    // 指定三个位置任两数之和的尾数，落在 5 码集合内即命中（hit 口径）
    const pos = Array.isArray(item.positions) ? item.positions : null;
    if (!pos || pos.length !== 3 || !/^\d{5}$/.test(code)) return null;
    if (pos.some((p) => !(p >= 0 && p <= 3))) return null;
    const tails = [
      (digits[pos[0]] + digits[pos[1]]) % 10,
      (digits[pos[0]] + digits[pos[2]]) % 10,
      (digits[pos[1]] + digits[pos[2]]) % 10
    ];
    return tails.some((t) => code.indexOf(String(t)) >= 0);
  }
  if (kind === "formalAnyTwo") {
    // 主站正式二数合：02468 中排 1 码剩 4 码；指定三位任两数合尾落入即命中
    const pos = Array.isArray(item.positions) ? item.positions : null;
    if (!pos || pos.length !== 3 || !isFormalAnyTwoCode(code)) return null;
    if (pos.some((p) => !(p >= 0 && p <= 3))) return null;
    const tails = [
      (digits[pos[0]] + digits[pos[1]]) % 10,
      (digits[pos[0]] + digits[pos[2]]) % 10,
      (digits[pos[1]] + digits[pos[2]]) % 10
    ];
    return tails.some((t) => code.indexOf(String(t)) >= 0);
  }
  return null;
}

/** 条目层证据：对 history 直接重算该条目的近窗/长窗避开率。 */
function itemEmpiricalFromHistory(item, historyRows) {
  let nearTotal = 0;
  let nearHit = 0;
  let longTotal = 0;
  let longHit = 0;
  const longLimit = Math.max(CLAUDE_ENSEMBLE_NEAR_WINDOW, CLAUDE_ENSEMBLE_LONG_WINDOW);
  for (let i = 0; i < historyRows.length && longTotal < longLimit; i += 1) {
    const digits = recordFirstFourDigits(historyRows[i]);
    if (!digits) continue;
    const outcome = itemOutcome(item, digits);
    if (outcome === null) return null;
    longTotal += 1;
    if (outcome) longHit += 1;
    if (nearTotal < CLAUDE_ENSEMBLE_NEAR_WINDOW) {
      nearTotal += 1;
      if (outcome) nearHit += 1;
    }
  }
  if (longTotal < CLAUDE_ENSEMBLE_MIN_ITEM_SAMPLE) return null;
  const lowerNear = wilsonLowerPercent(nearHit, nearTotal);
  const lowerLong = wilsonLowerPercent(longHit, longTotal);
  return {
    nearTotal,
    nearHit,
    longTotal,
    longHit,
    rateNear: nearTotal ? Number(((nearHit / nearTotal) * 100).toFixed(2)) : null,
    rateLong: longTotal ? Number(((longHit / longTotal) * 100).toFixed(2)) : null,
    lowerNear,
    lowerLong,
    score: Number((lowerNear * 0.65 + lowerLong * 0.35).toFixed(4))
  };
}

function evaluatedPredictions() {
  const rows = Array.isArray(state && state.predictions) ? state.predictions : [];
  return rows.filter((p) => p && p.evaluated && p.evaluation && typeof p.evaluation === "object");
}

let _moduleStatsCache = { signature: "", moduleStats: null, selfStats: null };

/**
 * 模块层证据：来源模块在已评估预测中的条目通过率与整组成功率。
 * 历史快照体被压缩清空后 evaluation.<field> 摘要仍在，这里只依赖摘要。
 */
function computeModuleStats(preds) {
  const out = {};
  SOURCE_MODULES.concat(["threeDuishu"]).forEach((field) => {
    let n = 0;
    let itemTotal = 0;
    let itemPassed = 0;
    let fullTotal = 0;
    let fullSuccess = 0;
    for (const pred of preds) {
      if (n >= CLAUDE_ENSEMBLE_MODULE_WINDOW) break;
      const summary = pred.evaluation[field];
      if (!summary || typeof summary !== "object" || summary.ok !== true) continue;
      if (summary.truthEligible !== true) continue;
      const total = Number(summary.total || 0);
      const passed = Number(summary.passed || 0);
      if (!total) continue;
      n += 1;
      itemTotal += total;
      itemPassed += Math.min(passed, total);
      fullTotal += 1;
      if (summary.success === true) fullSuccess += 1;
    }
    out[field] = {
      samples: n,
      itemTotal,
      itemPassed,
      itemRate: itemTotal ? Number(((itemPassed / itemTotal) * 100).toFixed(2)) : null,
      itemLower: wilsonLowerPercent(itemPassed, itemTotal),
      fullTotal,
      fullSuccess,
      fullRate: fullTotal ? Number(((fullSuccess / fullTotal) * 100).toFixed(2)) : null
    };
  });
  return out;
}

/** 自身战绩：过去带 claudeEnsemble 快照且已开奖的预测，整组全部避开才算成功。 */
function computeSelfStats(preds) {
  let total = 0;
  let hit = 0;
  for (const pred of preds) {
    const snap = pred.claudeEnsemble;
    if (!snap || typeof snap !== "object" || !Array.isArray(snap.items) || !snap.items.length) continue;
    const digits = recordFirstFourDigits({ numbers: pred.evaluation.actualNumbers }) ||
      (Array.isArray(pred.evaluation.actualFirstFourDigits) ? recordFirstFourDigits({ numbers: pred.evaluation.actualFirstFourDigits }) : null);
    if (!digits) continue;
    let allPass = true;
    let judged = 0;
    for (const item of snap.items) {
      const outcome = itemOutcome(item, digits);
      if (outcome === null) continue;
      judged += 1;
      if (!outcome) { allPass = false; break; }
    }
    if (!judged) continue;
    total += 1;
    if (allPass) hit += 1;
  }
  return {
    total,
    hit,
    rate: total ? Number(((hit / total) * 100).toFixed(2)) : null,
    lower: wilsonLowerPercent(hit, total),
    sampleInsufficient: total < 30
  };
}

function getModuleStats() {
  const preds = evaluatedPredictions();
  const head = preds.length ? String(preds[0].targetPeriod || preds[0].id || "") : "";
  const signature = preds.length + ":" + head + ":" + CLAUDE_ENSEMBLE_MODULE_WINDOW;
  if (_moduleStatsCache.moduleStats && _moduleStatsCache.signature === signature) return _moduleStatsCache;
  _moduleStatsCache = {
    signature,
    moduleStats: computeModuleStats(preds),
    selfStats: computeSelfStats(preds)
  };
  return _moduleStatsCache;
}

function snapshotItems(prediction, field) {
  if (!prediction || typeof prediction !== "object") return [];
  const snap = prediction[field];
  const fromSnap = snap && typeof snap === "object" && Array.isArray(snap.items) ? snap.items : null;
  const fromFlat = Array.isArray(prediction[field + "Items"]) ? prediction[field + "Items"] : null;
  return (fromSnap || fromFlat || []).filter((item) => item && typeof item === "object");
}

function itemLabel(kind, key, code) {
  if (kind === "position") return (POSITION_LABELS[key] || key) + "杀" + code;
  if (kind === "hewei") return (HEWEI_LABELS[key] || key) + "杀" + code;
  if (kind === "derivedReverseShape") {
    return (key === "reverseSize" ? "形态避开" : "奇偶形态避开") + code;
  }
  if (kind === "duishu") return "除对数" + code;
  return key + ":" + code;
}

function collectCurrentProposals(prediction) {
  const groups = new Map();
  SOURCE_MODULES.forEach((field) => {
    snapshotItems(prediction, field).forEach((item) => {
      const kind = String(item.kind || "");
      const key = String(item.key || "");
      const code = String(item.code === undefined || item.code === null ? "" : item.code).trim();
      if (!code || String(item.mode || "avoid") !== "avoid") return;
      // stable 分叉：只保留定位杀 + 合尾
      if (kind !== "position" && kind !== "hewei") return;
      if (kind === "position" && POSITION_INDEX[key] === undefined) return;
      if (kind === "hewei" && !HEWEI_INDICES[key]) return;
      // 形态一律用三位（千百十）：四位形态过于宽泛、约束弱，统一截取前三位
      let itemCode = code;
      if (kind === "derivedReverseShape") {
        if (itemCode.length < 3) return;
        itemCode = itemCode.slice(0, 3);
      }
      const groupKey = kind + "|" + key + "|" + itemCode;
      let group = groups.get(groupKey);
      if (!group) {
        group = { kind, key, code: itemCode, sources: [] };
        groups.set(groupKey, group);
      }
      if (!group.sources.includes(field)) group.sources.push(field);
    });
  });
  const duishu = prediction && prediction.threeDuishu;
  if (duishu && typeof duishu === "object" && /^\d{2}$/.test(String(duishu.selectedPair || ""))) {
    groups.set("duishu|selectedPair|" + duishu.selectedPair, {
      kind: "duishu",
      key: "selectedPair",
      code: String(duishu.selectedPair),
      sources: ["threeDuishu"]
    });
  }
  return Array.from(groups.values());
}

function scoreProposals(proposals, historyRows, moduleStats) {
  const scored = [];
  const duishuSamples = moduleStats && moduleStats.threeDuishu ? Number(moduleStats.threeDuishu.samples || 0) : 0;
  proposals.forEach((group) => {
    const empirical = itemEmpiricalFromHistory(group, historyRows);
    if (!empirical) return;
    // 真正落地的入选门槛：按类型要求近窗 Wilson 下界达标（定位更严，降低被打中率）
    const minLower = group.kind === "position" ? CLAUDE_ENSEMBLE_MIN_POSITION_LOWER : CLAUDE_ENSEMBLE_MIN_ITEM_LOWER;
    if (Number(empirical.lowerNear || 0) < minLower) return;
    // 对数排除历史战绩样本不足则不纳入（模块层样本太少不可信）
    if (group.kind === "duishu" || group.kind === "derivedReverseShape") return;
    if (group.kind === "duishu" && duishuSamples < CLAUDE_ENSEMBLE_MIN_DUISHU_SAMPLE) return;
    let moduleLower = 0;
    let bestModule = "";
    group.sources.forEach((field) => {
      const stat = moduleStats[field];
      const lower = stat && stat.itemTotal ? Number(stat.itemLower || 0) : 0;
      if (lower > moduleLower) {
        moduleLower = lower;
        bestModule = field;
      }
    });
    const voteBonus = Math.min((group.sources.length - 1) * CLAUDE_ENSEMBLE_VOTE_BONUS, CLAUDE_ENSEMBLE_VOTE_BONUS_CAP);
    const blended = empirical.score * CLAUDE_ENSEMBLE_ITEM_WEIGHT + moduleLower * (1 - CLAUDE_ENSEMBLE_ITEM_WEIGHT);
    scored.push({
      kind: group.kind,
      key: group.key,
      code: group.code,
      sources: group.sources.slice(),
      votes: group.sources.length,
      empirical,
      moduleLower: Number(moduleLower.toFixed(2)),
      bestModule,
      score: Number((blended + voteBonus).toFixed(4))
    });
  });
  scored.sort((a, b) => b.score - a.score);
  return scored;
}

/** 多样性约束：每个 position/hewei 键最多 1 条，形态最多 2 条，对数最多 1 条。 */
function selectItems(scored) {
  const chosen = [];
  const usedKeys = new Set();
  for (const candidate of scored) {
    if (chosen.length >= CLAUDE_ENSEMBLE_ITEM_COUNT) break;
    if (candidate.kind !== "position" && candidate.kind !== "hewei") continue;
    const slot = candidate.kind + "|" + candidate.key;
    if (usedKeys.has(slot)) continue;
    usedKeys.add(slot);
    chosen.push(candidate);
  }
  return chosen;
}

function qualityLabel(items, selfStats) {
  if (!items.length) return "无数据";
  const lowers = items.map((item) => Number(item.empirical && item.empirical.lowerNear || 0));
  const avg = lowers.reduce((a, b) => a + b, 0) / lowers.length;
  const min = Math.min.apply(null, lowers);
  const selfOk = !selfStats || selfStats.sampleInsufficient || Number(selfStats.rate || 0) >= 55;
  if (avg >= 85 && min >= 80 && selfOk) return "稳固";
  if (avg >= 78 && min >= 70) return "可用";
  return "观察";
}

function buildClaudeEnsembleSnapshot(prediction) {
  getState();
  const historyRows = Array.isArray(state && state.history) ? state.history : [];
  if (!historyRows.length) return null;
  const cache = getModuleStats();
  const proposals = collectCurrentProposals(prediction);
  if (!proposals.length) return null;
  const scored = scoreProposals(proposals, historyRows, cache.moduleStats);
  const chosen = selectItems(scored);
  if (chosen.length < 4) return null;
  const items = chosen.map((candidate, index) => ({
    rank: index + 1,
    kind: candidate.kind,
    key: candidate.key,
    name: itemLabel(candidate.kind, candidate.key, candidate.code),
    code: candidate.code,
    mode: "avoid",
    side: "kill",
    votes: candidate.votes,
    sources: candidate.sources,
    score: candidate.score,
    moduleLower: candidate.moduleLower,
    bestModule: candidate.bestModule,
    rate120: candidate.empirical.rateNear,
    rateLong: candidate.empirical.rateLong,
    lower120: candidate.empirical.lowerNear,
    lowerLong: candidate.empirical.lowerLong,
    sample120: candidate.empirical.nearTotal,
    sampleLong: candidate.empirical.longTotal
  }));
  const moduleWeights = {};
  Object.keys(cache.moduleStats).forEach((field) => {
    const stat = cache.moduleStats[field];
    if (stat && stat.itemTotal) {
      moduleWeights[field] = { itemLower: stat.itemLower, itemRate: stat.itemRate, samples: stat.samples };
    }
  });
  return {
    version: CLAUDE_ENSEMBLE_SNAPSHOT_VERSION,
    kind: "claudeStable",
    key: "claudeStable",
    side: "kill",
    name: "Claude集成评分",
    generatedAt: isoNow(),
    basePeriod: String(prediction && prediction.basePeriod || ""),
    methodology: "wilson-weighted-consensus-v1",
    itemCount: items.length,
    items,
    moduleWeights,
    quality: qualityLabel(items, cache.selfStats),
    ensembleTotal: cache.selfStats.total,
    ensembleHit: cache.selfStats.hit,
    ensembleRate: cache.selfStats.rate,
    ensembleLower: cache.selfStats.lower,
    ensembleSampleInsufficient: cache.selfStats.sampleInsufficient,
    nearWindow: CLAUDE_ENSEMBLE_NEAR_WINDOW,
    moduleWindow: CLAUDE_ENSEMBLE_MODULE_WINDOW,
    sealed: false
  };
}

function claudeEnsembleSnapshotIsComplete(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return false;
  if (String(snapshot.version || "") !== CLAUDE_ENSEMBLE_SNAPSHOT_VERSION) return false;
  const items = Array.isArray(snapshot.items) ? snapshot.items : [];
  if (items.length < 4) return false;
  return items.every((item) => item && String(item.code || "").length > 0);
}

function ensurePredictionClaudeEnsembleSnapshot(prediction) {
  if (!prediction || typeof prediction !== "object") return null;
  // 完整即冻结，与其他框架快照的语义一致
  if (claudeEnsembleSnapshotIsComplete(prediction.claudeEnsemble)) {
    return prediction.claudeEnsemble;
  }
  const snapshot = buildClaudeEnsembleSnapshot(prediction);
  if (!snapshot) return prediction.claudeEnsemble || null;
  prediction.claudeEnsemble = snapshot;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.claudeEnsembleSnapshotVersion = CLAUDE_ENSEMBLE_SNAPSHOT_VERSION;
  return prediction.claudeEnsemble;
}

function clearClaudeEnsembleStatsCache() {
  _moduleStatsCache = { signature: "", moduleStats: null, selfStats: null };
}

const POOL_POS_NAMES = ["千", "百", "十", "个"];

/**
 * 在选定的三个位置上判定杀码。positions 为升序的三个位置索引（0千 1百 2十 3个），
 * d3 是这三个位置上的数字。只应用能完整作用于这三个位置的条目，其余返回 null。
 */
function itemOutcomeOnPositions(item, d3, positions) {
  if (!item || !Array.isArray(d3) || d3.length < 3 || !Array.isArray(positions)) return null;
  const kind = String(item.kind || "");
  const key = String(item.key || "");
  const code = String(item.code === undefined || item.code === null ? "" : item.code).trim();
  if (!code) return null;
  if (kind === "position") {
    const index = POSITION_INDEX[key];
    if (index === undefined || !/^\d$/.test(code)) return null;
    const slot = positions.indexOf(index);
    if (slot < 0) return null; // 该定位杀不在选中的三个位置上
    return d3[slot] !== Number(code);
  }
  if (kind === "hewei") {
    const indices = HEWEI_INDICES[key];
    if (!indices || !/^\d$/.test(code)) return null;
    // 合尾的全部组成位都被选中才可判定
    if (!indices.every((i) => positions.includes(i))) return null;
    const tail = indices.reduce((sum, i) => sum + d3[positions.indexOf(i)], 0) % 10;
    return tail !== Number(code);
  }
  if (kind === "derivedReverseShape") {
    // 形态码是千百十（位置0,1,2）的三位形态，仅当选中位恰为千百十时适用
    if (code.length !== 3) return null;
    if (positions[0] !== 0 || positions[1] !== 1 || positions[2] !== 2) return null;
    if (key === "reverseSize") return sizeShape(d3) !== code;
    if (key === "reverseParity") return parityShape(d3) !== code;
  }
  return null;
}

/**
 * 500组"四选三"定位选号：千百十个四个位置中，按当期可预测性
 * （长窗频率偏度 + 当期定位杀码质量）选出信号最强的三个位置，
 * 最弱的位置控出用 x 表示。选中三位的 1000 组全空间 → 当期杀码过滤 →
 * 长窗位置频率+和值分布打分 → 取前 poolSize 组。
 * 开奖号在选中三个位置全对且落入池内即算命中（随机基线 = poolSize/1000）。
 */
/** 四项任务条目在三定三位组合上的判定（用于池子删减）。 */
function taskItemOutcomeOnTrio(item, d3, trio) {
  if (!item || !Array.isArray(d3) || d3.length !== 3) return null;
  const kind = String(item.kind || "");
  const code = String(item.code === undefined || item.code === null ? "" : item.code).trim();
  if (!code) return null;
  if (kind === "position") {
    const idx = POSITION_INDEX[String(item.key || "")];
    const slot = trio.indexOf(idx);
    if (slot < 0 || !/^\d$/.test(code)) return null;
    return d3[slot] !== Number(code);
  }
  if (kind === "trioSumTail") {
    if (!/^\d$/.test(code)) return null;
    return (d3[0] + d3[1] + d3[2]) % 10 !== Number(code);
  }
  if (kind === "trioAnyTwoTail") {
    if (!/^\d{5}$/.test(code)) return null;
    const tails = [(d3[0] + d3[1]) % 10, (d3[0] + d3[2]) % 10, (d3[1] + d3[2]) % 10];
    return tails.some((t) => code.indexOf(String(t)) >= 0);
  }
  return null;
}

function buildCandidatePool(snapshot, historyRows, poolSize, opts) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  if (!items.length) return null;
  const options = opts && typeof opts === "object" ? opts : {};
  const taskItems = Array.isArray(options.taskItems) ? options.taskItems : null;
  const forcedPositions = Array.isArray(options.positions) && options.positions.length === 3
    ? options.positions.slice().sort((a, b) => a - b)
    : null;
  const size = Math.max(50, Math.min(900, Number(poolSize || process.env.CLAUDE_ENSEMBLE_POOL_SIZE || 500)));
  const rows = [];
  for (const rec of Array.isArray(historyRows) ? historyRows : []) {
    const d = recordFirstFourDigits(rec);
    if (d) rows.push(d);
    if (rows.length >= CLAUDE_ENSEMBLE_LONG_WINDOW) break;
  }
  if (rows.length < CLAUDE_ENSEMBLE_MIN_ITEM_SAMPLE) return null;

  // 四个位置的长窗频率
  const posCount4 = [[], [], [], []].map(() => new Array(10).fill(1));
  rows.forEach((d) => { for (let p = 0; p < 4; p += 1) posCount4[p][d[p]] += 1; });

  // 位置可预测性评分：卡方偏度（分布越不均匀越可预测）+ 当期定位杀码下界
  const n = rows.length;
  const expected = n / 10;
  const chi2 = posCount4.map((arr) => arr.reduce((acc, c) => acc + Math.pow(c - 1 - expected, 2) / expected, 0));
  const maxChi2 = Math.max.apply(null, chi2.map((v) => Math.max(v, 1e-9)));
  const killLower = [0, 0, 0, 0];
  items.forEach((item) => {
    if (String(item.kind || "") !== "position") return;
    const idx = POSITION_INDEX[String(item.key || "")];
    if (idx === undefined) return;
    killLower[idx] = Math.max(killLower[idx], Number(item.lower120 || 0));
  });
  const posScore = chi2.map((v, p) => v / maxChi2 + killLower[p] / 100);
  // 控出（x）评分最低的位置；若外部已指定三定位置则直接沿用（保证与四项任务同一组位置）
  let positions;
  let xIndex;
  if (forcedPositions) {
    positions = forcedPositions;
    xIndex = [0, 1, 2, 3].find((p) => !positions.includes(p));
  } else {
    xIndex = 0;
    for (let p = 1; p < 4; p += 1) { if (posScore[p] < posScore[xIndex]) xIndex = p; }
    positions = [0, 1, 2, 3].filter((p) => p !== xIndex);
  }

  // 选中三位的频率与和值分布
  const posLog = positions.map((p) => {
    const total = posCount4[p].reduce((a, b) => a + b, 0);
    return posCount4[p].map((c) => Math.log(c / total));
  });
  const sumCount = new Array(28).fill(1);
  rows.forEach((d) => { sumCount[positions.reduce((acc, p) => acc + d[p], 0)] += 1; });
  const sumTotal = sumCount.reduce((a, b) => a + b, 0);
  const sumLog = sumCount.map((c) => Math.log(c / sumTotal));

  // 回删上期：上一期开奖号在三定位置上的组合，若仍幸存则整组删除
  const lastDraw = rows[0];
  const lastDrawCode = lastDraw ? positions.map((p) => lastDraw[p]).join("") : null;

  const scored = [];
  let killedCount = 0;
  let taskRemoved = 0;
  let lastDrawRemoved = 0;
  for (let m = 0; m < 1000; m += 1) {
    const d3 = [Math.floor(m / 100) % 10, Math.floor(m / 10) % 10, m % 10];
    let killed = false;
    for (const item of items) {
      if (itemOutcomeOnPositions(item, d3, positions) === false) { killed = true; break; }
    }
    if (killed) { killedCount += 1; continue; }
    // 四项任务删减：不满足任一任务条目的组合剔除（含二数合尾5码必中要求）
    if (taskItems) {
      let cut = false;
      for (const t of taskItems) {
        if (taskItemOutcomeOnTrio(t, d3, positions) === false) { cut = true; break; }
      }
      if (cut) { taskRemoved += 1; continue; }
    }
    const code = d3.join("");
    if (lastDrawCode && code === lastDrawCode) { lastDrawRemoved += 1; continue; }
    const score = posLog[0][d3[0]] + posLog[1][d3[1]] + posLog[2][d3[2]] +
      0.5 * sumLog[d3[0] + d3[1] + d3[2]];
    scored.push({ code, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return {
    kind: "three-of-four",
    positions,
    xIndex,
    positionLabel: positions.map((p) => POOL_POS_NAMES[p]).join("") + "三定（" + POOL_POS_NAMES[xIndex] + "位任意X）",
    poolSize: Math.min(size, scored.length),
    survivors: scored.length,
    killedByItems: killedCount,
    taskRemoved,
    taskAligned: !!taskItems,
    lastDrawCode,
    lastDrawRemoved,
    codes: scored.slice(0, size).map((x) => x.code)
  };
}

/**
 * 四项过关任务（用户定制）：
 *   1-2) 四个位置中挑最可靠的两个，各杀 1 个数字（优先取当期集成快照里
 *        评分最高的定位条目，不足两个时用纯历史统计补齐）；
 *   3) 千百十三数合尾杀 1 个尾数；
 *   4) 二数合尾命中：千百十任两数之和的尾数，从 0-9 穷举全部 252 种 5 码
 *      组合，选历史命中率 Wilson 下界最优的一组，任一组合落入即过。
 * 四项全过 = 过关。随机基线约 0.9×0.9×0.9×0.875 ≈ 64%。
 */
function buildCompactTask(snapshot, historyRows, trioPositions) {
  // 与三定池选中的三个位置完全对齐：定位杀、合尾杀、二数合尾全部作用于同一组位置
  const trio = Array.isArray(trioPositions) && trioPositions.length === 3 &&
    trioPositions.every((p) => Number.isInteger(p) && p >= 0 && p <= 3)
    ? trioPositions.slice().sort((a, b) => a - b)
    : [0, 1, 2];
  const trioLabel = trio.map((p) => POOL_POS_NAMES[p]).join("");
  const rows = [];
  for (const rec of Array.isArray(historyRows) ? historyRows : []) {
    const d = recordFirstFourDigits(rec);
    if (d) rows.push(d);
    if (rows.length >= CLAUDE_ENSEMBLE_LONG_WINDOW) break;
  }
  if (rows.length < CLAUDE_ENSEMBLE_MIN_ITEM_SAMPLE) return null;
  const near = Math.min(CLAUDE_ENSEMBLE_NEAR_WINDOW, rows.length);

  function statsFor(fn) {
    let nearHit = 0;
    let longHit = 0;
    for (let i = 0; i < rows.length; i += 1) {
      const ok = fn(rows[i]);
      if (ok) {
        longHit += 1;
        if (i < near) nearHit += 1;
      } else if (i < near) {
        // counted below
      }
    }
    const lowerNear = wilsonLowerPercent(nearHit, near);
    const lowerLong = wilsonLowerPercent(longHit, rows.length);
    return {
      rate120: Number(((nearHit / near) * 100).toFixed(2)),
      lower120: lowerNear,
      rateLong: Number(((longHit / rows.length) * 100).toFixed(2)),
      lowerLong,
      score: Number((lowerNear * 0.65 + lowerLong * 0.35).toFixed(4))
    };
  }

  const POS_KEYS = ["wan", "qian", "bai", "shi"];
  const items = [];

  // 1-2) 两个定位杀：只从三定选中的三个位置里挑，优先用集成快照条目（门槛+共识加权）
  const snapPosition = (snapshot && Array.isArray(snapshot.items) ? snapshot.items : [])
    .filter((it) => {
      if (!it || it.kind !== "position" || !/^\d$/.test(String(it.code || ""))) return false;
      const idx = POSITION_INDEX[String(it.key || "")];
      return idx !== undefined && trio.includes(idx);
    })
    .sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  const usedPos = new Set();
  for (const it of snapPosition) {
    if (items.length >= 2) break;
    const idx = POSITION_INDEX[String(it.key || "")];
    if (usedPos.has(idx)) continue;
    usedPos.add(idx);
    const st = statsFor((row) => row[idx] !== Number(it.code));
    items.push(Object.assign({
      kind: "position", key: String(it.key), code: String(it.code), mode: "avoid",
      name: POOL_POS_NAMES[idx] + "位杀" + it.code, source: "ensemble", votes: it.votes || 1
    }, st));
  }
  // 不足两个时用纯历史统计在剩余的三定位置里补齐
  if (items.length < 2) {
    const fillers = [];
    for (const p of trio) {
      if (usedPos.has(p)) continue;
      let best = null;
      for (let d = 0; d <= 9; d += 1) {
        const st = statsFor((row) => row[p] !== d);
        if (!best || st.score > best.score) best = Object.assign({ digit: d }, st);
      }
      fillers.push(Object.assign({ pos: p }, best));
    }
    fillers.sort((a, b) => b.score - a.score);
    for (const f of fillers) {
      if (items.length >= 2) break;
      usedPos.add(f.pos);
      items.push({
        kind: "position", key: POS_KEYS[f.pos], code: String(f.digit), mode: "avoid",
        name: POOL_POS_NAMES[f.pos] + "位杀" + f.digit, source: "history", votes: 0,
        rate120: f.rate120, lower120: f.lower120, rateLong: f.rateLong, lowerLong: f.lowerLong, score: f.score
      });
    }
  }

  // 3) 三定三数合尾杀：与三定位置对齐（历史最优尾；若三定恰为千百十则优先集成快照条目）
  const trioSum = (row) => (row[trio[0]] + row[trio[1]] + row[trio[2]]) % 10;
  const snapHewei = trio[0] === 0 && trio[1] === 1 && trio[2] === 2
    ? (snapshot && Array.isArray(snapshot.items) ? snapshot.items : [])
      .find((it) => it && it.kind === "hewei" && it.key === "wanQianBai" && /^\d$/.test(String(it.code || "")))
    : null;
  if (snapHewei) {
    const st = statsFor((row) => trioSum(row) !== Number(snapHewei.code));
    items.push(Object.assign({
      kind: "trioSumTail", key: "trioSum", positions: trio.slice(), code: String(snapHewei.code), mode: "avoid",
      name: trioLabel + "合尾杀" + snapHewei.code, source: "ensemble", votes: snapHewei.votes || 1
    }, st));
  } else {
    let best = null;
    for (let t = 0; t <= 9; t += 1) {
      const st = statsFor((row) => trioSum(row) !== t);
      if (!best || st.score > best.score) best = Object.assign({ tail: t }, st);
    }
    items.push({
      kind: "trioSumTail", key: "trioSum", positions: trio.slice(), code: String(best.tail), mode: "avoid",
      name: trioLabel + "合尾杀" + best.tail, source: "history", votes: 0,
      rate120: best.rate120, lower120: best.lower120, rateLong: best.rateLong, lowerLong: best.lowerLong, score: best.score
    });
  }

  // 4) 二数合尾 5 码命中：穷举 252 种组合选历史最优。
  // 关键约束：三个两两之和的尾数在奇偶上必然至少含一个偶数，
  // 因此 [02468] 是数学必中的作弊解（等于不排除）。这里先对每个组合
  // 用全部 1000 种千百十枚举出"结构基线"（均匀分布下的必中率），
  // 结构基线超过上限的组合一律禁用，只在真正有排除力的组合里选优。
  const ANYTWO_MAX_BASE = Number(process.env.CLAUDE_ENSEMBLE_ANYTWO_MAX_BASE || 92);
  const tripleTails = [];
  for (let m = 0; m < 1000; m += 1) {
    const a = Math.floor(m / 100) % 10;
    const b = Math.floor(m / 10) % 10;
    const c = m % 10;
    tripleTails.push([(a + b) % 10, (a + c) % 10, (b + c) % 10]);
  }
  const pairTails = rows.map((row) => [
    (row[trio[0]] + row[trio[1]]) % 10,
    (row[trio[0]] + row[trio[2]]) % 10,
    (row[trio[1]] + row[trio[2]]) % 10
  ]);
  let bestSet = null;
  for (let mask = 0; mask < 1024; mask += 1) {
    let bits = 0;
    for (let b = 0; b < 10; b += 1) { if (mask & (1 << b)) bits += 1; }
    if (bits !== 5) continue;
    // 结构基线：均匀假设下该组合的必中率
    let structHit = 0;
    for (let i = 0; i < 1000; i += 1) {
      const t = tripleTails[i];
      if (((mask >> t[0]) & 1) || ((mask >> t[1]) & 1) || ((mask >> t[2]) & 1)) structHit += 1;
    }
    const structBase = structHit / 10;
    if (structBase > ANYTWO_MAX_BASE) continue; // 禁用必中/准必中组合
    let nearHit = 0;
    let longHit = 0;
    for (let i = 0; i < pairTails.length; i += 1) {
      const t = pairTails[i];
      const hit = ((mask >> t[0]) & 1) || ((mask >> t[1]) & 1) || ((mask >> t[2]) & 1);
      if (hit) {
        longHit += 1;
        if (i < near) nearHit += 1;
      }
    }
    const lowerNear = wilsonLowerPercent(nearHit, near);
    const lowerLong = wilsonLowerPercent(longHit, pairTails.length);
    const score = lowerNear * 0.65 + lowerLong * 0.35;
    if (!bestSet || score > bestSet.score) {
      bestSet = { mask, score, nearHit, longHit, structBase };
    }
  }
  const setDigits = [];
  for (let b = 0; b <= 9; b += 1) { if (bestSet.mask & (1 << b)) setDigits.push(b); }
  items.push({
    kind: "trioAnyTwoTail", key: "trioAnyTwo", positions: trio.slice(), code: setDigits.join(""), mode: "hit",
    name: trioLabel + "二数合尾中[" + setDigits.join("") + "]", source: "optimized", votes: 0,
    structBase: Number(bestSet.structBase.toFixed(1)),
    rate120: Number(((bestSet.nearHit / near) * 100).toFixed(2)),
    lower120: wilsonLowerPercent(bestSet.nearHit, near),
    rateLong: Number(((bestSet.longHit / pairTails.length) * 100).toFixed(2)),
    lowerLong: wilsonLowerPercent(bestSet.longHit, pairTails.length),
    score: Number(bestSet.score.toFixed(4))
  });

  // 展示顺序固定：定位杀按位数从大到小（千→百→十→个），再合尾、再二数合尾
  const kindOrder = { position: 0, trioSumTail: 1, trioAnyTwoTail: 2 };
  items.sort((a, b) => {
    const ka = kindOrder[a.kind] !== undefined ? kindOrder[a.kind] : 9;
    const kb = kindOrder[b.kind] !== undefined ? kindOrder[b.kind] : 9;
    if (ka !== kb) return ka - kb;
    if (a.kind === "position") return (POSITION_INDEX[a.key] || 0) - (POSITION_INDEX[b.key] || 0);
    return 0;
  });

  const jointNear = items.reduce((acc, it) => acc * (Number(it.rate120 || 0) / 100), 1);
  // 随机基线按各项结构基线动态计算：定位/合尾杀=90%，二数合尾=所选组合的结构基线
  const dynamicBaseline = 0.9 * 0.9 * 0.9 * (bestSet.structBase / 100) * 100;
  return {
    kind: "compact-task-v3-trio-aligned",
    trio: trio.slice(),
    trioLabel,
    itemCount: items.length,
    items,
    expectedRate: Number((jointNear * 100).toFixed(2)),
    randomBaseline: Number(dynamicBaseline.toFixed(1)),
    anyTwoMaxBase: ANYTWO_MAX_BASE,
    window: { near, long: rows.length }
  };
}

/**
 * 独立正式二数合（方案 B）：02468 排 1 码剩 4 码。
 * 与四项任务第④项（5 码优化）分开；作用位与当期三定 trio 对齐。
 * 5 个候选按近/长窗 Wilson 选优；前列分差很小时用期号轮换，避免长期钉死 Top1。
 */
function buildFormalAnyTwo(historyRows, trioPositions, rotateSeed) {
  const trio = Array.isArray(trioPositions) && trioPositions.length === 3 &&
    trioPositions.every((p) => Number.isInteger(p) && p >= 0 && p <= 3)
    ? trioPositions.slice().sort((a, b) => a - b)
    : [0, 1, 2];
  const trioLabel = trio.map((p) => POOL_POS_NAMES[p]).join("");
  const rows = [];
  for (const rec of Array.isArray(historyRows) ? historyRows : []) {
    const d = recordFirstFourDigits(rec);
    if (d) rows.push(d);
    if (rows.length >= CLAUDE_ENSEMBLE_LONG_WINDOW) break;
  }
  if (rows.length < CLAUDE_ENSEMBLE_MIN_ITEM_SAMPLE) return null;
  const near = Math.min(CLAUDE_ENSEMBLE_NEAR_WINDOW, rows.length);
  const pairTails = rows.map((row) => [
    (row[trio[0]] + row[trio[1]]) % 10,
    (row[trio[0]] + row[trio[2]]) % 10,
    (row[trio[1]] + row[trio[2]]) % 10
  ]);
  // 结构基线：1000 种三字均匀分布下的必中率（02468 四码约 99%）
  const tripleTails = [];
  for (let m = 0; m < 1000; m += 1) {
    const a = Math.floor(m / 100) % 10;
    const b = Math.floor(m / 10) % 10;
    const c = m % 10;
    tripleTails.push([(a + b) % 10, (a + c) % 10, (b + c) % 10]);
  }
  const scored = FORMAL_ANY_TWO_CANDIDATES.map((code) => {
    let nearHit = 0;
    let longHit = 0;
    for (let i = 0; i < pairTails.length; i += 1) {
      const t = pairTails[i];
      const hit = code.indexOf(String(t[0])) >= 0 || code.indexOf(String(t[1])) >= 0 || code.indexOf(String(t[2])) >= 0;
      if (hit) {
        longHit += 1;
        if (i < near) nearHit += 1;
      }
    }
    let structHit = 0;
    for (let i = 0; i < 1000; i += 1) {
      const t = tripleTails[i];
      if (code.indexOf(String(t[0])) >= 0 || code.indexOf(String(t[1])) >= 0 || code.indexOf(String(t[2])) >= 0) structHit += 1;
    }
    const lowerNear = wilsonLowerPercent(nearHit, near);
    const lowerLong = wilsonLowerPercent(longHit, pairTails.length);
    const score = lowerNear * 0.65 + lowerLong * 0.35;
    return {
      code,
      nearHit,
      longHit,
      structBase: structHit / 10,
      rate120: Number(((nearHit / near) * 100).toFixed(2)),
      lower120: lowerNear,
      rateLong: Number(((longHit / pairTails.length) * 100).toFixed(2)),
      lowerLong,
      score: Number(score.toFixed(4))
    };
  }).sort((a, b) => b.score - a.score || a.code.localeCompare(b.code));
  if (!scored.length) return null;
  // 前列轮换：与 Top1 分差 ≤0.15 的候选里，按 rotateSeed 轮询
  const topScore = scored[0].score;
  const pool = scored.filter((row) => topScore - row.score <= 0.15);
  let pick = pool[0];
  const seed = String(rotateSeed == null ? "" : rotateSeed);
  if (pool.length > 1 && seed) {
    let h = 0;
    for (let i = 0; i < seed.length; i += 1) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
    pick = pool[h % pool.length];
  }
  const excluded = FORMAL_ANY_TWO_SOURCE.split("").filter((d) => pick.code.indexOf(d) < 0).join("");
  return {
    kind: "formalAnyTwo",
    key: "formalAnyTwo02468",
    positions: trio.slice(),
    code: pick.code,
    mode: "hit",
    name: trioLabel + "二数合" + pick.code,
    label: "二数合·02468排1码",
    excludedDigit: excluded,
    source: "formal-02468-v1",
    votes: 0,
    structBase: Number(pick.structBase.toFixed(1)),
    rate120: pick.rate120,
    lower120: pick.lower120,
    rateLong: pick.rateLong,
    lowerLong: pick.lowerLong,
    score: pick.score,
    poolSize: pool.length,
    candidates: scored.map((row) => ({ code: row.code, score: row.score, rate120: row.rate120 })),
    trioLabel,
    window: { near, long: rows.length }
  };
}

/** 把三位原码按控出位插入格式化，如 xIndex=1、code=001 → "0X01"。 */
function formatPoolCode(code, xIndex, placeholder) {
  const ph = placeholder === undefined || placeholder === null ? "X" : String(placeholder);
  const chars = String(code).split("");
  const out = [];
  let ci = 0;
  for (let p = 0; p < 4; p += 1) {
    out.push(p === Number(xIndex) ? ph : (chars[ci++] || "?"));
  }
  return out.join("");
}

module.exports = {
  bindRuntime,
  buildClaudeEnsembleSnapshot,
  ensurePredictionClaudeEnsembleSnapshot,
  claudeEnsembleSnapshotIsComplete,
  clearClaudeEnsembleStatsCache,
  buildCandidatePool,
  buildCompactTask,
  buildFormalAnyTwo,
  isFormalAnyTwoCode,
  formatPoolCode,
  itemOutcomeOnPositions,
  itemOutcome,
  CLAUDE_ENSEMBLE_SNAPSHOT_VERSION,
  FORMAL_ANY_TWO_CANDIDATES
};
