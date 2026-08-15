"use strict";

/**
 * 搭配模型 v2.2：2 定位避开 + 千百十个合尾避开，三件全过才算中。
 * 真实口径：
 * - 随机基线 0.9^3 ≈ 72.9%；硬指标只认 walk-forward
 * - 实盘 tip 与 walk-forward 同一套「选优 + 粘性」，禁止评估/出码两套参数
 * - 打分偏长窗单件下界，降近窗追热；位置对相关超额失败要扣分
 * 只用共享 history，不进 tipReady 核心门禁。
 */

const {
  MODEL_LONG_WINDOW,
  wilsonLowerRate
} = require("../_shared/formula_kit");

const VERSION = "dual-pos-total-hewei-v2.2-sticky-honest";
const LABEL = "二定+千百十个合尾";
const KIND = "dualPosTotalHewei";

const LOOKBACK = 120;
const SIM_WINDOW = Number(process.env.DUAL_POS_TOTAL_HEWEI_SIM_WINDOW || 120) || 120;
/** 长窗统计上限：避免对 5000 期全表反复扫把启动拖死；与真实口径仍够稳 */
const LONG_STAT_WINDOW = Number(process.env.DUAL_POS_TOTAL_HEWEI_LONG_WINDOW || 800) || 800;
const JOINT_EVAL_CAP = Number(process.env.DUAL_POS_TOTAL_HEWEI_JOINT_CAP || 120) || 120;
const TOP_POS_DIGITS = Number(process.env.DUAL_POS_TOTAL_HEWEI_TOP_POS || 2) || 2;
const TOP_HEWEI_DIGITS = Number(process.env.DUAL_POS_TOTAL_HEWEI_TOP_HW || 2) || 2;
const MIN_COMPONENT_LOWER_LONG = Number(process.env.DUAL_POS_TOTAL_HEWEI_MIN_LOWER_LONG || 88) || 88;
const STICKY_HOLD = Number(process.env.DUAL_POS_TOTAL_HEWEI_STICKY_HOLD || 8) || 8;
const STICKY_MIN_LOWER = Number(process.env.DUAL_POS_TOTAL_HEWEI_STICKY_MIN_LOWER || 70) || 70;
/** 均匀随机下三件独立全过的理论基线 */
const JOINT_BASELINE = 72.9;

const POSITION_LIST = Object.freeze([
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 }
]);

