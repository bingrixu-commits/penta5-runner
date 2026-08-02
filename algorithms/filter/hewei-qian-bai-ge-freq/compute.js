"use strict";

const { safeHistoryRows } = require("../../_shared/history_utils");
const formula = require("./formula");

function bindHost() {}

function compute(ctx) {
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const performance = ctx && ctx.modelPerformance ? ctx.modelPerformance : {};
  const result = formula.calculate(history, performance);
  return {
    payload: result,
    meta: {
      algorithmId: "hewei-qian-bai-ge-freq",
      historyCount: history.length,
      formula: "algorithms/hewei-qian-bai-ge-freq/formula.js"
    }
  };
}

module.exports = { bindHost, compute, calculate: formula.calculate };
