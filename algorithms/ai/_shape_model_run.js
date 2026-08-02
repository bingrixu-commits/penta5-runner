"use strict";
/**
 * 轻量形态模型：只算避开形态，不挂 pm2。
 * shapeKey: shapeSize | shapeParity
 */
const fs = require("fs");
const path = require("path");
const http = require("http");
const formula = require("../kill/four-pos-exclude/formula");

const REPO_ROOT = path.join(__dirname, "..", "..");

function httpGetJson(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs || 12000 }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
        catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

function asHistory(rows) {
  return (Array.isArray(rows) ? rows : [])
    .filter((r) => r && Array.isArray(r.numbers) && r.numbers.length >= 4)
    .map((r) => ({
      period: r.period || r.uid || "",
      numbers: r.numbers.slice(0, 5).map(String),
      opentime: r.opentime || ""
    }));
}

function pickTopForKey(history, shapeKey, topN) {
  const want = Math.max(1, Number(topN || 3));
  // reuse full candidate scoring then filter
  const all = formula.pickStableShapes
    ? null
    : null;
  // local: call internal via buildSnapshot shapeItems then filter is wrong (mixed).
  // Use formula exports if available; else duplicate thin pick.
  const snap = formula.buildSnapshot(history);
  // buildSnapshot picks mixed top2; we need dedicated ranking per key.
  // Fall back: score all codes for one key using formula internals if exported.
  return rankShapeKey(history, shapeKey, want);
}

function rankShapeKey(history, shapeKey, want) {
  // Access via evaluating all candidates using pickStableShapes with high count then filter —
  // pickStableShapes mixes types. Implement with formula's exported helpers if any.
  const exports = formula;
  // Monkey: temporarily use buildSnapshot history + private isn't exported.
  // Require file and use only public API: pickStableShapes returns mixed.
  // So implement scoring by importing formula_kit path through a tiny inline using buildSnapshot's approach —
  // Read pickStableShapes logic: we copy minimal call by requiring and using enumerate via buildSnapshot shape pool.

  // Practical approach: get many candidates by calling pickStableShapes with count=16 (won't work - only 2).
  // Direct require of formula and use undocumented: check exports again.
  const keys = Object.keys(exports);
  if (typeof exports.pickStableShapes === "function") {
    // still mixed
  }
  return rankWithFormulaFile(history, shapeKey, want);
}

function rankWithFormulaFile(history, shapeKey, want) {
  // Inline using formula.buildSnapshot is insufficient. Use child eval of formula internals:
  const pathToFormula = require.resolve("../kill/four-pos-exclude/formula");
  delete require.cache[pathToFormula];
  const f = require(pathToFormula);
  // Patch: call pickStableShapes-like by reading source — instead duplicate score loop here using f if functions exported.
  // Check module.exports at end of formula.js
  throw new Error("use dedicated ranker");
}

module.exports = { httpGetJson, asHistory, REPO_ROOT };
