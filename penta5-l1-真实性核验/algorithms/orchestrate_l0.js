"use strict";

/**
 * L0 orchestrator: runs independent algorithm compute modules in order.
 * Only shared history (+ modelPerformance) is input; modules must not read
 * sibling algorithm-projects state.json.
 */

const positionKillsAlgo = require("./position-kills");
const heweiAlgo = require("./hewei");
const stableMathAlgo = require("./stable-math");
const extraKillsAlgo = require("./extra-kills");

const L0_ALGORITHMS = Object.freeze([positionKillsAlgo, heweiAlgo, stableMathAlgo, extraKillsAlgo]);

function bindRunnerHost(host) {
  const next = host && typeof host === "object" ? host : {};
  L0_ALGORITHMS.forEach((algo) => {
    if (typeof algo.bindHost === "function") algo.bindHost(next);
  });
}

function computeL0(ctx) {
  const context = ctx && typeof ctx === "object" ? ctx : {};
  // Ensure L0 packages use real local formulas; lock/cache accessors come from prior bindHost.
  const heweiResult = heweiAlgo.compute(context);
  const positionResult = positionKillsAlgo.compute(context);
  const stableResult = stableMathAlgo.compute(context);
  const extraResult = extraKillsAlgo.compute(Object.assign({}, context, {
    getState: context.getState,
    getStableMathExcludeDigitSet: context.getStableMathExcludeDigitSet
  }));
  return {
    hewei: heweiResult.payload,
    positionKills: positionResult.payload,
    stableMathFilter: stableResult.payload,
    extraKills: extraResult.payload,
    meta: {
      hewei: heweiResult.meta,
      positionKills: positionResult.meta,
      stableMath: stableResult.meta,
      extraKills: extraResult.meta
    }
  };
}

module.exports = {
  L0_ALGORITHMS,
  bindRunnerHost,
  computeL0,
  positionKillsAlgo,
  heweiAlgo,
  stableMathAlgo,
  extraKillsAlgo
};