const PAIR_KEYS = Object.freeze([
  [0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]
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

function totalHewei(record) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  if (nums.length < 4) return "";
  return String((Number(nums[0]) + Number(nums[1]) + Number(nums[2]) + Number(nums[3])) % 10);
}

function avoidStats(okSeries) {
  let total = 0;
  let hit = 0;
  let missStreak = 0;
  let maxMiss = 0;
  let run = 0;
  let leading = true;
  for (let i = 0; i < okSeries.length; i += 1) {
    const ok = okSeries[i];
    if (ok == null) continue;
    total += 1;
    if (ok) {
      hit += 1;
      run = 0;
      leading = false;
    } else {
      run += 1;
      if (run > maxMiss) maxMiss = run;
      if (leading) missStreak += 1;
    }
  }
  const rate = total ? (hit * 100) / total : 0;
  return {
    total,
    hit,
    rate: Number(rate.toFixed(2)),
    lower: Number(wilsonLowerRate(hit, total, 1.44).toFixed(2)),
    missStreak,
    maxMissStreak: maxMiss
  };
}

/** newest-first records → digit avoid stats for one position */
function positionDigitStats(historyNewestFirst, index, limit) {
  const n = Math.max(1, Number(limit || LOOKBACK));
  const out = [];
  for (let d = 0; d < 10; d += 1) {
    const series = [];
    for (let i = 0; i < historyNewestFirst.length && i < n; i += 1) {
      const actual = actualDigit(historyNewestFirst[i], index);
      if (!/^\d$/.test(actual)) continue;
      series.push(actual !== String(d));
    }
    const st = avoidStats(series);
    out.push({
      digit: String(d),
      index,
      key: POSITION_LIST[index].key,
      label: POSITION_LIST[index].label,
      rate: st.rate,
      lower: st.lower,
      total: st.total,
      hit: st.hit,
      missStreak: st.missStreak
    });
  }
  out.sort((a, b) => {
    if (b.lower !== a.lower) return b.lower - a.lower;
    if (b.rate !== a.rate) return b.rate - a.rate;
    if (a.missStreak !== b.missStreak) return a.missStreak - b.missStreak;
    return String(a.digit).localeCompare(String(b.digit));
  });
  return out;
}

function heweiDigitStats(historyNewestFirst, limit) {
  const n = Math.max(1, Number(limit || LOOKBACK));
  const out = [];
  for (let d = 0; d < 10; d += 1) {
    const series = [];
    for (let i = 0; i < historyNewestFirst.length && i < n; i += 1) {
      const hw = totalHewei(historyNewestFirst[i]);
      if (!/^\d$/.test(hw)) continue;
      series.push(hw !== String(d));
    }
    const st = avoidStats(series);
    out.push({
      digit: String(d),
      rate: st.rate,
      lower: st.lower,
      total: st.total,
      hit: st.hit,
      missStreak: st.missStreak
    });
  }
  out.sort((a, b) => {
    if (b.lower !== a.lower) return b.lower - a.lower;
    if (b.rate !== a.rate) return b.rate - a.rate;
    if (a.missStreak !== b.missStreak) return a.missStreak - b.missStreak;
    return String(a.digit).localeCompare(String(b.digit));
  });
  return out;
}

function simulateCombo(historyNewestFirst, posA, posB, heweiDigit, limit) {
  const n = Math.max(20, Number(limit || SIM_WINDOW));
  const series = [];
  for (let i = 0; i < historyNewestFirst.length && i < n; i += 1) {
    const row = historyNewestFirst[i];
    const a = actualDigit(row, posA.index);
    const b = actualDigit(row, posB.index);
    const hw = totalHewei(row);
    if (!/^\d$/.test(a) || !/^\d$/.test(b) || !/^\d$/.test(hw)) continue;
    series.push(a !== posA.digit && b !== posB.digit && hw !== String(heweiDigit));
  }
  return avoidStats(series);
}

/** 两定位同时「撞码失败」相对独立假设的超额（百分点），越大越不该搭配 */
function pairFailExcess(historyNewestFirst, posA, posB, limit) {
  const nMax = Math.max(40, Number(limit || SIM_WINDOW));
  let n = 0;
  let aFail = 0;
  let bFail = 0;
  let bothFail = 0;
  for (let i = 0; i < historyNewestFirst.length && n < nMax; i += 1) {
    const row = historyNewestFirst[i];
    const a = actualDigit(row, posA.index);
    const b = actualDigit(row, posB.index);
    if (!/^\d$/.test(a) || !/^\d$/.test(b)) continue;
    n += 1;
    const af = a === String(posA.digit);
    const bf = b === String(posB.digit);
    if (af) aFail += 1;
    if (bf) bFail += 1;
    if (af && bf) bothFail += 1;
  }
  if (n < 30) return 0;
  const indep = (aFail / n) * (bFail / n);
  const actual = bothFail / n;
  return Math.max(0, (actual - indep) * 100);
}

function pickOptimalCombo(historyNewestFirst, options) {
  const opts = options && typeof options === "object" ? options : {};
  const simWindow = Math.max(40, Number(opts.simWindow || SIM_WINDOW));
  const longWindow = Math.max(
    simWindow,
    Math.min(LONG_STAT_WINDOW, Math.min(MODEL_LONG_WINDOW, historyNewestFirst.length || LONG_STAT_WINDOW))
  );
  const topPos = Math.max(1, Math.min(5, Number(opts.topPos || TOP_POS_DIGITS)));
  const topHw = Math.max(1, Math.min(8, Number(opts.topHw || TOP_HEWEI_DIGITS)));
  const minLowerLong = Number(opts.minLowerLong != null ? opts.minLowerLong : MIN_COMPONENT_LOWER_LONG);

  const posLong = POSITION_LIST.map((p) => positionDigitStats(historyNewestFirst, p.index, longWindow));
  const posNear = POSITION_LIST.map((p) => positionDigitStats(historyNewestFirst, p.index, simWindow));
  const hwLong = heweiDigitStats(historyNewestFirst, longWindow);
  const hwNear = heweiDigitStats(historyNewestFirst, simWindow);

  const byPosDigitLong = posLong.map((list) => {
    const map = Object.create(null);
    list.forEach((item) => { map[item.digit] = item; });
    return map;
  });
  const hwLongMap = Object.create(null);
  hwLong.forEach((item) => { hwLongMap[item.digit] = item; });

  // 单件池：长窗下界达标者优先；不够再放宽取 top
  function poolForPos(idx) {
    const longList = posLong[idx] || [];
    const qualified = longList.filter((item) => Number(item.lower || 0) >= minLowerLong);
    const base = (qualified.length >= topPos ? qualified : longList).slice(0, topPos);
    // 用近窗对象带上近窗率，保留长窗下界
    return base.map((longItem) => {
      const near = (posNear[idx] || []).find((x) => x.digit === longItem.digit) || longItem;
      return {
        digit: longItem.digit,
        index: idx,
        key: POSITION_LIST[idx].key,
        label: POSITION_LIST[idx].label,
        rateLong: longItem.rate,
        lowerLong: longItem.lower,
        sampleLong: longItem.total,
        rate120: near.rate,
        lower120: near.lower,
        missStreak: near.missStreak
      };
    });
  }

  const pools = POSITION_LIST.map((_, idx) => poolForPos(idx));
  let hwPool = hwLong.filter((item) => Number(item.lower || 0) >= minLowerLong).slice(0, topHw);
  if (hwPool.length < topHw) hwPool = hwLong.slice(0, topHw);
  hwPool = hwPool.map((longItem) => {
    const near = hwNear.find((x) => x.digit === longItem.digit) || longItem;
    return {
      digit: longItem.digit,
      rateLong: longItem.rate,
      lowerLong: longItem.lower,
      sampleLong: longItem.total,
      rate120: near.rate,
      lower120: near.lower,
      missStreak: near.missStreak
    };
  });

  let best = null;
  const scanned = [];
  for (let p = 0; p < PAIR_KEYS.length; p += 1) {
    const ia = PAIR_KEYS[p][0];
    const ib = PAIR_KEYS[p][1];
    const poolA = pools[ia] || [];
    const poolB = pools[ib] || [];
    for (let a = 0; a < poolA.length; a += 1) {
      for (let b = 0; b < poolB.length; b += 1) {
        for (let h = 0; h < hwPool.length; h += 1) {
          const posA = poolA[a];
          const posB = poolB[b];
          const hw = hwPool[h];
          const sim = simulateCombo(historyNewestFirst, posA, posB, hw.digit, simWindow);
          const minCompLower = Math.min(
            Number(posA.lowerLong || 0),
            Number(posB.lowerLong || 0),
            Number(hw.lowerLong || 0)
          );
          const corrExcess = pairFailExcess(historyNewestFirst, posA, posB, simWindow);
          const indepExpect = (
            Number(posA.rateLong || 0) / 100
          ) * (
            Number(posB.rateLong || 0) / 100
          ) * (
            Number(hw.rateLong || 0) / 100
          ) * 100;
          // 近窗联合率高于独立期望 = 过拟合，要扣；目标贴住 ~72.9% 真实基线
          const overfitGap = Math.max(0, Number(sim.rate || 0) - indepExpect);
          const score = (
            indepExpect * 0.34 +
            minCompLower * 0.34 +
            Number(sim.lower || 0) * 0.18 +
            Number(sim.rate || 0) * 0.06 -
            overfitGap * 0.55 -
            Number(sim.missStreak || 0) * 1.6 -
            corrExcess * 1.0
          );
          const cand = {
            positions: [posA, posB],
            hewei: hw,
            sim,
            minCompLower,
            indepExpect: Number(indepExpect.toFixed(3)),
            overfitGap: Number(overfitGap.toFixed(3)),
            corrExcess: Number(corrExcess.toFixed(3)),
            score: Number(score.toFixed(4))
          };
          scanned.push(cand);
          if (!best ||
              cand.score > best.score ||
              (cand.score === best.score && Number(cand.sim.lower) > Number(best.sim.lower)) ||
              (cand.score === best.score && Number(cand.sim.lower) === Number(best.sim.lower) && Number(cand.sim.rate) > Number(best.sim.rate))) {
            best = cand;
          }
        }
      }
    }
  }

  if (!best) {
    // 极端空池兜底：各位长窗第一 + 合尾长窗第一
    const fallbackPos = [0, 1].map((idx) => {
      const longItem = (posLong[idx] || [])[0] || { digit: "0", lower: 0, rate: 0, total: 0 };
      return {
        digit: longItem.digit || "0",
        index: idx,
        key: POSITION_LIST[idx].key,
        label: POSITION_LIST[idx].label,
        rateLong: longItem.rate || 0,
        lowerLong: longItem.lower || 0,
        sampleLong: longItem.total || 0,
        rate120: longItem.rate || 0,
        lower120: longItem.lower || 0,
        missStreak: longItem.missStreak || 0
      };
    });
    const hw0 = hwLong[0] || { digit: "0", rate: 0, lower: 0, total: 0 };
    const hw = {
      digit: hw0.digit || "0",
      rateLong: hw0.rate || 0,
      lowerLong: hw0.lower || 0,
      sampleLong: hw0.total || 0,
      rate120: hw0.rate || 0,
      lower120: hw0.lower || 0,
      missStreak: hw0.missStreak || 0
    };
    const sim = simulateCombo(historyNewestFirst, fallbackPos[0], fallbackPos[1], hw.digit, simWindow);
    best = {
      positions: fallbackPos,
      hewei: hw,
      sim,
      minCompLower: Math.min(fallbackPos[0].lowerLong, fallbackPos[1].lowerLong, hw.lowerLong),
      score: Number(sim.lower || 0)
    };
  }

  scanned.sort((x, y) => y.score - x.score);
  return {
    best,
    runnersUp: scanned.slice(1, 4),
    meta: {
      simWindow,
      longWindow,
      topPos,
      topHw,
      minLowerLong,
      scanned: scanned.length
    }
  };
}

function windowStats(outcomes, n) {
  const slice = n >= outcomes.length ? outcomes : outcomes.slice(-n);
  const total = slice.length;
  let hit = 0;
  let missStreak = 0;
  let maxMiss = 0;
  let run = 0;
  for (let i = 0; i < slice.length; i += 1) {
    if (slice[i]) {
      hit += 1;
      run = 0;
    } else {
      run += 1;
      if (run > maxMiss) maxMiss = run;
    }
  }
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (!slice[i]) missStreak += 1;
    else break;
  }
  const rate = total ? (hit * 100) / total : 0;
  return {
    window: n >= outcomes.length ? total : n,
    hit,
    total,
    rate: Number(rate.toFixed(2)),
    lower: Number(wilsonLowerRate(hit, total, 1.96).toFixed(2)),
    missStreak,
    maxMissStreak: maxMiss
  };
}

