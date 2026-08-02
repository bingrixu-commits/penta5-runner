"use strict";

/**
 * Hewei L0 compute — REAL local formula (algorithms/hewei/formula.js).
 */

const { safeHistoryRows } = require("../../_shared/history_utils");
const formula = require("./formula");

function bindHost(nextHost) {
  const host = nextHost && typeof nextHost === "object" ? nextHost : {};
  formula.bindRuntime({
    getStable120Locks: host.getStable120Locks,
    setStable120Locks: host.setStable120Locks,
    getStableMathFilter: host.getCachedFilter || host.getStableMathFilter,
    readCachedStableMathFilter: host.readCachedFilter || host.readCachedStableMathFilter
  });
}

function compute(ctx) {
  if (ctx && ctx.lockApi) {
    formula.bindRuntime({
      getStable120Locks: ctx.lockApi.getStable120Locks,
      setStable120Locks: ctx.lockApi.setStable120Locks,
      getStableMathFilter: ctx.getStableMathFilter,
      readCachedStableMathFilter: ctx.readCachedStableMathFilter
    });
  }
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const performance = ctx && ctx.modelPerformance ? ctx.modelPerformance : {};
  const totalHeweiKill = formula.calculateTotalHeweiKill(history, performance);
  const threeFourKill = formula.calculateThreeFourHeweiKillDigit(history, performance);
  const independentHeweiKills = formula.calculateIndependentHeweiKills(history, performance);
  const heweiReconcile = formula.reconcileHeweiKillBundle({
    totalHeweiKill,
    threeFourKill,
    independentHeweiKills
  });
  return {
    payload: {
      totalHeweiKill: totalHeweiKill.killHewei,
      totalHeweiBackupDigit: totalHeweiKill.backupDigit,
      threeFourHeweiKillDigit: threeFourKill.killDigit,
      threeFourHeweiBackupDigit: threeFourKill.backupDigit,
      independentHeweiKills,
      detail: {
        totalHeweiKill,
        threeFourHeweiKill: threeFourKill,
        independentHeweiKills,
        heweiReconcileNotes: Array.isArray(heweiReconcile && heweiReconcile.notes)
          ? heweiReconcile.notes
          : []
      }
    },
    meta: {
      algorithmId: "hewei",
      historyCount: history.length,
      reconcileNotes: Array.isArray(heweiReconcile && heweiReconcile.notes) ? heweiReconcile.notes : [],
      formula: "algorithms/hewei/formula.js"
    }
  };
}

module.exports = {
  bindHost,
  compute,
  calculateTotalHeweiKill: (...args) => formula.calculateTotalHeweiKill(...args),
  calculateThreeFourHeweiKillDigit: (...args) => formula.calculateThreeFourHeweiKillDigit(...args),
  calculateIndependentHeweiKills: (...args) => formula.calculateIndependentHeweiKills(...args),
  reconcileHeweiKillBundle: (...args) => formula.reconcileHeweiKillBundle(...args)
};
