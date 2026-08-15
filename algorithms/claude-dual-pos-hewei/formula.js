"use strict";

/**
 * Claude 版搭配模型：二定避码 + 千百十个合尾避码，三件全过才算中。
 * 结构与 dual-pos-total-hewei 相同，但选码与评估遵循以下原则：
 *
 * 1) 选码 = 长窗贝叶斯收缩频率（Dirichlet 先验），选后验出现概率最低的码。
 *    不使用近窗回放选优——均匀随机游戏里近窗"选优"只是拟合噪声，
 *    且长窗收缩让选码天然稳定、换手极少。
 * 2) 定位对选择：四个位置各算一个避码候选，取后验概率最低的两个位置。
 *    纯确定性，同一历史必得同一 tip（无状态、可复现、walk-forward 与实盘同构）。
 * 3) 评估只认 walk-forward，且与实盘用同一个 pick 函数（杜绝"评估参数≠实盘参数"）。
 *    输出明示随机基线 72.9% 与超额（edgeOverBaseline），不提供选中组合的回放指标。
 *
 * 数学事实：若开奖均匀随机，任意两位确定后合尾仍均匀，三件联合通过率恒为
 * 0.9^3 = 72.9%。本模型的目标是稳定贴住该上限并如实汇报，不制造虚高指标。
 */

const { wilsonLowerRate } = require("../_shared/formula_kit");

const VERSION = "claude-dual-pos-hewei-v1-shrinkage-honest";
const LABEL = "Claude二定+合尾";
const KIND = "claudeDualPosHewei";

const LONG_WINDOW = Number(process.env.CLAUDE_DUAL_LONG_WINDOW || 600) || 600;
// Dirichlet 先验强度（每码），相当于给每个数字预填 ALPHA 次伪计数
const ALPHA = Number(process.env.CLAUDE_DUAL_ALPHA || 6) || 6;
const EVAL_CAP = Number(process.env.CLAUDE_DUAL_EVAL_CAP || 1000) || 1000;
const MIN_HISTORY = 240;
const JOINT_BASELINE = 72.9;

const POSITION_LIST = Object.freeze([
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 }
]);

function asHistory(history) {
  return Array.isArray(history)
    ? history.filter((row) => row && Array.isArray(row.numbers) && row.numbers.length >= 4)
    : [];
}

function digitAt(record, index) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  const v = nums[index];
  const s = v === undefined || v === null ? "" : String(v);
  return /^\d$/.test(s) ? s : "";
}

function totalHewei(record) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  if (nums.length < 4) return "";
  const sum = Number(nums[0]) + Number(nums[1]) + Number(nums[2]) + Number(nums[3]);
  return Number.isFinite(sum) ? String(sum % 10) : "";
}

/**
 * 对一个数字序列（最新在前）做长窗收缩统计。
 * 返回每个码的后验出现概率与观测计数，及后验最低的避码候选。
 */
function shrunkAvoidCandidate(seriesNewestFirst, window) {
  const n = Math.min(seriesNewestFirst.length, Math.max(60, Number(window || LONG_WINDOW)));
  const counts = new Array(10).fill(0);
  let used = 0;
  for (let i = 0; i < seriesNewestFirst.length && used < n; i += 1) {
    const d = seriesNewestFirst[i];
    if (!/^\d$/.test(d)) continue;
    counts[Number(d)] += 1;
    used += 1;
  }
  const denom = used + 10 * ALPHA;
  let bestDigit = 0;
  for (let d = 1; d < 10; d += 1) {
    if (counts[d] < counts[bestDigit]) bestDigit = d;
  }
  const posterior = (counts[bestDigit] + ALPHA) / denom;
  // 避开成功率的经验值与 Wilson 下界（避开 = 该码未出现）
  const avoidHit = used - counts[bestDigit];
  return {
    digit: String(bestDigit),
    posterior: Number(posterior.toFixed(5)),
    appearCount: counts[bestDigit],
    sample: used,
    avoidRate: used ? Number(((avoidHit * 100) / used).toFixed(2)) : 0,
    avoidLower: Number(wilsonLowerRate(avoidHit, used, 1.44).toFixed(2))
  };
}

function seriesForPosition(historyNewestFirst, index) {
  const out = [];
  for (let i = 0; i < historyNewestFirst.length; i += 1) {
    out.push(digitAt(historyNewestFirst[i], index));
  }
  return out;
}

