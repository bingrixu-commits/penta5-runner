"use strict";

const { safeHistoryRows } = require("../_shared/history_utils");
const formula = require("./formula");

function bindHost() {
  // history-only sidecar
}

function compute(ctx) {
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const snapshot = formula.buildSnapshot(history);
  return {
    payload: snapshot,
    meta: {
      algorithmId: "claude-dual-pos-hewei",
      historyCount: history.length,
      complete: !!snapshot.complete,
      tipText: snapshot.tipText || "",
      jointEvalPeriods: snapshot.joint && snapshot.joint.evalPeriods || 0,
      formula: "algorithms/claude-dual-pos-hewei/formula.js",
      source: "rds-shared-read-only"
    }
  };
}

module.exports = {
  bindHost,
  compute,
  buildSnapshot: formula.buildSnapshot,
  snapshotIsComplete: formula.snapshotIsComplete,
  evaluateSnapshot: formula.evaluateSnapshot,
  VERSION: formula.VERSION,
  LABEL: formula.LABEL,
  KIND: formula.KIND,
  POSITION_LIST: formula.POSITION_LIST
};
