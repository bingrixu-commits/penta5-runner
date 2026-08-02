"use strict";

/**
 * REAL position-kill formula extracted from penta5_backend_runner.js.
 * No sibling algorithm-projects state.json reads.
 */

const {
  DIGIT_POOL,
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  MODEL_LOW_HIT_RATE,
  getModelStat,
  modelPerformanceSummary,
  stableWindowScore,
  stableWindowStage,
  wilsonLowerRate,
  rateVariance
} = require("../../_shared/formula_kit");

const POSITION_BLOCK_WINDOW = Number(process.env.POSITION_BLOCK_WINDOW || 120);
const POSITION_BLOCK_SIZE = Number(process.env.POSITION_BLOCK_SIZE || 20);
const POSITION_BLOCK_FLOOR = Number(process.env.POSITION_BLOCK_FLOOR || 65);
const POSITION_SWITCH_MARGIN = Number(process.env.POSITION_SWITCH_MARGIN || 4.5);
const POSITION_MIN_HOLD = Number(process.env.POSITION_MIN_HOLD || 3);

const POSITION_LIST = [
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 }
];

function pairBanKey(pair) {
  return (Array.isArray(pair) ? pair : [])
    .map((digit) => String(digit))
    .filter((digit) => /^\d$/.test(digit))
    .sort()
    .join(",");
}

