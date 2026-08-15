"use strict";

const { safeHistoryRows } = require("../_shared/history_utils");
const formula = require("./formula");

function bindHost() {
  // history-only; no sibling locks
}

function compute(ctx) {
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const snapshot = formula.buildSnapshot(history);
  return {
    payload: snapshot,
    meta: {
      algorithmId: "flex-dual-pos-hewei",
      historyCount: history.length,
      itemCount: Array.isArray(snapshot.items) ? snapshot.items.length : 0,
      regime: snapshot.regime || null,
      complete: !!snapshot.complete,
      formula: "algorithms/flex-dual-pos-hewei/formula.js",
      source: "rds-shared-read-only"
    }
  };
}

module.exports = {
  bindHost,
  compute,
  buildSnapshot: formula.buildSnapshot,
  snapshotIsComplete: formula.snapshotIsComplete,
  VERSION: formula.VERSION,
  LABEL: formula.LABEL,
  KIND: formula.KIND,
  POSITION_LIST: formula.POSITION_LIST
};
