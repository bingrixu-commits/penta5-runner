"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionAutoTop6Snapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildAutoTop6Snapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionAutoTop6Snapshot: ensure,
  buildAutoTop6Snapshot: build
};
