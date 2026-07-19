"use strict";

/**
 * L1 orchestrator: runs framework snapshot algorithms after L0 base outputs
 * are already on the prediction. Modules never read sibling state.json.
 */

const autoTop6 = require("./auto-top6");
const recommend2 = require("./recommend2");
const killFramework = require("./kill-framework");
const compactAttack = require("./compact-attack");
const threeDingTwo = require("./three-ding-two");
const threeDingOne = require("./three-ding-one");
const sideStable = require("./side-stable");
const coldFramework = require("./cold-framework");
const dingKill = require("./ding-kill");
const pick5000 = require("./pick-5000");
const threeDuishu = require("./three-duishu");

const L1_ALGORITHMS = Object.freeze([
  autoTop6,
  recommend2,
  killFramework,
  compactAttack,
  threeDingTwo,
  threeDingOne,
  sideStable,
  coldFramework,
  dingKill,
  pick5000,
  threeDuishu
]);

function bindRunnerHost(host) {
  const bound = host && typeof host === "object" ? host : {};
  L1_ALGORITHMS.forEach((algo) => {
    if (typeof algo.bindHost === "function") algo.bindHost(bound);
  });
}

function computeL1(ctx) {
  const context = ctx && typeof ctx === "object" ? ctx : {};
  if (!context.prediction || typeof context.prediction !== "object") {
    throw new Error("computeL1 requires ctx.prediction");
  }
  const meta = {};
  L1_ALGORITHMS.forEach((algo) => {
    if (algo.id === "three-duishu" && typeof context.recomputeThreeDuishuRecommendation === "function") {
      context.recomputeThreeDuishuRecommendation();
    }
    const result = algo.compute(context);
    meta[algo.id] = result.meta || null;
  });
  return {
    prediction: context.prediction,
    meta
  };
}

module.exports = {
  L1_ALGORITHMS,
  bindRunnerHost,
  computeL1,
  autoTop6,
  recommend2,
  killFramework,
  compactAttack,
  threeDingTwo,
  threeDingOne,
  sideStable,
  coldFramework,
  dingKill,
  pick5000,
  threeDuishu
};
