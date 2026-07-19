"use strict";

const { safeHistoryRows } = require("../_shared/history_utils");
const formula = require("./formula");
const heweiFormula = require("../hewei/formula");

function bindHost(nextHost) {
  const host = nextHost && typeof nextHost === "object" ? nextHost : {};
  formula.bindRuntime({
    getState: host.getState,
    getStableMathExcludeDigitSet: host.getStableMathExcludeDigitSet
  });
  if (typeof host.getStable120Locks === "function" || typeof host.getCachedFilter === "function") {
    heweiFormula.bindRuntime({
      getStable120Locks: host.getStable120Locks,
      setStable120Locks: host.setStable120Locks,
      getStableMathFilter: host.getCachedFilter,
      readCachedStableMathFilter: host.readCachedFilter
    });
  }
}

function compute(ctx) {
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const performance = ctx && ctx.modelPerformance ? ctx.modelPerformance : {};
  if (ctx && typeof ctx.getState === "function") {
    formula.bindRuntime({
      getState: ctx.getState,
      getStableMathExcludeDigitSet: () => heweiFormula.getStableMathExcludeDigitSet()
    });
  }
  const payload = formula.calculateExtraAvoidKills(history, performance);
  return {
    payload,
    meta: {
      algorithmId: "extra-kills",
      historyCount: history.length,
      keyCount: payload && typeof payload === "object" ? Object.keys(payload).length : 0,
      formula: "algorithms/extra-kills/formula.js"
    }
  };
}

module.exports = {
  bindHost,
  compute,
  calculateExtraAvoidKills: (...args) => formula.calculateExtraAvoidKills(...args)
};
