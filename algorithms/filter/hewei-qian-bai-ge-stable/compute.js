"use strict";

const { safeHistoryRows } = require("../../_shared/history_utils");
const formula = require("./formula");

function bindHost(nextHost) {
  formula.bindRuntime(nextHost && typeof nextHost === "object" ? nextHost : {});
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
  const result = formula.calculate(history, performance);
  return {
    payload: result,
    meta: {
      algorithmId: "hewei-qian-bai-ge-stable",
      historyCount: history.length,
      formula: "algorithms/hewei-qian-bai-ge-stable/formula.js"
    }
  };
}

module.exports = { bindHost, compute, calculate: formula.calculate };