function tipFromBest(best) {
  if (!best || !Array.isArray(best.positions) || best.positions.length !== 2 || !best.hewei) return null;
  return {
    positions: best.positions.map((p) => ({
      index: p.index,
      key: p.key,
      label: p.label,
      digit: p.digit
    })),
    heweiDigit: String(best.hewei.digit || "")
  };
}

function tipKey(tip) {
  if (!tip || !Array.isArray(tip.positions) || tip.positions.length !== 2) return "";
  const parts = tip.positions
    .map((p) => String(p.index) + ":" + String(p.digit))
    .sort();
  return parts.join("|") + "#h" + String(tip.heweiDigit || "");
}

/** 把粘住的 tip 还原成带近窗 sim 的 best 结构（出码展示用） */
function bestFromTip(historyNewestFirst, tip, opts) {
  if (!tip || !Array.isArray(tip.positions) || tip.positions.length !== 2) return null;
  const simWindow = Math.max(40, Number(opts && opts.simWindow || SIM_WINDOW));
  const longWindow = Math.max(
    simWindow,
    Math.min(LONG_STAT_WINDOW, Math.min(MODEL_LONG_WINDOW, historyNewestFirst.length || LONG_STAT_WINDOW))
  );
  const positions = tip.positions.map((p) => {
    const idx = Number(p.index);
    const longList = positionDigitStats(historyNewestFirst, idx, longWindow);
    const nearList = positionDigitStats(historyNewestFirst, idx, simWindow);
    const longItem = longList.find((x) => x.digit === String(p.digit)) || longList[0] || { digit: p.digit, rate: 0, lower: 0, total: 0 };
    const near = nearList.find((x) => x.digit === String(p.digit)) || longItem;
    return {
      digit: String(p.digit),
      index: idx,
      key: p.key || (POSITION_LIST[idx] && POSITION_LIST[idx].key) || "",
      label: p.label || (POSITION_LIST[idx] && POSITION_LIST[idx].label) || "",
      rateLong: longItem.rate,
      lowerLong: longItem.lower,
      sampleLong: longItem.total,
      rate120: near.rate,
      lower120: near.lower,
      missStreak: near.missStreak
    };
  });
  const hwLong = heweiDigitStats(historyNewestFirst, longWindow);
  const hwNear = heweiDigitStats(historyNewestFirst, simWindow);
  const hw0 = hwLong.find((x) => x.digit === String(tip.heweiDigit)) || hwLong[0] || { digit: tip.heweiDigit, rate: 0, lower: 0, total: 0 };
  const hwN = hwNear.find((x) => x.digit === String(tip.heweiDigit)) || hw0;
  const hewei = {
    digit: String(tip.heweiDigit || ""),
    rateLong: hw0.rate,
    lowerLong: hw0.lower,
    sampleLong: hw0.total,
    rate120: hwN.rate,
    lower120: hwN.lower,
    missStreak: hwN.missStreak
  };
  const sim = simulateCombo(historyNewestFirst, positions[0], positions[1], hewei.digit, simWindow);
  const corrExcess = pairFailExcess(historyNewestFirst, positions[0], positions[1], simWindow);
  const minCompLower = Math.min(positions[0].lowerLong, positions[1].lowerLong, hewei.lowerLong);
  const indepExpect = (positions[0].rateLong / 100) * (positions[1].rateLong / 100) * (hewei.rateLong / 100) * 100;
  const overfitGap = Math.max(0, Number(sim.rate || 0) - indepExpect);
  const score = (
    indepExpect * 0.34 +
    minCompLower * 0.34 +
    Number(sim.lower || 0) * 0.18 +
    Number(sim.rate || 0) * 0.06 -
    overfitGap * 0.55 -
    Number(sim.missStreak || 0) * 1.6 -
    corrExcess * 1.0
  );
  return {
    positions,
    hewei,
    sim,
    minCompLower,
    indepExpect: Number(indepExpect.toFixed(3)),
    overfitGap: Number(overfitGap.toFixed(3)),
    corrExcess: Number(corrExcess.toFixed(3)),
    score: Number(score.toFixed(4)),
    sticky: true
  };
}

