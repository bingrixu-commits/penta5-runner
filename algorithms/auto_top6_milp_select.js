"use strict";

/**
 * autoTop6 正式 3定位+2合尾 的精确选型（MILP 同构穷举）。
 * - 默认：线性权最大 → 近优集合再按 comboScore 重排
 * - 硬约束不可行时自动放宽同码错开
 * - 无 cuOpt 依赖；事件循环友好（候选通常几十个）
 */

function itemId(item) {
  return [item && item.kind, item && item.key, item && item.side || "recommend", String(item && item.code)].join(":");
}

function modelKey(item) {
  return `${item && item.kind}:${item && item.key}`;
}

function heweiDigitToken(item) {
  if (!item || item.kind !== "hewei") return "";
  if ((item.side || "recommend") === "backup") return "";
  const digits = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  return digits.length === 1 ? digits : "";
}

function excludeDigitToken(item) {
  if (!item) return "";
  if ((item.side || "recommend") === "backup" && item.kind === "position") return "";
  if (item.kind !== "position" && item.kind !== "hewei") return "";
  const digits = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  return digits.length === 1 ? digits : "";
}

function linearWeight(item) {
  return (
    10000 * Number(item && item.stableScore || 0) +
    100 * Number(item && item.rate300 || 0) +
    Number(item && item.rate60 || 0)
  );
}

function combos(arr, k) {
  const out = [];
  const n = arr.length;
  if (k <= 0 || k > n) return out;
  const idx = Array.from({ length: k }, (_, i) => i);
  while (true) {
    out.push(idx.map((i) => arr[i]));
    let t = k - 1;
    while (t >= 0 && idx[t] === n - k + t) t -= 1;
    if (t < 0) break;
    idx[t] += 1;
    for (let j = t + 1; j < k; j += 1) idx[j] = idx[j - 1] + 1;
  }
  return out;
}

function feasible(set, allowSameExcludeDigit, maxPerModel) {
  const modelCount = Object.create(null);
  const heweiDigits = Object.create(null);
  const excludeDigits = Object.create(null);
  for (let i = 0; i < set.length; i += 1) {
    const item = set[i];
    const mk = modelKey(item);
    modelCount[mk] = Number(modelCount[mk] || 0) + 1;
    if (modelCount[mk] > Number(maxPerModel || 2)) return false;
    const hd = heweiDigitToken(item);
    if (hd) {
      if (heweiDigits[hd]) return false;
      heweiDigits[hd] = true;
    }
    if (!allowSameExcludeDigit) {
      const ed = excludeDigitToken(item);
      if (ed) {
        if (excludeDigits[ed]) return false;
        excludeDigits[ed] = true;
      }
    }
  }
  return true;
}

function defaultOrder(a, b) {
  if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
  if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
  if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
  return String(a.name || "").localeCompare(String(b.name || ""));
}

/**
 * @param {object} opts
 * @param {array} opts.positionPool formal position candidates
 * @param {array} opts.heweiPool formal hewei candidates
 * @param {function} [opts.comboScore] (items)=>number higher better
 * @param {number} [opts.positionCount=3]
 * @param {number} [opts.heweiCount=2]
 * @param {number} [opts.maxPerModel=2]
 * @param {number} [opts.rerankTop=40]
 * @returns {{ok:boolean, items:array, meta:object}}
 */
function selectFormalPositionHewei(opts) {
  const o = opts || {};
  const positionNeed = Math.max(0, Number(o.positionCount || 3) || 3);
  const heweiNeed = Math.max(0, Number(o.heweiCount || 2) || 2);
  const maxPerModel = Math.max(1, Number(o.maxPerModel || 2) || 2);
  const rerankTop = Math.max(1, Math.min(200, Number(o.rerankTop || 40) || 40));
  const comboScore = typeof o.comboScore === "function" ? o.comboScore : null;
  const positionPool = (Array.isArray(o.positionPool) ? o.positionPool : []).filter(Boolean);
  const heweiPool = (Array.isArray(o.heweiPool) ? o.heweiPool : []).filter(Boolean);

  if (positionPool.length < positionNeed || heweiPool.length < heweiNeed) {
    return {
      ok: false,
      items: [],
      meta: { path: "milp-exact", reason: "pool-short", positionPool: positionPool.length, heweiPool: heweiPool.length }
    };
  }

  const posCombos = combos(positionPool, positionNeed);
  const heweiCombos = combos(heweiPool, heweiNeed);
  const attempts = [
    { allowSameExcludeDigit: false, label: "hard-digit" },
    { allowSameExcludeDigit: true, label: "soft-digit" }
  ];

  for (let ai = 0; ai < attempts.length; ai += 1) {
    const attempt = attempts[ai];
    let bestLinear = -Infinity;
    const ranked = [];

    for (let pi = 0; pi < posCombos.length; pi += 1) {
      for (let hi = 0; hi < heweiCombos.length; hi += 1) {
        const set = posCombos[pi].concat(heweiCombos[hi]);
        if (!feasible(set, attempt.allowSameExcludeDigit, maxPerModel)) continue;
        const w = set.reduce((sum, item) => sum + linearWeight(item), 0);
        if (w > bestLinear) bestLinear = w;
        ranked.push({ set, w });
      }
    }

    if (!ranked.length) continue;

    ranked.sort((a, b) => b.w - a.w || defaultOrder(a.set[0], b.set[0]));
    const shortlist = ranked.slice(0, Math.min(rerankTop, ranked.length));
    let best = shortlist[0];
    let bestCombo = -Infinity;
    if (comboScore) {
      for (let i = 0; i < shortlist.length; i += 1) {
        const score = Number(comboScore(shortlist[i].set) || 0);
        if (score > bestCombo || (score === bestCombo && shortlist[i].w > best.w)) {
          bestCombo = score;
          best = shortlist[i];
        }
      }
    }

    const ids = new Set();
    const items = [];
    best.set.forEach((item) => {
      const id = itemId(item);
      if (ids.has(id)) return;
      ids.add(id);
      items.push(item);
    });

    return {
      ok: items.length === positionNeed + heweiNeed,
      items,
      meta: {
        path: "milp-exact",
        solver: "enumerate",
        digitMode: attempt.label,
        linearWeight: best.w,
        comboScore: comboScore ? bestCombo : null,
        enumerated: ranked.length,
        reranked: shortlist.length
      }
    };
  }

  return {
    ok: false,
    items: [],
    meta: { path: "milp-exact", reason: "infeasible", positionPool: positionPool.length, heweiPool: heweiPool.length }
  };
}

function itemsFromIds(positionPool, heweiPool, ids) {
  const map = new Map();
  (Array.isArray(positionPool) ? positionPool : []).concat(Array.isArray(heweiPool) ? heweiPool : []).forEach((item) => {
    if (!item) return;
    map.set(itemId(item), item);
  });
  const items = [];
  const seen = new Set();
  (Array.isArray(ids) ? ids : []).forEach((id) => {
    const key = String(id);
    if (seen.has(key)) return;
    const item = map.get(key);
    if (!item) return;
    seen.add(key);
    items.push(item);
  });
  return items;
}

module.exports = {
  selectFormalPositionHewei,
  linearWeight,
  itemId,
  itemsFromIds,
  heweiDigitToken,
  excludeDigitToken
};
