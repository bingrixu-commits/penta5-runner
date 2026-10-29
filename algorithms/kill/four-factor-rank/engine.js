"use strict";

/**
 * 四因子排码：近90期加权频次 + 短期热度 + 一阶转移 + 弱遗漏修正
 * 主排4+追加3=排除7；选杀取高分（原四因子口径，不用低分钝杀）。
 *
 * 组数对齐（满池10000）：
 *   剩号(在池) ≈ 4500  ↔  剩码命中(开奖在池)
 *   排除       ≈ 5500  ↔  联合命中/正路盖全(四位开奖均落在该位排除集=不在剩池)
 * 二者互补；联合=正路（不是反路指标）：正路命中率越低越好；反路=排除后剩号池。
 */
const fs = require("fs");
const path = require("path");
const lib = require("../../ai/_shape_lib");

const MODEL_ID = "four-factor-rank";
const VERSION = "four-factor-rank-v17-highscore";
const FULL_COUNT = 10000;
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
const MIN_TRAIN = 30;
const EVAL_WINDOWS = Object.freeze([30, 120]);
/** 正车：每位主排1码(4) + 全局再补3码 = 共排除7个位×码；反路=排除后剩号 */
const DROP_PRIMARY_N = 4;
const DROP_EXTRA_N = 3;
const DROP_TOTAL_N = DROP_PRIMARY_N + DROP_EXTRA_N;
const DAY_RESET_HOUR = Math.max(0, Math.min(23, Number(process.env.PENTA5_DAY_RESET_HOUR || 5) || 5));

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

function emptyTrans() {
  const trans = Object.create(null);
  DIGITS.forEach((a) => {
    trans[a] = Object.create(null);
    DIGITS.forEach((b) => { trans[a][b] = 0; });
  });
  return trans;
}

function buildTrans(seq) {
  const trans = emptyTrans();
  for (let i = 1; i < seq.length; i += 1) {
    trans[seq[i - 1]][seq[i]] += 1;
  }
  return trans;
}

function appendDigit(seq, trans, digit) {
  const d = String(digit);
  if (seq.length) trans[seq[seq.length - 1]][d] += 1;
  seq.push(d);
}