function evaluateFixedTip(actual, tip) {
  if (!tip || !actual) return false;
  const posOk = (tip.positions || []).every((k) => actualDigit(actual, k.index) !== k.digit);
  const hwOk = totalHewei(actual) !== String(tip.heweiDigit);
  return posOk && hwOk;
}

function livePickOptions() {
  return {
    simWindow: SIM_WINDOW,
    topPos: TOP_POS_DIGITS,
    topHw: TOP_HEWEI_DIGITS,
    minLowerLong: MIN_COMPONENT_LOWER_LONG
  };
}

/** walk-forward 与实盘同构：同一选优参数 + 粘性锁；返回 nextBest 供当期出 tip */
function walkForwardJointStats(historyNewestFirst) {
  const chrono = asHistory(historyNewestFirst).slice().reverse();
  const need = Math.max(LOOKBACK, SIM_WINDOW) + 10;
  const pickOpts = livePickOptions();
  if (chrono.length <= need) {
    const picked = pickOptimalCombo(historyNewestFirst, pickOpts);
    return {
      evalPeriods: 0,
      windows: [],
      method: "select-optimal-sticky-honest",
      baseline: JOINT_BASELINE,
      nextBest: picked.best,
      nextTip: tipFromBest(picked.best),
      nextRunnersUp: picked.runnersUp || [],
      nextPickMeta: picked.meta || null
    };
  }
  const start = Math.max(Math.max(LOOKBACK, SIM_WINDOW), chrono.length - JOINT_EVAL_CAP);
  const outcomes = [];
  let stickyTip = null;
  let stickyHoldLeft = 0;
  for (let i = start; i < chrono.length; i += 1) {
    const histBeforeNewestFirst = chrono.slice(0, i).slice().reverse();
    let tip = stickyTip;
    let refresh = !tip || stickyHoldLeft <= 0;
    if (tip && !refresh) {
      const sim = simulateCombo(
        histBeforeNewestFirst,
        tip.positions[0],
        tip.positions[1],
        tip.heweiDigit,
        Math.min(80, SIM_WINDOW)
      );
      if (Number(sim.lower || 0) < STICKY_MIN_LOWER) refresh = true;
    }
    if (refresh) {
      const picked = pickOptimalCombo(histBeforeNewestFirst, pickOpts);
      tip = tipFromBest(picked.best);
      stickyTip = tip;
      stickyHoldLeft = STICKY_HOLD;
    }
    stickyHoldLeft -= 1;
    outcomes.push(evaluateFixedTip(chrono[i], tip));
  }

  // 实盘 tip = walk-forward 粘性状态在「最新一期之后」的续持/重选（同构）
  let nextTip = stickyTip;
  let nextRefresh = !nextTip || stickyHoldLeft <= 0;
  if (nextTip && !nextRefresh) {
    const simNow = simulateCombo(
      historyNewestFirst,
      nextTip.positions[0],
      nextTip.positions[1],
      nextTip.heweiDigit,
      Math.min(80, SIM_WINDOW)
    );
    if (Number(simNow.lower || 0) < STICKY_MIN_LOWER) nextRefresh = true;
  }
  let nextBest = null;
  let nextRunnersUp = [];
  let nextPickMeta = null;
  if (nextRefresh) {
    const pickedNow = pickOptimalCombo(historyNewestFirst, pickOpts);
    nextBest = pickedNow.best;
    nextRunnersUp = pickedNow.runnersUp || [];
    nextPickMeta = pickedNow.meta || null;
    nextTip = tipFromBest(nextBest);
  } else {
    nextBest = bestFromTip(historyNewestFirst, nextTip, pickOpts);
    nextPickMeta = {
      simWindow: SIM_WINDOW,
      topPos: TOP_POS_DIGITS,
      topHw: TOP_HEWEI_DIGITS,
      minLowerLong: MIN_COMPONENT_LOWER_LONG,
      sticky: true,
      stickyHold: STICKY_HOLD
    };
  }

  const caps = [30, 120, outcomes.length];
  const windows = [];
  const seen = new Set();
  caps.forEach((n) => {
    const key = n >= outcomes.length ? "all" : String(n);
    if (seen.has(key)) return;
    seen.add(key);
    const st = windowStats(outcomes, n);
    st.baseline = JOINT_BASELINE;
    st.edgeOverBaseline = st.rate != null
      ? Number((Number(st.rate) - JOINT_BASELINE).toFixed(2))
      : null;
    windows.push(st);
  });
  return {
    evalPeriods: outcomes.length,
    windows,
    method: "select-optimal-sticky-honest",
    baseline: JOINT_BASELINE,
    stickyHold: STICKY_HOLD,
    nextBest,
    nextTip,
    nextTipKey: tipKey(nextTip),
    nextRunnersUp,
    nextPickMeta
  };
}

