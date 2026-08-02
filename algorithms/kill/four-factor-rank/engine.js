"use strict";

/**
 * 四因子排码：近90期加权频次 + 短期热度 + 一阶转移 + 弱遗漏修正
 * 每位取 score 最高的 1 码（杀码/排码口径）
 */
const fs = require("fs");
const path = require("path");
const lib = require("../../ai/_shape_lib");

const MODEL_ID = "four-factor-rank";
const VERSION = "four-factor-rank-v1";
const DIGITS = Object.freeze(["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]);
const POSITIONS = Object.freeze([
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 }
]);
const WEIGHTS = Object.freeze({
  wfreq: 0.4,
  heat: 0.25,
  markov: 0.25,
  omit: 0.1
});

function outPaths() {
  const dir = path.join(lib.REPO_ROOT, "data", "algorithm-projects", MODEL_ID);
  return { dir, file: path.join(dir, "state.json") };
}

function chronologic(history) {
  const rows = (Array.isArray(history) ? history : [])
    .filter((r) => r && Array.isArray(r.numbers) && r.numbers.length >= 4)
    .map((r) => ({
      period: String(r.period || ""),
      numbers: r.numbers.slice(0, 4).map(String)
    }));
  rows.sort((a, b) => {
    if (a.period < b.period) return -1;
    if (a.period > b.period) return 1;
    return 0;
  });
  return rows;
}

function minMaxNorm(obj) {
  const vals = DIGITS.map((d) => Number(obj[d] || 0));
  const mn = Math.min.apply(null, vals);
  const mx = Math.max.apply(null, vals);
  const out = {};
  DIGITS.forEach((d) => {
    out[d] = mx === mn ? 0.1 : (Number(obj[d] || 0) - mn) / (mx - mn);
  });
  return out;
}

function scorePosition(seq) {
  const last = seq[seq.length - 1];
  const win90 = seq.slice(-90);

  const wfreq = Object.create(null);
  DIGITS.forEach((d) => { wfreq[d] = 0; });
  let wsum = 0;
  win90.forEach((d, i) => {
    const w = Math.pow(0.97, win90.length - 1 - i);
    wfreq[d] += w;
    wsum += w;
  });
  DIGITS.forEach((d) => { wfreq[d] = wsum ? wfreq[d] / wsum : 0; });

  const heat = Object.create(null);
  DIGITS.forEach((d) => { heat[d] = 0; });
  seq.slice(-10).forEach((d) => { heat[d] += 1; });
  DIGITS.forEach((d) => { heat[d] /= 10; });

  const trans = Object.create(null);
  DIGITS.forEach((a) => {
    trans[a] = Object.create(null);
    DIGITS.forEach((b) => { trans[a][b] = 0; });
  });
  for (let i = 1; i < seq.length; i += 1) {
    trans[seq[i - 1]][seq[i]] += 1;
  }
  const row = trans[last] || Object.create(null);
  let rowSum = 0;
  DIGITS.forEach((d) => { rowSum += Number(row[d] || 0); });
  if (!rowSum) rowSum = 1;
  const markov = Object.create(null);
  DIGITS.forEach((d) => { markov[d] = Number(row[d] || 0) / rowSum; });

  const miss = Object.create(null);
  DIGITS.forEach((d) => { miss[d] = 999; });
  for (let i = seq.length - 1; i >= 0; i -= 1) {
    const d = seq[i];
    if (miss[d] === 999) miss[d] = seq.length - 1 - i;
  }
  const mean = DIGITS.reduce((s, d) => s + miss[d], 0) / 10;
  const omit = Object.create(null);
  DIGITS.forEach((d) => {
    const x = miss[d];
    if (x >= 0.6 * mean && x <= 1.8 * mean) {
      const z = (x - mean) / (0.7 * mean + 1e-6);
      omit[d] = Math.exp(-(z * z));
    } else {
      omit[d] = 0;
    }
  });

  const A = minMaxNorm(wfreq);
  const B = minMaxNorm(heat);
  const C = minMaxNorm(markov);
  const D = minMaxNorm(omit);
  const score = Object.create(null);
  DIGITS.forEach((d) => {
    score[d] =
      WEIGHTS.wfreq * A[d] +
      WEIGHTS.heat * B[d] +
      WEIGHTS.markov * C[d] +
      WEIGHTS.omit * D[d];
  });
  const ranked = DIGITS.slice().sort((a, b) => (score[b] - score[a]) || a.localeCompare(b));
  return {
    digit: ranked[0],
    score: Number(score[ranked[0]].toFixed(4)),
    miss: miss[ranked[0]],
    top3: ranked.slice(0, 3).map((d) => ({
      digit: d,
      score: Number(score[d].toFixed(4)),
      miss: miss[d]
    })),
    parts: {
      wfreq: Number(A[ranked[0]].toFixed(4)),
      heat: Number(B[ranked[0]].toFixed(4)),
      markov: Number(C[ranked[0]].toFixed(4)),
      omit: Number(D[ranked[0]].toFixed(4))
    }
  };
}

function compute(history) {
  const rows = chronologic(history);
  if (rows.length < 30) {
    return {
      ok: false,
      error: "history-too-short",
      historyCount: rows.length
    };
  }
  const train = rows; // 全窗训练；最新期参与转移/遗漏
  const latest = train[train.length - 1];
  const items = POSITIONS.map((pos) => {
    const seq = train.map((r) => String(r.numbers[pos.index]));
    const picked = scorePosition(seq);
    return {
      key: pos.key,
      label: pos.label,
      index: pos.index,
      digit: picked.digit,
      score: picked.score,
      miss: picked.miss,
      top3: picked.top3,
      parts: picked.parts
    };
  });
  const byKey = Object.create(null);
  items.forEach((it) => { byKey[it.key] = it.digit; });
  return {
    ok: true,
    version: VERSION,
    id: MODEL_ID,
    kind: "fourFactorRank",
    label: "四因子排码",
    note: "近90期加权频次+短期热度+一阶转移+弱遗漏修正 · 每位排1码",
    weights: WEIGHTS,
    generatedAt: new Date().toISOString(),
    historyCount: train.length,
    latestPeriod: latest.period,
    latestNumbers: latest.numbers.slice(),
    picks: byKey,
    items,
    line: items.map((it) => it.label + it.digit).join(" / ")
  };
}

async function refresh() {
  const history = await lib.fetchHistory(Number(process.env.FOUR_FACTOR_HISTORY_LIMIT || 200));
  if (!history.length) throw new Error("history empty");
  const state = compute(history);
  if (!state.ok) throw new Error(state.error || "compute failed");
  const { dir, file } = outPaths();
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(state, null, 2));
  return state;
}

module.exports = {
  MODEL_ID,
  VERSION,
  WEIGHTS,
  POSITIONS,
  compute,
  refresh,
  outPaths
};