function calculatePositionKill(history, position, performance, lockApi, options) {
  const opts = options && typeof options === "object" ? options : {};
  const forceUnlock = opts.forceUnlock === true;
  const bannedKey = pairBanKey(opts.bannedPair);
  const ensureLocks = function () {
    if (forceUnlock) return {};
    if (lockApi && typeof lockApi.getStable120Locks === "function") {
      const locks = lockApi.getStable120Locks();
      return locks && typeof locks === "object" ? locks : {};
    }
    return {};
  };
  const state = {
    get stable120Locks() { return ensureLocks(); },
    set stable120Locks(value) {
      if (forceUnlock) return;
      if (lockApi && typeof lockApi.setStable120Locks === "function") lockApi.setStable120Locks(value);
    }
  };

    const safeHistory = Array.isArray(history)
      ? history
        .filter((record) => record && Array.isArray(record.numbers))
        .slice()
        .sort((a, b) => {
          const aKey = String(a && (a.period || a.uid) || "");
          const bKey = String(b && (b.period || b.uid) || "");
          return bKey.localeCompare(aKey);
        })
      : [];
    const index = Number(position && position.index);
    const posKey = String(position && position.key || "pos");
    const posLabel = String(position && position.label || posKey);
  
    function actualDigit(record) {
      const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
      return nums[index] === undefined || nums[index] === null ? "" : String(nums[index]);
    }
  
    function pairRateMultiWindow(pair, records, limits) {
      const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || 120))))];
      const maxLimit = Math.max(...lims);
      const sliced = (records || []).slice(0, maxLimit);
      const results = {};
      lims.forEach((limit) => {
        results[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0, _currentRun: 0, _leading: true };
      });
      for (let i = 0; i < sliced.length; i += 1) {
        const actual = actualDigit(sliced[i]);
        if (!actual) continue;
        const ok = pair.indexOf(actual) < 0;
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
        result.rate = result.total ? result.hit * 100 / result.total : 0;
        delete result._currentRun;
        delete result._leading;
      });
      return results;
    }
  
    function pairRate(pair, records, limit) {
      const key = Math.max(1, Number(limit || 120));
      const results = pairRateMultiWindow(pair, records, [key]);
      return results[key] || { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0 };
    }
  
    function pairBlockStats(pair) {
      const rows = safeHistory.slice(0, POSITION_BLOCK_WINDOW);
      const blocks = [];
      for (let start = 0; start < rows.length; start += POSITION_BLOCK_SIZE) {
        const blockRows = rows.slice(start, start + POSITION_BLOCK_SIZE);
        if (!blockRows.length) continue;
        const result = pairRate(pair, blockRows, blockRows.length);
        if (result.total > 0) {
          blocks.push({
            index: blocks.length + 1,
            total: result.total,
            hit: result.hit,
            fail: result.total - result.hit,
            rate: Number(result.rate.toFixed(2))
          });
        }
      }
      if (!blocks.length) {
        return {
          window: POSITION_BLOCK_WINDOW,
          blockSize: POSITION_BLOCK_SIZE,
          blockCount: 0,
          minRate: null,
          avgRate: null,
          maxRate: null,
          variance: null,
          weakBlocks: 0,
          floor: POSITION_BLOCK_FLOOR,
          blocks: []
        };
      }
      const rates = blocks.map((block) => Number(block.rate || 0));
      const avg = rates.reduce((sum, n) => sum + n, 0) / rates.length;
      return {
        window: POSITION_BLOCK_WINDOW,
        blockSize: POSITION_BLOCK_SIZE,
        blockCount: blocks.length,
        minRate: Math.min(...rates),
        avgRate: Number(avg.toFixed(2)),
        maxRate: Math.max(...rates),
        variance: Number(rateVariance(rates).toFixed(2)),
        weakBlocks: blocks.filter((block) => Number(block.rate || 0) < POSITION_BLOCK_FLOOR).length,
        floor: POSITION_BLOCK_FLOOR,
        blocks
      };
    }
  
    function scorePositionPair(pair, rLong, r300, r120, r60, r30) {
      const weighted = stableWindowScore(r30, r60, r120, r300, rLong, 3.5);
      const block = pairBlockStats(pair);
      const lower120 = wilsonLowerRate(r120.hit, r120.total);
      const lower300 = wilsonLowerRate(r300.hit, r300.total);
      const recentPenalty = Number((r30 && r30.missStreak) || 0) * 1.8;
      const weakPenalty = Number(block.weakBlocks || 0) * 2.4;
      const variancePenalty = Number(block.variance || 0) * 0.08;
      const score = (
        weighted.score * 0.54 +
        lower120 * 0.14 +
        lower300 * 0.10 +
        Number(block.minRate || 0) * 0.12 +
        Number(block.avgRate || 0) * 0.08 -
        recentPenalty -
        weakPenalty -
        variancePenalty
      );
      return {
        score: Number(score.toFixed(4)),
        stage: weighted.stage,
        block,
        lower120: Number(lower120.toFixed(2)),
        lower300: Number(lower300.toFixed(2)),
        repairFlags: {
          recentMiss: Number((r30 && r30.missStreak) || 0),
          weakBlocks: Number(block.weakBlocks || 0),
          blockMinRate: Number(block.minRate || 0),
          lower120: Number(lower120.toFixed(2))
        }
      };
    }
  
    function uniquePair(a, b) {
      const out = [];
      [a, b].forEach((digit) => {
        digit = String(digit);
        if (digit !== "" && digit !== "--" && out.indexOf(digit) < 0) out.push(digit);
      });
      DIGIT_POOL.forEach((digit) => { if (out.indexOf(digit) < 0) out.push(digit); });
      return out.slice(0, 2);
    }
  
    const candidates = [];
    const positionRateWindows = [MODEL_LONG_WINDOW, MODEL_STABLE_WINDOW, 120, 60, 30];
    for (let i = 0; i < DIGIT_POOL.length; i += 1) {
      for (let j = i + 1; j < DIGIT_POOL.length; j += 1) {
        const pair = uniquePair(DIGIT_POOL[i], DIGIT_POOL[j]);
        const rateWindows = pairRateMultiWindow(pair, safeHistory, positionRateWindows);
        const rLong = rateWindows[MODEL_LONG_WINDOW];
        const r300 = rateWindows[MODEL_STABLE_WINDOW];
        const r120 = rateWindows[120];
        const r30 = rateWindows[30];
        const r60 = rateWindows[60];
        const weighted = scorePositionPair(pair, rLong, r300, r120, r60, r30);
        candidates.push({ pair, rLong, r300, r120, r60, r30, score: weighted.score, stage: weighted.stage, block: weighted.block, lower120: weighted.lower120, lower300: weighted.lower300, repairFlags: weighted.repairFlags });
      }
    }
    candidates.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
      if ((b.block && b.block.minRate || 0) !== (a.block && a.block.minRate || 0)) return (b.block && b.block.minRate || 0) - (a.block && a.block.minRate || 0);
      if (b.r300.rate !== a.r300.rate) return b.r300.rate - a.r300.rate;
      if (b.rLong.rate !== a.rLong.rate) return b.rLong.rate - a.rLong.rate;
      if (b.r120.rate !== a.r120.rate) return b.r120.rate - a.r120.rate;
      if (b.r60.rate !== a.r60.rate) return b.r60.rate - a.r60.rate;
      return a.pair.join("").localeCompare(b.pair.join(""));
    });
    if (bannedKey) {
      const filtered = candidates.filter((item) => pairBanKey(item && item.pair) !== bannedKey);
      if (filtered.length) {
        candidates.length = 0;
        filtered.forEach((item) => candidates.push(item));
      }
    }
  
    const emptyBlock = {
      window: POSITION_BLOCK_WINDOW,
      blockSize: POSITION_BLOCK_SIZE,
      blockCount: 0,
      minRate: null,
      avgRate: null,
      maxRate: null,
      variance: null,
      weakBlocks: 0,
      floor: POSITION_BLOCK_FLOOR,
      blocks: []
    };
    const best = candidates[0] || {
      pair: ["--", "--"],
      rLong: { total: 0, hit: 0, rate: null, missStreak: 0 },
      r300: { total: 0, hit: 0, rate: null, missStreak: 0 },
      r120: { total: 0, hit: 0, rate: null, missStreak: 0 },
      r60: { total: 0, hit: 0, rate: null, missStreak: 0 },
      r30: { total: 0, hit: 0, rate: null, missStreak: 0 },
      score: 0,
      stage: stableWindowStage(0),
      block: emptyBlock,
      lower120: null,
      lower300: null,
      repairFlags: {},
      truthAvailable: false
    };
    let selected = best;
    let reason = "best-stability-window";
    const perf = getModelStat(performance, `position.${posKey}.dual`);
    const weak = perf.total >= 8 && Number(perf.rate || 0) < 78;
  
    if (typeof state !== "undefined" && state) {
      if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
    }
    const locks = (typeof state !== "undefined" && state && state.stable120Locks) ? state.stable120Locks : {};
    const lockKey = `v42:position:${posKey}`;
    const lock = locks[lockKey] || {};
    const oldPair = Array.isArray(lock.pair) ? lock.pair.map(String).slice(0, 2) : [];
  
    if (oldPair.length === 2 && !forceUnlock) {
      const oldRateWindows = pairRateMultiWindow(oldPair, safeHistory, positionRateWindows);
      const old = {
        pair: oldPair,
        rLong: oldRateWindows[MODEL_LONG_WINDOW],
        r300: oldRateWindows[MODEL_STABLE_WINDOW],
        r120: oldRateWindows[120],
        r60: oldRateWindows[60],
        r30: oldRateWindows[30]
      };
      const oldWeighted = scorePositionPair(old.pair, old.rLong, old.r300, old.r120, old.r60, old.r30);
      old.score = oldWeighted.score;
      old.stage = oldWeighted.stage;
      old.block = oldWeighted.block;
      old.lower120 = oldWeighted.lower120;
      old.lower300 = oldWeighted.lower300;
      old.repairFlags = oldWeighted.repairFlags;
      const oldHold = Number(lock.hold || 0);
      const oldBlock = old.block || emptyBlock;
      const bestClearlyBetter = best.score > old.score + POSITION_SWITCH_MARGIN;
      const dualPerf = modelPerformanceSummary(performance, `position.${posKey}.dual`);
      const dualLow = !!(dualPerf && dualPerf.low);
      const oldWarning = dualLow ||
        old.r30.missStreak >= 2 ||
        old.r30.rate < MODEL_LOW_HIT_RATE ||
        old.r60.rate < 80 ||
        old.r120.rate < 78 ||
        Number(old.lower120 || 0) < 72 ||
        Number(oldBlock.minRate || 0) < POSITION_BLOCK_FLOOR ||
        Number(oldBlock.weakBlocks || 0) > 0;
      if (!weak && !dualLow && oldHold > 0 && oldHold < POSITION_MIN_HOLD && !oldWarning && best.score <= old.score + POSITION_SWITCH_MARGIN * 1.5) {
        selected = old;
        reason = "keep-min-hold-stable-pair";
      } else if (!weak && !dualLow && !oldWarning && !bestClearlyBetter) {
        selected = old;
        reason = "keep-locked-1000-stable";
      } else if (!weak && !dualLow && old.r120.rate >= best.r120.rate - 2 && old.r30.missStreak < 2 && Number(oldBlock.minRate || 0) >= POSITION_BLOCK_FLOOR && best.score <= old.score + POSITION_SWITCH_MARGIN * 1.25) {
        selected = old;
        reason = "keep-close-stable-pair";
      } else {
        reason = weak ? "weak-position-switch-1000" : (dualLow ? "aging-dual-low-switch" : "warning-switch-stability-block");
      }
    }
  
    if (selected && selected.r30) {
      const selectedBlock = selected.block || emptyBlock;
      const selectedWarning = selected.r30.rate < MODEL_LOW_HIT_RATE ||
        selected.r60.rate < 80 ||
        selected.r120.rate < 78 ||
        selected.r30.missStreak >= 2 ||
        Number(selected.lower120 || 0) < 72 ||
        Number(selectedBlock.minRate || 0) < POSITION_BLOCK_FLOOR ||
        Number(selectedBlock.weakBlocks || 0) > 0;
      const guarded = candidates
        .filter((item) => item && item.r30 &&
          item.r30.rate >= MODEL_LOW_HIT_RATE &&
          item.r60.rate >= 80 &&
          item.r120.rate >= 78 &&
          item.r30.missStreak < 2 &&
          Number(item.lower120 || 0) >= 72 &&
          item.block &&
          Number(item.block.minRate || 0) >= POSITION_BLOCK_FLOOR &&
          Number(item.block.weakBlocks || 0) === 0)
        .sort((a, b) => {
          if (b.score !== a.score) return b.score - a.score;
          if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
          if ((b.block && b.block.minRate || 0) !== (a.block && a.block.minRate || 0)) return (b.block && b.block.minRate || 0) - (a.block && a.block.minRate || 0);
          if (b.r300.rate !== a.r300.rate) return b.r300.rate - a.r300.rate;
          if (b.r120.rate !== a.r120.rate) return b.r120.rate - a.r120.rate;
          if (b.r60.rate !== a.r60.rate) return b.r60.rate - a.r60.rate;
          if (b.r30.rate !== a.r30.rate) return b.r30.rate - a.r30.rate;
          if (b.rLong.rate !== a.rLong.rate) return b.rLong.rate - a.rLong.rate;
          return a.pair.join("").localeCompare(b.pair.join(""));
        })[0];
      if (selectedWarning && guarded && guarded.pair.join("") !== selected.pair.join("")) {
        selected = guarded;
        reason = "position-stability-guard";
      }
    }
  
    const selectedValue = selected.pair.join("");
    const oldValue = oldPair.join("");
    const latestPeriod = safeHistory[0] ? String(safeHistory[0].period || safeHistory[0].uid || "") : "";
    const samePeriod = String(lock.period || "") === latestPeriod;
    const hold = oldValue && oldValue === selectedValue
      ? (samePeriod ? Math.max(1, Number(lock.hold || 0)) : Number(lock.hold || 0) + 1)
      : 1;
    if (locks && lockKey && !forceUnlock) {
      locks[lockKey] = {
        pair: selected.pair.slice(0, 2),
        hold,
        period: latestPeriod,
        score: selected.score,
        rateLong: Number(selected.rLong.rate.toFixed(2)),
        rate300: Number(selected.r300.rate.toFixed(2)),
        rate120: Number(selected.r120.rate.toFixed(2)),
        rate60: Number(selected.r60.rate.toFixed(2)),
        rate30: Number(selected.r30.rate.toFixed(2)),
        lower120: Number((selected.lower120 || 0).toFixed(2)),
        lower300: Number((selected.lower300 || 0).toFixed(2)),
        blockMinRate: Number(((selected.block && selected.block.minRate) || 0).toFixed(2)),
        blockAvgRate: Number(((selected.block && selected.block.avgRate) || 0).toFixed(2)),
        blockWeakBlocks: Number((selected.block && selected.block.weakBlocks) || 0),
        blockVariance: Number(((selected.block && selected.block.variance) || 0).toFixed(2)),
        missStreak30: selected.r30.missStreak,
        stage: selected.stage,
        reason,
        updatedAt: (new Date()).toISOString()
      };
    }
    if (forceUnlock && bannedKey && pairBanKey(selected.pair) !== bannedKey) {
      reason = reason && String(reason).indexOf("force") >= 0 ? reason : "force-unlock-switch-pair";
    } else if (forceUnlock) {
      reason = "force-unlock-best-pair";
    }
  
    return {
      label: `${posLabel}位定位双码排除`,
      key: posKey,
      mainDigit: selected.pair[0] || "--",
      backupDigit: selected.pair[1] || "--",
      primaryDigit: selected.pair[0] || "--",
      originalBackupDigit: selected.pair[1] || "--",
      killDigits: selected.pair.filter((digit) => digit !== "--"),
      algorithm: "v42-position-stability-blocks",
      performance: modelPerformanceSummary(performance, `position.${posKey}.dual`),
      detail: candidates.slice(0, 10).map((item) => ({ pair: item.pair, rateLong: Number(item.rLong.rate.toFixed(2)), rate300: Number(item.r300.rate.toFixed(2)), rate120: Number(item.r120.rate.toFixed(2)), rate60: Number(item.r60.rate.toFixed(2)), rate30: Number(item.r30.rate.toFixed(2)), lower120: Number((item.lower120 || 0).toFixed(2)), lower300: Number((item.lower300 || 0).toFixed(2)), blockMinRate: Number(((item.block && item.block.minRate) || 0).toFixed(2)), blockAvgRate: Number(((item.block && item.block.avgRate) || 0).toFixed(2)), blockWeakBlocks: Number((item.block && item.block.weakBlocks) || 0), blockVariance: Number(((item.block && item.block.variance) || 0).toFixed(2)), missStreak30: item.r30.missStreak, score: item.score, stage: item.stage })),
      stage: selected.stage,
      stability: {
        lower120: Number((selected.lower120 || 0).toFixed(2)),
        lower300: Number((selected.lower300 || 0).toFixed(2)),
        block: selected.block || emptyBlock,
        repairFlags: selected.repairFlags || {},
        minHold: POSITION_MIN_HOLD,
        switchMargin: POSITION_SWITCH_MARGIN
      },
      reason
    };
}

module.exports = {
  POSITION_LIST,
  calculatePositionKill,
  POSITION_BLOCK_WINDOW,
  POSITION_BLOCK_SIZE,
  POSITION_BLOCK_FLOOR,
  POSITION_SWITCH_MARGIN,
  POSITION_MIN_HOLD
};
