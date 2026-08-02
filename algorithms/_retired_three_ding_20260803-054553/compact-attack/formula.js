"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionCompactAttackSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildCompactAttackSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionCompactAttackSnapshot: ensure,
  buildCompactAttackSnapshot: build
};
