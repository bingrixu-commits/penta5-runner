"use strict";
/**
 * Lazy facade - primary path is each algorithm's own engine.js.
 * Three-ding frameworks retired: compact-attack / three-ding-* exports are no-ops.
 */

const path = require("path");
const ALGO_ROOT = path.join(__dirname, "..", "..");

const loaders = {
  "three-duishu": () => require(path.join(ALGO_ROOT, "three-duishu", "engine.js")),
  "auto-top6": () => require(path.join(ALGO_ROOT, "auto-top6", "engine.js")),
  recommend2: () => require(path.join(ALGO_ROOT, "recommend2", "engine.js")),
  "kill-framework": () => require(path.join(ALGO_ROOT, "kill-framework", "engine.js")),
  "four-ding-framework": () => require(path.join(ALGO_ROOT, "four-ding-framework", "engine.js"))
};


const cache = {};
let pendingRuntime = null;

function eng(algo) {
  if (!cache[algo]) {
    cache[algo] = loaders[algo]();
    if (pendingRuntime && typeof cache[algo].bindRuntime === "function") {
      cache[algo].bindRuntime(pendingRuntime);
    }
  }
  return cache[algo];
}

function bindRuntime(runtime) {
  pendingRuntime = runtime || null;
  Object.keys(cache).forEach((algo) => {
    if (typeof cache[algo].bindRuntime === "function") cache[algo].bindRuntime(pendingRuntime);
  });
}

function wrap(algo, name) {
  return function () {
    return eng(algo)[name].apply(null, arguments);
  };
}

function retired() { return null; }

module.exports = {
  bindRuntime,
  buildAutoTop6Snapshot: wrap("auto-top6", "buildAutoTop6Snapshot"),
  ensurePredictionAutoTop6Snapshot: wrap("auto-top6", "ensurePredictionAutoTop6Snapshot"),
  autoTop6CandidateItems: wrap("auto-top6", "autoTop6CandidateItems"),
  buildRecommend2Snapshot: wrap("recommend2", "buildRecommend2Snapshot"),
  ensurePredictionRecommend2Snapshot: wrap("recommend2", "ensurePredictionRecommend2Snapshot"),
  buildKillFrameworkSnapshot: wrap("kill-framework", "buildKillFrameworkSnapshot"),
  ensurePredictionKillFrameworkSnapshot: wrap("kill-framework", "ensurePredictionKillFrameworkSnapshot"),
  buildFourDingFrameworkSnapshot: wrap("four-ding-framework", "buildFourDingFrameworkSnapshot"),
  ensurePredictionFourDingFrameworkSnapshot: wrap("four-ding-framework", "ensurePredictionFourDingFrameworkSnapshot"),
  fourDingFrameworkSnapshotIsComplete: wrap("four-ding-framework", "fourDingFrameworkSnapshotIsComplete"),
  buildCompactAttackSnapshot: retired,
  ensurePredictionCompactAttackSnapshot: retired,
  buildThreeDingTwoSnapshot: retired,
  ensurePredictionThreeDingTwoSnapshot: retired,
  buildThreeDingOneSnapshot: retired,
  ensurePredictionThreeDingOneSnapshot: retired,
  buildThreeDuishuSnapshot: wrap("three-duishu", "buildThreeDuishuSnapshot"),
  ensurePredictionThreeDuishuSnapshot: wrap("three-duishu", "ensurePredictionThreeDuishuSnapshot"),
  recomputeThreeDuishuRecommendation: wrap("three-duishu", "recomputeThreeDuishuRecommendation"),
  getThreeDuishuRecommendation: wrap("three-duishu", "getThreeDuishuRecommendation"),
  threeDuishuSnapshotIsComplete: wrap("three-duishu", "threeDuishuSnapshotIsComplete"),
  resolveThreeDuishuPairs: wrap("three-duishu", "resolveThreeDuishuPairs"),
  threeDuishuRecordHits: wrap("three-duishu", "threeDuishuRecordHits"),
  clearScoreThreeDuishuCandidatesCache: wrap("three-duishu", "clearScoreThreeDuishuCandidatesCache")
};
