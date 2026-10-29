"use strict";

// Read-only confirmation. An importer exit 0 is not proof that its immutable
// merge could store an incoming original over an older incomplete sealed row.
const fs = require("fs");
const crypto = require("crypto");
const {predictionFromRow, predictionId} = require("./prediction_merge");
const KEYS = ["fourPosExclude", "autoTop6", "recommend2", "killFramework", "fourDingFramework", "threeDuishu"];

function canonical(value) {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value && typeof value === "object") return "{" + Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonical(value[key])).join(",") + "}";
  return JSON.stringify(value);
}

function expectedPredictions(state) {
  const meta = state && state.rdsMirrorOutbox;
  if (!meta || meta.version !== 1 || !Array.isArray(meta.ids) || meta.ids.length > 100
      || new Set(meta.ids).size !== meta.ids.length) throw new Error("invalid-mirror-outbox-expectations");
  const expected = new Map();
  for (const rawId of meta.ids) {
    const id = predictionId(rawId);
    if (typeof rawId !== "string" || rawId !== id) throw new Error("invalid-mirror-outbox-identity");
    const matches = (Array.isArray(state.predictions) ? state.predictions : []).filter(prediction => prediction && prediction.id === id);
    if (matches.length !== 1) throw new Error("ambiguous-mirror-outbox-prediction");
    const prediction = matches[0];
    if (prediction.allSnapshotsSealed !== true || !prediction.snapshotsSealedAt
        || KEYS.some(key => !prediction[key] || prediction[key].sealed !== true
          || !prediction[key].sealHash || prediction[key].reconstructed === true)) throw new Error("incomplete-mirror-outbox-expectation");
    expected.set(id, prediction);
  }
  return expected;
}

function assertSealedMatch(expected, stored) {
  for (const key of ["id", "baseUid", "basePeriod", "generatedAt", "snapshotsSealedAt", "snapshotsSealedBaseUid", "allSnapshotsSealed"]) {
    if (canonical(expected[key]) !== canonical(stored[key])) throw new Error("mirror-sealed-identity-mismatch:" + key);
  }
  for (const key of KEYS) {
    if (!stored[key] || stored[key].sealed !== true || !stored[key].sealHash
        || canonical(expected[key]) !== canonical(stored[key])) throw new Error("mirror-sealed-content-mismatch:" + key);
  }
}

function verifyRows(expected, rows) {
  const seen = new Set();
  for (const row of rows) {
    const id = String(row.prediction_id);
    if (!expected.has(id) || seen.has(id)) throw new Error("mirror-stored-prediction-identity-conflict");
    assertSealedMatch(expected.get(id), predictionFromRow(row));
    seen.add(id);
  }
  if (seen.size !== expected.size) throw new Error("mirror-stored-prediction-missing");
  return {ok: true, verified_outbox_count: seen.size};
}

async function verifyState(state, query) {
  const expected = expectedPredictions(state);
  if (!expected.size) return {ok: true, verified_outbox_count: 0};
  const ids = [...expected.keys()];
  const [rows] = await query(`SELECT prediction_id, base_uid, base_period, evaluated, counter_applied, raw_json
    FROM model_predictions WHERE prediction_id IN (${ids.map(() => "?").join(",")})`, ids);
  return verifyRows(expected, rows);
}

function expectedEntries(state) {
  const expected = expectedPredictions(state), entries = state.rdsMirrorOutbox.entries;
  if (!Array.isArray(entries) || entries.length !== expected.size) throw new Error("invalid-mirror-outbox-entry-expectations");
  const hashes = new Map();
  for (const entry of entries) {
    if (!entry || !expected.has(entry.id) || hashes.has(entry.id) || !/^[a-f0-9]{64}$/.test(entry.sha256 || "")) throw new Error("invalid-mirror-outbox-entry-expectations");
    hashes.set(entry.id, entry.sha256);
  }
  return {expected, hashes};
}

function verifyRowsPartial(state, rows) {
  const {expected, hashes} = expectedEntries(state), stored = new Map();
  for (const row of rows) {
    const id = String(row.prediction_id);
    if (!expected.has(id) || stored.has(id)) throw new Error("mirror-stored-prediction-identity-conflict");
    stored.set(id, row);
  }
  const verified = [], conflicted = [];
  for (const [id, original] of expected) {
    const entry = {id, sha256: hashes.get(id)};
    try {
      if (!stored.has(id)) throw new Error("mirror-stored-prediction-missing");
      assertSealedMatch(original, predictionFromRow(stored.get(id)));
      verified.push(entry);
    } catch (error) {
      const reason = String(error && error.message || "mirror-stored-prediction-invalid");
      // These operations are local validation only; query/SQL failures occur
      // outside this loop and must remain whole-attempt failures.
      conflicted.push({...entry, reason: /^(mirror-|Prediction |Invalid stored )/.test(reason)
        ? reason.slice(0, 120) : "mirror-stored-prediction-invalid"});
    }
  }
  return {ok: true, verified_outbox_count: verified.length,
    verified_outbox_entries: verified, conflicted_outbox_entries: conflicted};
}

async function verifyStatePartial(state, query) {
  const {expected} = expectedEntries(state);
  if (!expected.size) return verifyRowsPartial(state, []);
  const ids = [...expected.keys()];
  const [rows] = await query(`SELECT prediction_id, base_uid, base_period, evaluated, counter_applied, raw_json
    FROM model_predictions WHERE prediction_id IN (${ids.map(() => "?").join(",")})`, ids);
  return verifyRowsPartial(state, rows);
}

async function main() {
  const value = flag => { const i = process.argv.indexOf(flag); return i < 0 ? "" : process.argv[i + 1] || ""; };
  const file = value("--file"), expectedSha = value("--expected-freeze-sha");
  if (!file || !/^[a-f0-9]{64}$/.test(expectedSha)) throw new Error("invalid-mirror-verification-request");
  const bytes = fs.readFileSync(file);
  if (crypto.createHash("sha256").update(bytes).digest("hex") !== expectedSha) throw new Error("mirror-verification-freeze-hash-mismatch");
  const state = JSON.parse(bytes.toString("utf8"));
  const partial = process.argv.includes("--partial-confirmation");
  const expected = expectedPredictions(state);
  if (!expected.size) { console.log(JSON.stringify(partial ? verifyRowsPartial(state, []) : {ok: true, verified_outbox_count: 0})); return; }
  const pool = require("./db").createPool();
  try { console.log(JSON.stringify(await (partial ? verifyStatePartial : verifyState)(state, (...args) => pool.query(...args)))); }
  finally { await pool.end(); }
}

if (require.main === module) main().catch(error => {
  console.error("Mirror sealed evidence verification failed: " + String(error && error.message || "unknown"));
  process.exitCode = 1;
});
module.exports = {canonical, expectedPredictions, assertSealedMatch, verifyRows, verifyState,
  expectedEntries, verifyRowsPartial, verifyStatePartial};
