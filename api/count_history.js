"use strict";
const fs = require("fs");
const paths = [
  "/root/penta5-runner/data/state.json",
  "/root/penta5-runner/data/backups/state_full_sync_20260714_021512.json",
  "/root/penta5-runner/data/backups/state_before_rds_import_20260712_203239.json",
  "/root/penta5-runner/data/backups/state_v145_20260712_190720.json",
  "/root/penta5-runner/data/backups/state_20260712_061232.json",
];
function countHistory(file) {
  const raw = fs.readFileSync(file, "utf8");
  const state = JSON.parse(raw);
  const h = Array.isArray(state.history) ? state.history : [];
  const first = h[0] || {};
  const last = h[h.length - 1] || {};
  return {
    path: file,
    size: fs.statSync(file).size,
    history: h.length,
    newest: first.period || null,
    oldest: last.period || null,
    predictions: Array.isArray(state.predictions) ? state.predictions.length : 0,
  };
}
const out = [];
for (const p of paths) {
  if (!fs.existsSync(p)) {
    out.push({ path: p, missing: true });
    continue;
  }
  out.push(countHistory(p));
}
console.log(JSON.stringify(out, null, 2));
