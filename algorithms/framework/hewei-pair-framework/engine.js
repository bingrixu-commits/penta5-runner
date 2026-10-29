"use strict";

/**
 * 合尾框架：五种独立合尾里按 30/60/120 投票推荐 2 路，两项同过才算过。
 * 面板侧拼接逻辑独立在 panel.html，此引擎供后端 buildSnapshot / evaluate 用。
 */

const voteSelect = require("../../_shared/vote_select");

const HEWEI_PAIR_FRAMEWORK_SNAPSHOT_VERSION = "backend-hewei-pair-framework-v1-vote-top2-of-5";
const HEWEI_PAIR_POOL = Object.freeze([
  { key: "wanQianBai", label: "千百十合尾", shot: "千百十合" },
  { key: "wanQianGe", label: "千百个合尾", shot: "千百个合" },
  { key: "wanBaiShi", label: "千十个合尾", shot: "千十个合" },
  { key: "qianBaiShi", label: "百十个合尾", shot: "百十个合" },
  { key: "wanQianBaiShi", label: "千百十个合尾", shot: "千百十个合" }
]);
const LEGACY_PAIR_KEYS = Object.freeze(["wanQianBai", "qianBaiShi"]);

function killDigit(entry) {
  if (entry == null) return "";
  if (typeof entry === "object")
    return String(entry.killDigit || entry.primaryDigit || entry.value || "").replace(/\D/g, "").charAt(0);
  return String(entry).replace(/\D/g, "").charAt(0);
}

function remainPool(digit) {
  const d = String(digit || "").replace(/\D/g, "").charAt(0);
  if (!/^\d$/.test(d)) return "";
  return "1234567890".split(d).join("");
}

function attackShotFromItems(items) {
  const parts = [];
  for (let i = 0; i < HEWEI_PAIR_POOL.length; i += 1) {
    const spec = HEWEI_PAIR_POOL[i];
    const item = (Array.isArray(items) ? items : []).find((row) => row && row.key === spec.key);
    if (!item) continue;
    const pool = remainPool(item.killDigit);
    if (!pool) return "";
    parts.push(spec.shot + pool);
  }
  return parts.join("");
}

function windowRate(predictions, key, win) {
  const rows = Array.isArray(predictions) ? predictions : [];
  let total = 0, hit = 0, missStreak = 0;
  for (let i = 0; i < rows.length; i += 1) {
    const ev = (rows[i] && rows[i].evaluation) || {};
    const item = (ev.independentHewei || {})[key];
    if (!item || typeof item.success !== "boolean") continue;
    total += 1;
    if (item.success === true) { hit += 1; missStreak = 0; }
    else missStreak += 1;
    if (total >= win) break;
  }
  return { rate: total ? Number(((hit / total) * 100).toFixed(2)) : 0, sample: total, hit, missStreak };
}

function wilsonLower(hit, total) {
  const n = Number(total || 0);
  if (!n) return 0;
  const p = Number(hit || 0) / n, z = 1.96, d = 1 + (z * z) / n, c = p + (z * z) / (2 * n);
  const m = z * Math.sqrt((p * (1 - p) + (z * z) / (4 * n)) / n);
  return Number((Math.max(0, Math.min(1, (c - m) / d)) * 100).toFixed(2));
}

function candidateRow(spec, hewei, predictions) {
  const digit = killDigit(hewei && hewei[spec.key]);
  const r10 = windowRate(predictions, spec.key, voteSelect.EARLY_ROTATE_WINDOW);
  const r30 = windowRate(predictions, spec.key, 30);
  const r60 = windowRate(predictions, spec.key, 60);
  const r120 = windowRate(predictions, spec.key, 120);
  return {
    key: spec.key, label: spec.label, shot: spec.shot,
    digit, killDigit: digit,
    rate10: r10.rate, sample10: r10.sample, fail10: Math.max(0, Number(r10.sample || 0) - Number(r10.hit || 0)),
    rate30: r30.rate, sample30: r30.sample,
    rate60: r60.rate, sample60: r60.sample,
    rate120: r120.rate, sample120: r120.sample,
    lower120: wilsonLower(r120.hit, r120.sample),
    score: r120.rate
  };
}

function poolOrderedSort(items) {
  const rank = {};
  HEWEI_PAIR_POOL.forEach((spec, idx) => { rank[spec.key] = idx; });
  return (Array.isArray(items) ? items : []).slice().sort((a, b) =>
    Number(rank[a && a.key] || 99) - Number(rank[b && b.key] || 99));
}

function lastHeweiSuccess(predictions, key) {
  const rows = Array.isArray(predictions) ? predictions : [];
  for (let i = 0; i < rows.length; i += 1) {
    const item = (((rows[i] && rows[i].evaluation) || {}).independentHewei || {})[key];
    if (item && typeof item.success === "boolean") return item.success === true;
  }
  return null;
}

