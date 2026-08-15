"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
async function ensure(prediction, history) {
  return engine.ensurePredictionAutoTop6Snapshot(prediction, history);
}
async function build(prediction, history, options) {
  return engine.buildAutoTop6Snapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensureAsync: ensure,
  ensurePredictionAutoTop6Snapshot: ensure,
  buildAutoTop6Snapshot: build
};
