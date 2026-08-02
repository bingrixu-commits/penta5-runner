"use strict";

/**
 * Position-kill L0 compute — uses REAL local formula (not runner host callback).
 */

const { safeHistoryRows } = require("../../_shared/history_utils");
const formula = require("./formula");

let lockApi = null;

function bindHost(nextHost) {
  // Optional: only used for lock store injection from runner.
  const host = nextHost && typeof nextHost === "object" ? nextHost : {};
  if (typeof host.getStable120Locks === "function" || typeof host.setStable120Locks === "function") {
    lockApi = {
      getStable120Locks: host.getStable120Locks,
      setStable120Locks: host.setStable120Locks
    };
  }
}

function resolveLockApi(ctx) {
  if (ctx && ctx.lockApi) return ctx.lockApi;
  if (lockApi) return lockApi;
  if (ctx && ctx.locks && typeof ctx.locks === "object") {
    return { getStable120Locks: () => ctx.locks };
  }
  return { getStable120Locks: () => ({}) };
}

function mapCalculated(calculated) {
  return {
    main: calculated.mainDigit,
    backup: calculated.backupDigit,
    recommendDigit: calculated.mainDigit,
    selectedDigit: calculated.mainDigit,
    mainAlgorithmDigit: calculated.primaryDigit,
    backupAlgorithmDigit: calculated.originalBackupDigit,
    killDigits: calculated.killDigits,
    algorithm: calculated.algorithm,
    performance: calculated.performance,
    primaryDigit: calculated.primaryDigit,
    originalBackupDigit: calculated.originalBackupDigit,
    reason: calculated.reason,
    stage: calculated.stage,
    stability: calculated.stability,
    detail: calculated.detail
  };
}

function compute(ctx) {
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const performance = ctx && ctx.modelPerformance ? ctx.modelPerformance : {};
  const api = resolveLockApi(ctx);
  const forceUnlock = !!(ctx && ctx.forceUnlock);
  const bannedPairs = ctx && ctx.bannedPairs && typeof ctx.bannedPairs === "object" ? ctx.bannedPairs : {};
  const positions = formula.POSITION_LIST.slice(0, 4);
  const positionKills = {};
  positions.forEach((position) => {
    const calculated = formula.calculatePositionKill(history, position, performance, api, {
      forceUnlock,
      bannedPair: bannedPairs[position.key] || null
    });
    positionKills[position.key] = mapCalculated(calculated);
  });
  // Codex overlay is a separate formal algo: algorithms/codex-stable
  return {
    payload: positionKills,
    meta: {
      algorithmId: "position-kills",
      historyCount: history.length,
      positions: positions.map((p) => p.key),
      formula: "algorithms/position-kills/formula.js",
      forceUnlock: forceUnlock || undefined
    }
  };
}

function computeRawEntries(ctx) {
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const performance = ctx && ctx.modelPerformance ? ctx.modelPerformance : {};
  const api = resolveLockApi(ctx);
  const positions = formula.POSITION_LIST.slice(0, 4);
  const detailPositionKills = {};
  positions.forEach((position) => {
    detailPositionKills[position.key] = formula.calculatePositionKill(history, position, performance, api);
  });
  return {
    payload: detailPositionKills,
    meta: {
      algorithmId: "position-kills",
      mode: "raw-entries",
      historyCount: history.length,
      formula: "algorithms/position-kills/formula.js"
    }
  };
}

module.exports = {
  bindHost,
  compute,
  computeRawEntries,
  calculatePositionKill: formula.calculatePositionKill,
  POSITION_LIST: formula.POSITION_LIST
};