function pickTopTwo(hewei, predictions) {
  const rows = HEWEI_PAIR_POOL
    .map((spec) => candidateRow(spec, hewei, predictions))
    .filter((row) => /^\d$/.test(row.digit));
  if (rows.length < 2)
    return poolOrderedSort(rows.length === 2 ? rows : LEGACY_PAIR_KEYS
      .map((k) => {
        const spec = HEWEI_PAIR_POOL.find((s) => s.key === k);
        if (!spec) return null;
        const digit = killDigit(hewei && hewei[k]);
        return /^\d$/.test(digit) ? { key: spec.key, label: spec.label, shot: spec.shot, killDigit: digit, votes: 0, rate30: 0, rate60: 0, rate120: 0, sample30: 0, sample60: 0, sample120: 0, lower120: 0 } : null;
      }).filter(Boolean));
  rows.forEach((row) => {
    row.justMissed = lastHeweiSuccess(predictions, row.key) === false;
  });
  const fresh = rows.filter((row) => row.justMissed !== true);
  const pool = fresh.length >= 2 ? fresh : rows;
  voteSelect.assignFirstPlaceVotes(pool);
  const first = voteSelect.pickVotedRow(pool);
  const second = voteSelect.pickVotedBackup(pool, first);
  if (!first || !second)
    return poolOrderedSort(pool.slice(0, 2));
  return poolOrderedSort([first, second]).map((row) => ({
    key: row.key, label: row.label, shot: row.shot,
    killDigit: row.killDigit || row.digit,
    votes: Number(row.votes || 0),
    rate30: row.rate30, rate60: row.rate60, rate120: row.rate120,
    sample30: row.sample30, sample60: row.sample60, sample120: row.sample120,
    lower120: row.lower120,
    justMissed: row.justMissed === true
  }));
}

function snapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  if (!snapshot || snapshot.version !== HEWEI_PAIR_FRAMEWORK_SNAPSHOT_VERSION) return false;
  if (items.length !== 2) return false;
  if (new Set(items.map((item) => item && item.key)).size !== 2) return false;
  for (let i = 0; i < items.length; i += 1) {
    const spec = HEWEI_PAIR_POOL.find((s) => s.key === (items[i] && items[i].key));
    if (!spec) return false;
    if (!/^\d$/.test(String(items[i].killDigit || ""))) return false;
  }
  return !!String(snapshot.shot || "").trim();
}

function buildSnapshot(options) {
  const opts = options && typeof options === "object" ? options : {};
  const hewei = opts.independentHeweiKills && typeof opts.independentHeweiKills === "object" ? opts.independentHeweiKills : {};
  const predictions = Array.isArray(opts.predictions) ? opts.predictions : [];
  const items = pickTopTwo(hewei, predictions);
  const shot = attackShotFromItems(items);
  return {
    version: HEWEI_PAIR_FRAMEWORK_SNAPSHOT_VERSION,
    kind: "heweiPairFramework",
    key: "heweiPairFramework",
    name: "合尾框架",
    keys: items.map((item) => item.key),
    items,
    shot,
    pickReason: HEWEI_PAIR_POOL.some((spec) => lastHeweiSuccess(predictions, spec.key) === false)
      && items.every((item) => lastHeweiSuccess(predictions, item && item.key) !== false)
      ? "vote-top2-miss-leave"
      : "vote-top2-of-5",
    poolKeys: HEWEI_PAIR_POOL.map((spec) => spec.key),
    sealed: false
  };
}

function evaluate(prediction, independentHewei) {
  const snapshot = prediction && prediction.heweiPairFramework;
  if (!snapshotIsComplete(snapshot))
    return { ok: false, success: null, passed: 0, total: 2, version: snapshot && snapshot.version || HEWEI_PAIR_FRAMEWORK_SNAPSHOT_VERSION, reason: "incomplete-hewei-pair-snapshot", truthEligible: false };
  const hw = independentHewei && typeof independentHewei === "object" ? independentHewei : {};
  let passed = 0;
  const failed = [], itemEvals = [];
  for (let i = 0; i < snapshot.items.length; i += 1) {
    const item = snapshot.items[i];
    const ev = hw[item.key] || {};
    const success = typeof ev.success === "boolean" ? ev.success === true : null;
    if (success === true) passed += 1;
    else if (success === false) failed.push(String(item.shot || "") + String(item.killDigit || ""));
    itemEvals.push({ key: item.key, label: item.label, killDigit: item.killDigit, success });
    if (typeof ev.success !== "boolean")
      return { ok: false, success: null, passed: 0, total: 2, keys: snapshot.keys, shot: snapshot.shot, items: itemEvals, version: snapshot.version, reason: "hewei-eval-missing", truthEligible: false };
  }
  return {
    ok: true, success: failed.length === 0, passed, total: 2,
    keys: snapshot.keys.slice(), shot: snapshot.shot, failed, items: itemEvals,
    version: snapshot.version, pickReason: snapshot.pickReason || "",
    truthEligible: true,
    sealedAt: snapshot.sealedAt || "", sealedBaseUid: snapshot.sealedBaseUid || "", sealHash: snapshot.sealHash || ""
  };
}

function slimSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return snapshot || null;
  const items = Array.isArray(snapshot.items)
    ? snapshot.items.map((item) => ({ key: item && item.key, label: item && item.label, shot: item && item.shot, killDigit: item && item.killDigit, votes: item && item.votes, rate30: item && item.rate30, rate60: item && item.rate60, rate120: item && item.rate120 }))
    : [];
  return {
    version: snapshot.version, kind: snapshot.kind, key: snapshot.key, name: snapshot.name,
    keys: Array.isArray(snapshot.keys) ? snapshot.keys.slice() : items.map((item) => item.key),
    items, shot: snapshot.shot || "", pickReason: snapshot.pickReason || "",
    sealed: snapshot.sealed === true, sealedAt: snapshot.sealedAt || "",
    sealedBaseUid: snapshot.sealedBaseUid || "", sealedBasePeriod: snapshot.sealedBasePeriod || "", sealHash: snapshot.sealHash || ""
  };
}

module.exports = {
  HEWEI_PAIR_FRAMEWORK_SNAPSHOT_VERSION, HEWEI_PAIR_POOL, LEGACY_PAIR_KEYS,
  snapshotIsComplete, buildSnapshot, evaluate, slimSnapshot,
  attackShotFromItems, killDigit
};