function buildSnapshot(history) {
  const safeHistory = asHistory(history);
  const latest = safeHistory[0] || null;
  const joint = walkForwardJointStats(safeHistory);
  let best = joint.nextBest || null;
  let runnersUp = Array.isArray(joint.nextRunnersUp) ? joint.nextRunnersUp : [];
  let pickMeta = joint.nextPickMeta || null;
  if (!best) {
    const picked = pickOptimalCombo(safeHistory, livePickOptions());
    best = picked.best;
    runnersUp = picked.runnersUp || [];
    pickMeta = picked.meta || null;
  }
  const items = (best.positions || []).map((p) => ({
    kind: "position",
    key: p.key,
    label: p.label,
    index: p.index,
    code: p.digit,
    rate120: p.rate120,
    rateLong: p.rateLong,
    lowerLong: p.lowerLong,
    sampleLong: p.sampleLong,
    missStreak: p.missStreak
  }));
  const heweiItem = {
    kind: "hewei",
    key: "wanQianBaiShi",
    label: "千百十个合尾",
    code: best.hewei && best.hewei.digit || "",
    rate120: best.hewei && best.hewei.rate120,
    rateLong: best.hewei && best.hewei.rateLong,
    lowerLong: best.hewei && best.hewei.lowerLong,
    sampleLong: best.hewei && best.hewei.sampleLong,
    missStreak: best.hewei && best.hewei.missStreak
  };
  const tipText = items.map((it) => (it.label || "") + (it.code || "")).join("、")
    + " + 合" + (heweiItem.code || "--");
  const win120 = (joint.windows || []).find((w) => Number(w.window) === 120)
    || (joint.windows || []).find((w) => Number(w.total) >= 30)
    || null;
  return {
    version: VERSION,
    kind: KIND,
    label: LABEL,
    algorithm: "dual-pos-total-hewei",
    pickMethod: "select-optimal-sticky-honest",
    basePeriod: latest && latest.period != null ? String(latest.period) : "",
    generatedAt: new Date().toISOString(),
    items,
    hewei: heweiItem,
    tipText,
    itemCount: items.length,
    complete: items.length === 2
      && items.every((it) => /^\d$/.test(String(it.code || "")))
      && /^\d$/.test(String(heweiItem.code || ""))
      && safeHistory.length >= LOOKBACK
      && Number((best.sim && best.sim.total) || 0) >= 30,
    comboScore: best.score,
    comboSim: best.sim || null,
    minCompLower: best.minCompLower,
    corrExcess: best.corrExcess != null ? best.corrExcess : null,
    sticky: best.sticky === true,
    runnersUp: (runnersUp || []).slice(0, 3).map((c) => ({
      tipText: (c.positions || []).map((p) => (p.label || "") + (p.digit || "")).join("、")
        + " + 合" + (c.hewei && c.hewei.digit || ""),
      score: c.score,
      simRate: c.sim && c.sim.rate,
      simLower: c.sim && c.sim.lower
    })),
    joint,
    baseline: JOINT_BASELINE,
    edgeOverBaseline: win120 && win120.edgeOverBaseline != null
      ? win120.edgeOverBaseline
      : (win120 && win120.rate != null
        ? Number((Number(win120.rate) - JOINT_BASELINE).toFixed(2))
        : null),
    note: "真实口径：walk-forward=实盘同构粘性选优；随机基线72.9%；近窗回放仅说明当期组合，不算硬指标",
    source: {
      officialHistory: "rds-shared-read-only",
      historyCount: safeHistory.length,
      longWindow: LONG_STAT_WINDOW,
      simWindow: SIM_WINDOW,
      jointEvalCap: JOINT_EVAL_CAP,
      lookback: LOOKBACK,
      stickyHold: STICKY_HOLD,
      stickyMinLower: STICKY_MIN_LOWER,
      baseline: JOINT_BASELINE,
      pickMeta: pickMeta
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
  if (histCount > 0 && histCount < LOOKBACK) return false;
  const simTotal = Number(snap.comboSim && snap.comboSim.total || 0);
  if (simTotal > 0 && simTotal < 30) return false;
  // 拒绝启动占位：历史不足时穷举会塌成 0/0/合0
  if (itemsArePlaceholder(snap)) return false;
  return snap.complete === true;
}

function itemsArePlaceholder(snap) {
  const items = Array.isArray(snap && snap.items) ? snap.items : [];
  const hw = snap && snap.hewei && snap.hewei.code != null ? String(snap.hewei.code) : "";
  if (items.length !== 2 || hw !== "0") return false;
  return items.every((it) => String(it && it.code || "") === "0");
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
    return {
      key: it.key,
      label: it.label,
      code,
      actual: actual || "",
      success: ok
    };
  });
  const hwCode = String(snap.hewei.code);
  const hwActual = String((Number(numbers[0]) + Number(numbers[1]) + Number(numbers[2]) + Number(numbers[3])) % 10);
  const heweiOk = hwActual !== hwCode;
  const posOk = posResults.every((r) => r.success === true);
  const success = posOk && heweiOk;
  return {
    version: VERSION,
    kind: KIND,
    label: LABEL,
    success,
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
  LOOKBACK,
  SIM_WINDOW,
  JOINT_EVAL_CAP,
  JOINT_BASELINE,
  buildSnapshot,
  snapshotIsComplete,
  evaluateSnapshot,
  pickOptimalCombo
};