function seriesForHewei(historyNewestFirst) {
  const out = [];
  for (let i = 0; i < historyNewestFirst.length; i += 1) {
    out.push(totalHewei(historyNewestFirst[i]));
  }
  return out;
}

/**
 * 核心选码：四位各取收缩后验最低的避码，选后验最低的两个位置 + 合尾避码。
 * 纯确定性；walk-forward 与实盘调用同一个函数。
 */
function pickTip(historyNewestFirst, window) {
  const win = Number(window || LONG_WINDOW);
  const candidates = POSITION_LIST.map((p) => {
    const cand = shrunkAvoidCandidate(seriesForPosition(historyNewestFirst, p.index), win);
    return Object.assign({ index: p.index, key: p.key, label: p.label }, cand);
  });
  const ranked = candidates.slice().sort((a, b) => {
    if (a.posterior !== b.posterior) return a.posterior - b.posterior;
    return a.index - b.index;
  });
  const positions = ranked.slice(0, 2).sort((a, b) => a.index - b.index);
  const hewei = shrunkAvoidCandidate(seriesForHewei(historyNewestFirst), win);
  return { positions, hewei, candidates };
}

function evaluateFixedTip(actualRecord, tip) {
  if (!tip || !actualRecord) return null;
  for (let i = 0; i < tip.positions.length; i += 1) {
    const p = tip.positions[i];
    const actual = digitAt(actualRecord, p.index);
    if (!actual) return null;
    if (actual === String(p.digit)) return false;
  }
  const hw = totalHewei(actualRecord);
  if (!hw) return null;
  return hw !== String(tip.hewei.digit);
}

function windowStats(outcomes, n) {
  const slice = n >= outcomes.length ? outcomes : outcomes.slice(-n);
  let hit = 0;
  let run = 0;
  let maxMiss = 0;
  for (let i = 0; i < slice.length; i += 1) {
    if (slice[i]) {
      hit += 1;
      run = 0;
    } else {
      run += 1;
      if (run > maxMiss) maxMiss = run;
    }
  }
  let missStreak = 0;
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (!slice[i]) missStreak += 1;
    else break;
  }
  const total = slice.length;
  const rate = total ? Number(((hit * 100) / total).toFixed(2)) : 0;
  return {
    window: n >= outcomes.length ? total : n,
    hit,
    total,
    rate,
    lower: Number(wilsonLowerRate(hit, total, 1.96).toFixed(2)),
    missStreak,
    maxMissStreak: maxMiss,
    baseline: JOINT_BASELINE,
    edgeOverBaseline: total ? Number((rate - JOINT_BASELINE).toFixed(2)) : null
  };
}

/**
 * walk-forward：逐期用"当期之前"的历史调用与实盘相同的 pickTip。
 * 评估窗默认 1000 期，报告随机基线与超额。
 */
function walkForwardJointStats(historyNewestFirst) {
  const chrono = asHistory(historyNewestFirst).slice().reverse();
  if (chrono.length <= MIN_HISTORY + 10) {
    return { evalPeriods: 0, windows: [], method: "same-policy-walk-forward", baseline: JOINT_BASELINE };
  }
  const start = Math.max(MIN_HISTORY, chrono.length - EVAL_CAP);
  const outcomes = [];
  for (let i = start; i < chrono.length; i += 1) {
    const before = chrono.slice(Math.max(0, i - LONG_WINDOW), i).reverse();
    const picked = pickTip(before, LONG_WINDOW);
    const ok = evaluateFixedTip(chrono[i], picked);
    if (ok === null) continue;
    outcomes.push(ok);
  }
  const caps = [30, 120, outcomes.length];
  const seen = new Set();
  const windows = [];
  caps.forEach((n) => {
    const key = n >= outcomes.length ? "all" : String(n);
    if (seen.has(key)) return;
    seen.add(key);
    windows.push(windowStats(outcomes, n));
  });
  return {
    evalPeriods: outcomes.length,
    windows,
    method: "same-policy-walk-forward",
    baseline: JOINT_BASELINE,
    note: "评估与实盘同一 pickTip；基线为均匀随机下的理论值 0.9^3"
  };
}

