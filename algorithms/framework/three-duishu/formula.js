"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionThreeDuishuSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildThreeDuishuSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionThreeDuishuSnapshot: ensure,
  buildThreeDuishuSnapshot: build,
  getThreeDuishuRecommendation: (...args) => engine.getThreeDuishuRecommendation(...args),
  recomputeThreeDuishuRecommendation: (...args) => engine.recomputeThreeDuishuRecommendation(...args),
  threeDuishuSnapshotIsComplete: (...args) => engine.threeDuishuSnapshotIsComplete(...args),
  resolveThreeDuishuPairs: (...args) => engine.resolveThreeDuishuPairs(...args),
  threeDuishuRecordHits: (...args) => engine.threeDuishuRecordHits(...args),
  clearScoreThreeDuishuCandidatesCache: (...args) => engine.clearScoreThreeDuishuCandidatesCache(...args)
};