/** transOpt 可传入已维护的转移表，避免 walk-forward 每步全量重扫 */
function scorePosition(seq, transOpt) {
  if (!seq || !seq.length) {
    const z = DIGITS.map((d) => ({ digit: d, score: 0, miss: 999 }));
    return {
      digit: "0",
      score: 0,
      miss: 999,
      top3: z.slice(0, 3),
      rankedAll: z,
      parts: { wfreq: 0, heat: 0, markov: 0, omit: 0 }
    };
  }
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

  const trans = transOpt || buildTrans(seq);
  const row = trans[last] || Object.create(null);
  let rowSum = 0;
  DIGITS.forEach((d) => { rowSum += Number(row[d] || 0); });
  if (!rowSum) rowSum = 1;
  const markov = Object.create(null);
  DIGITS.forEach((d) => { markov[d] = Number(row[d] || 0) / rowSum; });

  const miss = Object.create(null);
  DIGITS.forEach((d) => { miss[d] = 999; });
  let left = 10;
  for (let i = seq.length - 1; i >= 0; i -= 1) {
    const d = seq[i];
    if (miss[d] === 999) {
      miss[d] = seq.length - 1 - i;
      left -= 1;
      if (!left) break;
    }
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
  const rankedAll = ranked.map((d) => ({
    digit: d,
    score: Number(score[d].toFixed(4)),
    miss: miss[d]
  }));
  return {
    digit: ranked[0],
    score: Number(score[ranked[0]].toFixed(4)),
    miss: miss[ranked[0]],
    top3: rankedAll.slice(0, 3),
    rankedAll,
    parts: {
      wfreq: Number(A[ranked[0]].toFixed(4)),
      heat: Number(B[ranked[0]].toFixed(4)),
      markov: Number(C[ranked[0]].toFixed(4)),
      omit: Number(D[ranked[0]].toFixed(4))
    }
  };
}

function killId(key, digit) {
  return String(key) + ":" + String(digit);
}

function codeSurvivesDrops(code, kills) {
  const s = String(code || "");
  if (!/^\d{4}$/.test(s)) return false;
  for (let i = 0; i < (kills || []).length; i += 1) {
    const k = kills[i];
    if (!k) continue;
    const idx = Number(k.index);
    if (!(idx >= 0 && idx < 4)) continue;
    if (s.charAt(idx) === String(k.digit)) return false;
  }
  return true;
}

function buildRemainCodes(kills) {
  const out = [];
  for (let n = 0; n < 10000; n += 1) {
    const code = String(n).padStart(4, "0");
    if (codeSurvivesDrops(code, kills)) out.push(code);
  }
  return out;
}

/** 正路排除池：开奖落在此侧 = 联合盖全命中 */
function buildExcludeCodes(kills) {
  const out = [];
  for (let n = 0; n < 10000; n += 1) {
    const code = String(n).padStart(4, "0");
    if (!codeSurvivesDrops(code, kills)) out.push(code);
  }
  return out;
}

function remainCountForKills(kills) {
  // 按位统计丢码数，避免扫 10000（同分布下与枚举一致）
  const dropAt = [0, 0, 0, 0];
  const seen = Object.create(null);
  (kills || []).forEach((k) => {
    if (!k) return;
    const idx = Number(k.index);
    const d = String(k.digit || "");
    if (!(idx >= 0 && idx < 4) || !/^\d$/.test(d)) return;
    const id = idx + ":" + d;
    if (seen[id]) return;
    seen[id] = true;
    dropAt[idx] += 1;
  });
  let n = 1;
  for (let i = 0; i < 4; i += 1) n *= Math.max(0, 10 - dropAt[i]);
  return n;
}

/** 从已算好的 rankedAll 抽主排+追加：均取高分（原四因子口径） */
function pickDropKillsFromRanked(scoredByPos) {
  const primary = [];
  const pool = [];
  POSITIONS.forEach((pos) => {
    const scored = scoredByPos[pos.key] || {};
    const ranked = Array.isArray(scored.rankedAll) ? scored.rankedAll : (scored.top3 || []);
    if (!ranked.length) return;
    // rankedAll 已按分数高→低；主排取最高分
    const top = ranked[0];
    primary.push({
      role: "primary",
      key: pos.key,
      label: pos.label,
      index: pos.index,
      digit: String(top.digit),
      score: Number(top.score || 0),
      miss: top.miss,
      // 转换器口径：除千7（不是千除7）；后者会被短指令吞掉却不真正排除
      attack: "除" + pos.label + String(top.digit),
      id: killId(pos.key, top.digit)
    });
    for (let i = 1; i < ranked.length; i += 1) {
      const row = ranked[i];
      pool.push({
        role: "extra",
        key: pos.key,
        label: pos.label,
        index: pos.index,
        digit: String(row.digit),
        score: Number(row.score || 0),
        miss: row.miss,
        attack: "除" + pos.label + String(row.digit),
        id: killId(pos.key, row.digit)
      });
    }
  });
  // 追加继续取高分；主排4+追加3=排除7
  pool.sort((a, b) => (b.score - a.score) || a.id.localeCompare(b.id));
  const used = Object.create(null);
  primary.forEach((k) => { used[k.id] = true; });
  const extra = [];
  const extraPosCount = Object.create(null);
  function takeExtra(preferSpread) {
    for (let i = 0; i < pool.length && extra.length < DROP_EXTRA_N; i += 1) {
      const cand = pool[i];
      if (!cand || used[cand.id]) continue;
      const posKey = String(cand.key || "");
      if (preferSpread && Number(extraPosCount[posKey] || 0) > 0) continue;
      used[cand.id] = true;
      extraPosCount[posKey] = Number(extraPosCount[posKey] || 0) + 1;
      extra.push(cand);
    }
  }
  // 优先把追加分到不同位，避免叠杀同一位把剩池砍太狠
  takeExtra(true);
  takeExtra(false);
  const kills = primary.concat(extra).map((k, idx) => Object.assign({}, k, { rank: idx + 1 }));
  return { primary, extra, kills };
}

/** 每位主排1(高分) + 剩余候选里按高分再取 EXTRA 个 */
function pickDropKills(train) {
  const scoredByPos = Object.create(null);
  POSITIONS.forEach((pos) => {
    const seq = train.map((r) => String(r.numbers[pos.index]));
    scoredByPos[pos.key] = scorePosition(seq);
  });
  return pickDropKillsFromRanked(scoredByPos);
}

function pickDropKillsFromPosState(posState) {
  const scoredByPos = Object.create(null);
  POSITIONS.forEach((pos, idx) => {
    const st = posState[idx] || { seq: [], trans: emptyTrans() };
    scoredByPos[pos.key] = scorePosition(st.seq, st.trans);
  });
  return pickDropKillsFromRanked(scoredByPos);
}

function ratePct(hit, total) {
  if (!total) return null;
  return Number(((100 * hit) / total).toFixed(2));
}

/** hits: 时间从旧到新的 boolean 序列 */
function streakFromHits(hits) {
  const list = Array.isArray(hits) ? hits : [];
  let maxWin = 0;
  let maxMiss = 0;
  let runWin = 0;
  let runMiss = 0;
  for (let i = 0; i < list.length; i += 1) {
    if (list[i] === true) {
      runWin += 1;
      runMiss = 0;
      if (runWin > maxWin) maxWin = runWin;
    } else {
      runMiss += 1;
      runWin = 0;
      if (runMiss > maxMiss) maxMiss = runMiss;
    }
  }
  let currentWin = 0;
  let currentMiss = 0;
  for (let j = list.length - 1; j >= 0; j -= 1) {
    if (list[j] === true) {
      if (currentMiss > 0) break;
      currentWin += 1;
    } else {
      if (currentWin > 0) break;
      currentMiss += 1;
    }
  }
  return {
    currentWin,
    currentMiss,
    maxWin,
    maxMiss,
    total: list.length,
    hit: list.filter(Boolean).length
  };
}

/** 期号 YYYYMMDDnnn（5分钟序）→ 业务日 YYYY-MM-DD（默认 05:00 切日） */
function businessDayKeyFromPeriod(period) {
  const text = String(period || "").trim();
  const m = text.match(/^([0-9]{4})([0-9]{2})([0-9]{2})([0-9]{3})$/);
  if (!m) return "";
  const year = Number(m[1]), month = Number(m[2]), day = Number(m[3]), seq = Number(m[4]);
  if (seq < 1 || seq > 288) return "";
  // Encode the Beijing wall-clock date in UTC arithmetic. The official first
  // slot is 001=00:05; 060=05:00 and 288=the following midnight.
  const dateMs = Date.UTC(year, month - 1, day);
  const parsed = new Date(dateMs);
  if (!Number.isFinite(dateMs) || parsed.getUTCFullYear() !== year
      || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return "";
  const shifted = new Date(dateMs + (seq * 5 - DAY_RESET_HOUR * 60) * 60 * 1000);
  return String(shifted.getUTCFullYear()).padStart(4, "0") + "-"
    + String(shifted.getUTCMonth() + 1).padStart(2, "0") + "-"
    + String(shifted.getUTCDate()).padStart(2, "0");
}

/**
 * Walk-forward：用期 i 之前的历史排码，对照期 i 开奖。
 * 排除7是动作。
 * 剩码命中 = 开奖在剩号池（对齐剩号组数≈4500）
 * 联合命中(正路盖全) = 四位开奖均在该位排除集(=不在剩池；对齐排除≈5500)
 * 互补。按位序列 + 增量转移表，避免每步 O(n) 全量重扫。
 */
function walkForward(rows, evalN) {
  const maxEval = Math.max(30, Number(evalN) || 120);
  const start = Math.max(MIN_TRAIN, rows.length - maxEval);
  const outcomes = [];

  const posState = POSITIONS.map((pos) => {
    const seq = [];
    const trans = emptyTrans();
    for (let i = 0; i < start; i += 1) {
      appendDigit(seq, trans, rows[i].numbers[pos.index]);
    }
    return { seq, trans };
  });

  for (let i = start; i < rows.length; i += 1) {
    const target = rows[i];
    const scoredByPos = Object.create(null);
    POSITIONS.forEach((pos, idx) => {
      const st = posState[idx];
      scoredByPos[pos.key] = st.seq.length < MIN_TRAIN
        ? { rankedAll: [], digit: null }
        : scorePosition(st.seq, st.trans);
    });
    const dropPack = pickDropKillsFromRanked(scoredByPos);
    const primaryByKey = Object.create(null);
    (dropPack.primary || []).forEach((k) => {
      if (k && k.key) primaryByKey[k.key] = String(k.digit);
    });
    // 分位统计：主排钝杀码是否撞上开奖该位（与实际排除一致）
    const hits = POSITIONS.map((pos) => {
      const dig = primaryByKey[pos.key];
      if (dig == null) return false;
      return dig === String(target.numbers[pos.index]);
    });
    const targetCode = POSITIONS.map((pos) => String(target.numbers[pos.index])).join("");
    const inRemain = codeSurvivesDrops(targetCode, dropPack.kills) === true;
    outcomes.push({
      period: target.period,
      hits,
      remainHit: inRemain,
      // 联合=正路盖全：开奖不在剩池（!inRemain）；越低越好
      jointHit: !inRemain
    });
    POSITIONS.forEach((pos, idx) => {
      const st = posState[idx];
      appendDigit(st.seq, st.trans, target.numbers[pos.index]);
    });
  }

  function windowStats(n) {
    const slice = outcomes.slice(-n);
    const total = slice.length;
    const pos = POSITIONS.map((pos, idx) => {
      let hit = 0;
      slice.forEach((o) => { if (o.hits[idx]) hit += 1; });
      return {
        key: pos.key,
        label: pos.label,
        hit,
        total,
        rate: ratePct(hit, total)
      };
    });
    let jointHit = 0;
    let remainHit = 0;
    slice.forEach((o) => {
      if (o.jointHit) jointHit += 1;
      if (o.remainHit) remainHit += 1;
    });
    return {
      window: n,
      total,
      jointHit,
      jointRate: ratePct(jointHit, total),
      remainHit,
      remainRate: ratePct(remainHit, total),
      positions: pos
    };
  }

  const byWindow = Object.create(null);
  EVAL_WINDOWS.forEach((n) => {
    byWindow["w" + n] = windowStats(n);
  });
  const w30 = byWindow.w30 || null;
  const w120 = byWindow.w120 || null;

  const perPosition = Object.create(null);
  POSITIONS.forEach((pos, idx) => {
    const p30 = w30 && w30.positions[idx] ? w30.positions[idx] : null;
    const p120 = w120 && w120.positions[idx] ? w120.positions[idx] : null;
    const allHits = outcomes.map((o) => o.hits[idx] === true);
    const sAll = streakFromHits(allHits);
    const s30 = streakFromHits(allHits.slice(-30));
    const s120 = streakFromHits(allHits.slice(-120));
    perPosition[pos.key] = {
      key: pos.key,
      label: pos.label,
      rate30: p30 ? p30.rate : null,
      hit30: p30 ? p30.hit : 0,
      total30: p30 ? p30.total : 0,
      rate120: p120 ? p120.rate : null,
      hit120: p120 ? p120.hit : 0,
      total120: p120 ? p120.total : 0,
      currentWin: sAll.currentWin,
      currentMiss: sAll.currentMiss,
      maxWin: sAll.maxWin,
      maxMiss: sAll.maxMiss,
      currentWin30: s30.currentWin,
      currentMiss30: s30.currentMiss,
      maxWin30: s30.maxWin,
      maxMiss30: s30.maxMiss,
      currentWin120: s120.currentWin,
      currentMiss120: s120.currentMiss,
      maxWin120: s120.maxWin,
      maxMiss120: s120.maxMiss
    };
  });

  const jointHits = outcomes.map((o) => o.jointHit === true);
  const jointStreak = streakFromHits(jointHits);
  const remainHits = outcomes.map((o) => o.remainHit === true);
  const remainStreak = streakFromHits(remainHits);

  const latestPeriod = outcomes.length ? String(outcomes[outcomes.length - 1].period || "") : "";
  const todayKey = businessDayKeyFromPeriod(latestPeriod);
  const todayOutcomes = todayKey
    ? outcomes.filter((o) => businessDayKeyFromPeriod(o.period) === todayKey)
    : [];
  const jointTodayStreak = streakFromHits(todayOutcomes.map((o) => o.jointHit === true));
  const remainTodayStreak = streakFromHits(todayOutcomes.map((o) => o.remainHit === true));

  function packRate(streak, todayStreak, hitKey, rateKey) {
    return {
      rate30: w30 ? w30[rateKey] : null,
      hit30: w30 ? w30[hitKey] : 0,
      total30: w30 ? w30.total : 0,
      rate120: w120 ? w120[rateKey] : null,
      hit120: w120 ? w120[hitKey] : 0,
      total120: w120 ? w120.total : 0,
      currentWin: streak.currentWin,
      currentMiss: streak.currentMiss,
      maxWin: streak.maxWin,
      maxMiss: streak.maxMiss,
      todayKey: todayKey || "",
      todayTotal: todayStreak.total,
      todayHit: todayStreak.hit,
      currentWinToday: todayStreak.currentWin,
      currentMissToday: todayStreak.currentMiss,
      maxWinToday: todayStreak.maxWin,
      maxMissToday: todayStreak.maxMiss
    };
  }

  return {
    method: "walk-forward",
    version: VERSION,
    evalCount: outcomes.length,
    windows: byWindow,
    positions: perPosition,
    // 联合=正路盖全 ≈ 排除组数/10000；meaning 保持 kill-not-in-remain
    joint: Object.assign(packRate(jointStreak, jointTodayStreak, "jointHit", "jointRate"), {
      meaning: "kill-not-in-remain",
      align: "exclude-groups"
    }),
    // 剩码 = 开奖在池 ≈ 剩号组数/10000
    remain: Object.assign(packRate(remainStreak, remainTodayStreak, "remainHit", "remainRate"), {
      meaning: "remain-pool",
      align: "remain-groups"
    })
  };
}

function compute(history) {
  const rows = chronologic(history);
  if (rows.length < MIN_TRAIN) {
    return {
      ok: false,
      error: "history-too-short",
      historyCount: rows.length
    };
  }
  const train = rows; // 全窗训练；最新期参与转移/遗漏
  const latest = train[train.length - 1];
  const backtest = walkForward(train, Number(process.env.FOUR_FACTOR_EVAL_N || 120));
  const dropPack = pickDropKills(train);
  const dropKills = dropPack.kills;
  const remainCount = remainCountForKills(dropKills);
  const excludeCount = FULL_COUNT - remainCount;
  const poolSharePct = ratePct(remainCount, FULL_COUNT);
  const excludeSharePct = ratePct(excludeCount, FULL_COUNT);
  const byPosDrop = Object.create(null);
  POSITIONS.forEach((pos) => {
    byPosDrop[pos.key] = dropKills.filter((k) => k.key === pos.key).map((k) => String(k.digit));
  });
  const primaryByKey = Object.create(null);
  (dropPack.primary || []).forEach((k) => {
    if (k && k.key) primaryByKey[k.key] = k;
  });
  const items = POSITIONS.map((pos) => {
    const seq = train.map((r) => String(r.numbers[pos.index]));
    const picked = scorePosition(seq);
    const bt = backtest.positions[pos.key] || null;
    const dropDigits = byPosDrop[pos.key] || [];
    const primaryKill = primaryByKey[pos.key] || null;
    const showDigit = primaryKill ? String(primaryKill.digit) : String(picked.digit);
    const showScore = primaryKill ? Number(primaryKill.score || 0) : picked.score;
    const showMiss = primaryKill && primaryKill.miss != null ? primaryKill.miss : picked.miss;
    return {
      key: pos.key,
      label: pos.label,
      index: pos.index,
      digit: showDigit,
      score: showScore,
      miss: showMiss,
      top3: picked.top3,
      parts: picked.parts,
      dropDigits,
      dropCount: dropDigits.length,
      rate30: bt ? bt.rate30 : null,
      hit30: bt ? bt.hit30 : 0,
      total30: bt ? bt.total30 : 0,
      rate120: bt ? bt.rate120 : null,
      hit120: bt ? bt.hit120 : 0,
      total120: bt ? bt.total120 : 0,
      currentWin: bt ? bt.currentWin : 0,
      currentMiss: bt ? bt.currentMiss : 0,
      maxWin: bt ? bt.maxWin : 0,
      maxMiss: bt ? bt.maxMiss : 0,
      currentWin30: bt ? bt.currentWin30 : 0,
      currentMiss30: bt ? bt.currentMiss30 : 0,
      maxWin30: bt ? bt.maxWin30 : 0,
      maxMiss30: bt ? bt.maxMiss30 : 0,
      currentWin120: bt ? bt.currentWin120 : 0,
      currentMiss120: bt ? bt.currentMiss120 : 0,
      maxWin120: bt ? bt.maxWin120 : 0,
      maxMiss120: bt ? bt.maxMiss120 : 0
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
    side: "forward",
    mark: "联合正路排除池",
    note: "主排4+追加3高分排除7 · 转发默认排除侧(正路盖全) · 剩号=反路 · 联合命中率越低越好",
    weights: WEIGHTS,
    dropN: DROP_TOTAL_N,
    dropPrimaryN: DROP_PRIMARY_N,
    dropExtraN: DROP_EXTRA_N,
    generatedAt: new Date().toISOString(),
    historyCount: train.length,
    latestPeriod: latest.period,
    latestNumbers: latest.numbers.slice(),
    picks: byKey,
    items,
    dropKills,
    dropPrimary: dropPack.primary,
    dropExtra: dropPack.extra,
    remainCount,
    excludeCount,
    fullCount: FULL_COUNT,
    poolSharePct,
    excludeSharePct,
    shot: dropKills.map((k) => k.attack).filter(Boolean).join(""),
    backtest,
    joint: backtest.joint,
    remain: backtest.remain || null,
    line: items.map((it) => it.label + it.digit).join(" / "),
    reverseLine: dropKills.map((k) => k.label + k.digit).join(" / ")
  };
}

function remainCodesFromState(state) {
  const kills = state && Array.isArray(state.dropKills) ? state.dropKills : [];
  if (!kills.length) return [];
  const codes = buildRemainCodes(kills);
  const expect = remainCountForKills(kills);
  if (codes.length !== expect) {
    throw new Error("remain truncate: built " + codes.length + " != expect " + expect);
  }
  for (let i = 0; i < codes.length; i += 1) {
    if (!/^\d{4}$/.test(codes[i])) {
      throw new Error("remain short-code at " + (i + 1) + ": " + String(codes[i]));
    }
  }
  return codes;
}

function excludeCodesFromState(state) {
  const kills = state && Array.isArray(state.dropKills) ? state.dropKills : [];
  if (!kills.length) return [];
  const codes = buildExcludeCodes(kills);
  const remainExpect = remainCountForKills(kills);
  const expect = FULL_COUNT - remainExpect;
  if (codes.length !== expect) {
    throw new Error("exclude truncate: built " + codes.length + " != expect " + expect);
  }
  for (let i = 0; i < codes.length; i += 1) {
    if (!/^\d{4}$/.test(codes[i])) {
      throw new Error("exclude short-code at " + (i + 1) + ": " + String(codes[i]));
    }
  }
  return codes;
}

async function refresh(options) {
  options = options && typeof options === "object" ? options : {};
  const history = await lib.fetchHistory(Number(process.env.FOUR_FACTOR_HISTORY_LIMIT || 250));
  if (!history.length) throw new Error("history empty");
  const rows = chronologic(history);
  const latestPeriod = rows.length ? String(rows[rows.length - 1].period || "") : "";
  // 同期未变：只拉历史核对期号，跳过重算与写盘
  if (!options.force && options.prevPeriod && latestPeriod && latestPeriod === String(options.prevPeriod)) {
    return {
      ok: true,
      skipped: true,
      latestPeriod,
      historyCount: rows.length
    };
  }
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
  DROP_TOTAL_N,
  DROP_EXTRA_N,
  compute,
  walkForward,
  refresh,
  outPaths,
  pickDropKills,
  buildRemainCodes,
  buildExcludeCodes,
  remainCountForKills,
  remainCodesFromState,
  excludeCodesFromState
};
