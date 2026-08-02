"use strict";

/**
 * Codex-stable L0 compute — overlays competitive position picks onto positionKills.
 * Input: ctx.history + ctx.prediction.positionKills (or ctx.positionKills).
 */

const formula = require("./formula");
const { asObject } = require("../../_shared/define_algorithm");

function bindHost() {
  // Stateless formula; host bind reserved for future locks.
}

function resolvePositionKills(ctx) {
  const context = asObject(ctx);
  if (context.positionKills && typeof context.positionKills === "object") {
    return context.positionKills;
  }
  const prediction = asObject(context.prediction);
  if (prediction.positionKills && typeof prediction.positionKills === "object") {
    return prediction.positionKills;
  }
  const detail = asObject(prediction.detail);
  if (detail.positionKills && typeof detail.positionKills === "object") {
    return detail.positionKills;
  }
  return null;
}

function compute(ctx) {
  const history = Array.isArray(ctx && ctx.history) ? ctx.history : [];
  const source = resolvePositionKills(ctx);
  if (!source || typeof source !== "object") {
    return {
      payload: {
        positionKills: {},
        codexStable: {}
      },
      meta: {
        algorithmId: "codex-stable",
        skipped: true,
        reason: "missing-position-kills",
        formula: "algorithms/codex-stable/formula.js"
      }
    };
  }

  // Clone so worker isolation does not mutate shared slices unexpectedly.
  let positionKills;
  try {
    positionKills = JSON.parse(JSON.stringify(source));
  } catch (_) {
    positionKills = Object.assign({}, source);
  }

  const codexStable = formula.computeCodexStablePositionKills(history);
  formula.applyCodexStablePositionOverlay(positionKills, history);

  // Also write onto prediction slice when present (L1-style patch path).
  if (ctx && ctx.prediction && typeof ctx.prediction === "object") {
    ctx.prediction.positionKills = positionKills;
    ctx.prediction.codexStable = codexStable;
    if (!ctx.prediction.detail || typeof ctx.prediction.detail !== "object") {
      ctx.prediction.detail = {};
    }
    ctx.prediction.detail.positionKills = positionKills;
    ctx.prediction.detail.codexStable = codexStable;
  }

  return {
    payload: {
      positionKills,
      codexStable
    },
    meta: {
      algorithmId: "codex-stable",
      historyCount: history.length,
      recommendedCount: Object.keys(codexStable).filter((key) => codexStable[key] && codexStable[key].recommended).length,
      formula: "algorithms/codex-stable/formula.js"
    }
  };
}

module.exports = {
  bindHost,
  compute,
  computeCodexStablePositionKills: formula.computeCodexStablePositionKills,
  applyCodexStablePositionOverlay: formula.applyCodexStablePositionOverlay
};