function buildSnapshot(history) {
  const safeHistory = asHistory(history);
  const latest = safeHistory[0] || null;
  const picked = pickTip(safeHistory, LONG_WINDOW);
  const items = picked.positions.map((p) => ({
    kind: "position",
    key: p.key,
    label: p.label,
    index: p.index,
    code: p.digit,
    posterior: p.posterior,
    rateLong: p.avoidRate,
    lowerLong: p.avoidLower,
    sampleLong: p.sample
  }));
  const heweiItem = {
    kind: "hewei",
    key: "wanQianBaiShi",
    label: "千百十个合尾",
    code: picked.hewei.digit,
    posterior: picked.hewei.posterior,
    rateLong: picked.hewei.avoidRate,
    lowerLong: picked.hewei.avoidLower,
    sampleLong: picked.hewei.sample
  };
  const joint = walkForwardJointStats(safeHistory);
  const tipText = items.map((it) => (it.label || "") + (it.code || "")).join("、")
    + " + 合" + (heweiItem.code || "--");
  return {
    version: VERSION,
    kind: KIND,
    label: LABEL,
    algorithm: "claude-dual-pos-hewei",
    pickMethod: "long-window-shrunk-posterior",
    basePeriod: latest && latest.period != null ? String(latest.period) : "",
    generatedAt: new Date().toISOString(),
    items,
    hewei: heweiItem,
    tipText,
    itemCount: items.length,
    complete: items.length === 2
      && items.every((it) => /^\d$/.test(String(it.code || "")))
      && /^\d$/.test(String(heweiItem.code || ""))
      && safeHistory.length >= MIN_HISTORY,
    joint,
    note: "长窗收缩后验选码；三件全过才算中；均匀随机下理论上限 72.9%，联合指标只认 walk-forward",
    source: {
      officialHistory: "rds-shared-read-only",
      historyCount: safeHistory.length,
      longWindow: LONG_WINDOW,
      alpha: ALPHA,
      evalCap: EVAL_CAP,
      minHistory: MIN_HISTORY
    }
  };
}

function snapshotIsComplete(snapshot) {
  const snap = snapshot && typeof snapshot === "object" ? snapshot : null;
  if (!snap || snap.version !== VERSION) return false;
  if (!Array.isArray(snap.items) || snap.items.length !== 2) return false;
  if (!snap.items.every((it) => it && /^\d$/.test(String(it.code || "")) && it.key != null)) return false;
  const hw = snap.hewei && snap.hewei.code != null ? String(snap.hewei.code) : "";
  if (!/^\d$/.test(hw)) return false;
  const histCount = Number(snap.source && snap.source.historyCount || 0);
  if (histCount > 0 && histCount < MIN_HISTORY) return false;
  return snap.complete === true;
}

function evaluateSnapshot(snapshot, actualRecord) {
  const snap = snapshot && typeof snapshot === "object" ? snapshot : null;
  const numbers = actualRecord && Array.isArray(actualRecord.numbers)
    ? actualRecord.numbers.map(String)
    : [];
  if (!snap || !snapshotIsComplete(snap) || numbers.length < 4) {
    return {
      version: VERSION,
      kind: KIND,
      label: LABEL,
      success: null,
      truthEligible: false,
      reason: "incomplete"
    };
  }
  const posResults = (snap.items || []).map((it) => {
    const idx = Number(it.index);
    const actual = numbers[idx];
    const code = String(it.code);
    const ok = /^\d$/.test(actual) && actual !== code;
    return { key: it.key, label: it.label, code, actual: actual || "", success: ok };
  });
  const hwCode = String(snap.hewei.code);
  const hwActual = String((Number(numbers[0]) + Number(numbers[1]) + Number(numbers[2]) + Number(numbers[3])) % 10);
  const heweiOk = hwActual !== hwCode;
  const posOk = posResults.every((r) => r.success === true);
  return {
    version: VERSION,
    kind: KIND,
    label: LABEL,
    success: posOk && heweiOk,
    truthEligible: true,
    positionOk: posOk,
    heweiOk,
    positions: posResults,
    hewei: { code: hwCode, actual: hwActual, success: heweiOk },
    tipText: snap.tipText || ""
  };
}

module.exports = {
  VERSION,
  LABEL,
  KIND,
  POSITION_LIST,
  LONG_WINDOW,
  ALPHA,
  EVAL_CAP,
  MIN_HISTORY,
  JOINT_BASELINE,
  pickTip,
  buildSnapshot,
  snapshotIsComplete,
  evaluateSnapshot,
  walkForwardJointStats
};
