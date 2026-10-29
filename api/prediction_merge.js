"use strict";

// Pure persistence merge. Sealed evidence is never reconstructed or re-signed here.
function isObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function predictionId(value) {
  if ((typeof value !== "string" && typeof value !== "number") || !String(value).trim()) {
    throw new Error("Prediction id is required");
  }
  const id = String(value);
  if (id.length > 190) throw new Error("Prediction id exceeds persistence limit");
  return id;
}

function parseStoredObject(value, label) {
  const parsed = typeof value === "string" || Buffer.isBuffer(value) ? JSON.parse(String(value)) : value;
  if (!isObject(parsed)) throw new Error("Invalid stored " + label + "; refusing overwrite");
  return parsed;
}

function assertIdentity(existing, incoming) {
  for (const key of ["id", "baseUid", "basePeriod"]) {
    const left = existing && existing[key];
    const right = incoming && incoming[key];
    if (left != null && left !== "" && right != null && right !== "" && String(left) !== String(right)) {
      throw new Error("Prediction identity conflict: " + key);
    }
  }
}

function flag(value) {
  return value === true || value === 1 || value === "1";
}

function predictionFromRow(row) {
  if (!row) return null;
  const raw = parseStoredObject(row.raw_json, "prediction");
  const identity = {id: row.prediction_id, baseUid: row.base_uid, basePeriod: row.base_period};
  assertIdentity(identity, raw);
  const result = copy(raw);
  for (const [key, value] of Object.entries(identity)) {
    if (result[key] == null || result[key] === "") result[key] = value;
  }
  result.evaluated = flag(raw.evaluated) || flag(row.evaluated);
  result.counterApplied = flag(raw.counterApplied) || flag(row.counter_applied);
  return result;
}

function containsSealedSnapshot(value) {
  if (!value || typeof value !== "object") return false;
  if (isObject(value) && value.sealed === true) return true;
  return Object.values(value).some(containsSealedSnapshot);
}

function hasSealedEvidence(prediction) {
  if (!isObject(prediction)) return false;
  if (prediction.allSnapshotsSealed === true || prediction.snapshotsSealedAt) return true;
  return containsSealedSnapshot(prediction);
}

function hasEvaluation(value) {
  return isObject(value) && Object.keys(value).length > 0;
}

function mergePartialEvidence(existing, incoming) {
  if (isObject(existing) && existing.sealed === true) return copy(existing);
  if (!isObject(existing) || !isObject(incoming)) {
    // Replacing a container or array must not delete signed descendants.
    return copy(containsSealedSnapshot(existing) ? existing : incoming);
  }
  const result = copy(existing);
  for (const [key, value] of Object.entries(incoming)) {
    result[key] = key in existing ? mergePartialEvidence(existing[key], value) : copy(value);
  }
  return result;
}

const FRAMEWORK_KEYS = ["fourPosExclude", "autoTop6", "recommend2", "killFramework", "fourDingFramework", "threeDuishu"];

function mergePrediction(existing, incoming, storedEvaluation) {
  if (!isObject(incoming)) throw new Error("Invalid incoming prediction");
  const id = predictionId(incoming.id);
  assertIdentity(existing, incoming);
  const aggregateSealed = existing && (existing.allSnapshotsSealed === true || existing.snapshotsSealedAt);
  if (existing && !aggregateSealed && hasSealedEvidence(existing)) {
    for (const key of ["generatedAt", "modelVersion"]) {
      if (existing[key] && incoming[key] && String(existing[key]) !== String(incoming[key])) throw new Error("Prediction sealed identity conflict: " + key);
    }
  }
  const merged = aggregateSealed ? copy(existing)
    : existing && hasSealedEvidence(existing) ? mergePartialEvidence(existing, incoming)
      : copy(Object.assign({}, existing || {}, incoming));
  merged.id = id;
  // A slim replay may omit identity, but can never clear a known one.
  for (const key of ["baseUid", "basePeriod"]) {
    if ((merged[key] == null || merged[key] === "") && existing && existing[key] != null) merged[key] = existing[key];
  }
  merged.evaluated = flag(existing && existing.evaluated) || flag(incoming.evaluated);
  merged.counterApplied = flag(existing && existing.counterApplied) || flag(incoming.counterApplied);
  // The evaluation table is the first persisted settlement. Never replace it
  // with a later slim/replayed/recomputed evaluation from the live process.
  const evaluation = hasEvaluation(storedEvaluation) ? storedEvaluation
    : hasEvaluation(existing && existing.evaluation) ? existing.evaluation
      : hasEvaluation(incoming.evaluation) ? incoming.evaluation : null;
  if (evaluation) merged.evaluation = copy(evaluation);
  else if (merged.evaluation !== undefined) delete merged.evaluation;
  if (hasSealedEvidence(merged)) {
    const missing = FRAMEWORK_KEYS.filter((key) => !isObject(merged[key]) || merged[key].sealed !== true || !merged[key].sealHash);
    if (missing.length) {
      // Describe pre-existing loss; never fill missing signed content from a replay.
      merged.persistenceEvidenceWarning = {version: 1, status: "incomplete-sealed-snapshot-fields", missingFrameworks: missing, reconstructed: false};
    } else if (merged.persistenceEvidenceWarning && merged.persistenceEvidenceWarning.version === 1) {
      delete merged.persistenceEvidenceWarning;
    }
  }
  return merged;
}

module.exports = {predictionId, parseStoredObject, predictionFromRow, mergePrediction, hasSealedEvidence, hasEvaluation};
