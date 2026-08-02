"use strict";

/**
 * four-pos-exclude L0 compute — local formula only, RDS history input.
 */

const { safeHistoryRows } = require("../../_shared/history_utils");
const formula = require("./formula");

function bindHost() {
  // No sibling locks / filters required; history-only algorithm.
}

function compute(ctx) {
  const history = safeHistoryRows(ctx && ctx.history, 4);
  const snapshot = formula.buildSnapshot(history);
  return {
    payload: snapshot,
    meta: {
      algorithmId: "four-pos-exclude",
      historyCount: history.length,
      itemCount: Array.isArray(snapshot.items) ? snapshot.items.length : 0,
      formula: "algorithms/four-pos-exclude/formula.js",
      source: "rds-shared-read-only"
    }
  };
}

module.exports = {
  bindHost,
  compute,
  buildSnapshot: formula.buildSnapshot,
  snapshotIsComplete: formula.snapshotIsComplete,
  positionsComplete: formula.positionsComplete,
  shapesComplete: formula.shapesComplete,
  sealSnapshot: formula.sealSnapshot,
  evaluateSnapshot: formula.evaluateSnapshot,
  evaluatePositions: formula.evaluatePositions,
  evaluateShapes: formula.evaluateShapes,
  VERSION: formula.VERSION,
  LABEL: formula.LABEL,
  POSITION_LIST: formula.POSITION_LIST,
  SHAPE_COUNT: formula.SHAPE_COUNT
};
