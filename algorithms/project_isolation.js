"use strict";

/**
 * Per-algorithm state isolation for the Penta5 runner.
 *
 * The shared RDS draw history remains the only source of official input.  Each
 * algorithm writes its derived prediction, evaluation, and health to its own
 * state file under data/algorithm-projects/<id>/state.json.  No algorithm
 * reads another algorithm's state file as an input.
 */

const fs = require("fs");
const path = require("path");

// Independent algorithm packages (L0 + L1). projects/* remain compatibility re-exports.
const PROJECTS = Object.freeze([
  require("./projects/core_runner"),
  require("./position-kills"),
  require("./hewei"),
  require("./extra-kills"),
  require("./stable-math"),
  require("./auto-top6"),
  require("./recommend2"),
  require("./kill-framework"),
  require("./compact-attack"),
  require("./three-ding-two"),
  require("./three-ding-one"),
  require("./side-stable"),
  require("./cold-framework"),
  require("./ding-kill"),
  require("./pick-5000"),
  require("./three-duishu")
]);

const PROJECT_BY_ID = new Map(PROJECTS.map((project) => [project.id, project]));
const SNAPSHOT_PROJECT_IDS = Object.freeze({
  autoTop6: "auto-top6",
  recommend2: "recommend2",
  killFramework: "kill-framework",
  compactAttack: "compact-attack",
  threeDingTwo: "three-ding-two",
  threeDingOne: "three-ding-one",
  sideStable: "side-stable",
  coldFramework: "cold-framework",
  dingKill: "ding-kill",
  pick5000: "pick-5000",
  threeDuishu: "three-duishu"
});

function isoNow() {
  return new Date().toISOString();
}

function isObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function safeString(value, fallback) {
  const text = value === undefined || value === null ? "" : String(value).trim();
  return text || String(fallback || "");
}

function cloneCompact(value, depth) {
  const level = Number.isFinite(Number(depth)) ? Number(depth) : 0;
  if (value === null || value === undefined || typeof value !== "object") return value;
  if (level > 7) return "[truncated]";
  if (Array.isArray(value)) {
    const limit = 160;
    const out = value.slice(0, limit).map((item) => cloneCompact(item, level + 1));
    if (value.length > limit) out.push({ truncatedCount: value.length - limit });
    return out;
  }
  const out = {};
  Object.keys(value).sort().forEach((key) => {
    if (key === "rawJson" || key === "choiceCompared" || key === "details") return;
    if (key === "codes" && Array.isArray(value[key])) {
      out.codeCount = Number(value.codeCount || value[key].length) || value[key].length;
      out.codesOmitted = true;
      return;
    }
    out[key] = cloneCompact(value[key], level + 1);
  });
  return out;
}

function readJson(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    return isObject(parsed) ? parsed : fallback;
  } catch (_) {
    return fallback;
  }
}

function writeJsonAtomic(file, payload) {
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = path.join(dir, `.${path.basename(file)}.${process.pid}.${Date.now()}.tmp`);
  try {
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), "utf8");
    fs.renameSync(tmp, file);
  } catch (error) {
    try {
      if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
    } catch (_) {}
    throw error;
  }
}

function projectStateFile(dataDir, projectId) {
  return path.join(dataDir, "algorithm-projects", projectId, "state.json");
}

function projectIdForIssue(issue) {
  const key = safeString(issue && issue.key);
  if (key.startsWith("prediction.") || key === "watchdog") return "core-runner";
  if (key.startsWith("position.")) return "position-kills";
  if (key.startsWith("hewei.")) return "hewei";
  if (key.startsWith("extra.")) return "extra-kills";
  if (key.startsWith("stableMath")) return "stable-math";
  if (key.startsWith("snapshot.")) {
    const name = key.split(".")[1] || "";
    return SNAPSHOT_PROJECT_IDS[name] || "core-runner";
  }
  return "core-runner";
}

function projectRecord(projectId, prediction) {
  const current = isObject(prediction) ? prediction : {};
  const project = PROJECT_BY_ID.get(projectId) || PROJECT_BY_ID.get("core-runner");
  return {
    predictionId: safeString(current.id),
    baseUid: safeString(current.baseUid),
    basePeriod: safeString(current.basePeriod),
    targetPeriod: safeString(current.targetPeriod),
    generatedAt: safeString(current.generatedAt),
    evaluated: current.evaluated === true,
    evaluatedAt: safeString(current.evaluation && current.evaluation.evaluatedAt),
    payload: cloneCompact(project.selectPrediction(current), 0),
    evaluation: cloneCompact(project.selectEvaluation(current), 0)
  };
}

function projectHealth(projectId, healthMap, prediction) {
  const configured = isObject(healthMap) && isObject(healthMap[projectId]) ? healthMap[projectId] : {};
  return {
    status: configured.status === "paused" ? "paused" : (configured.status === "degraded" ? "degraded" : "ok"),
    issueCount: Math.max(0, Number(configured.issueCount || 0) || 0),
    lastIssue: cloneCompact(configured.lastIssue || null, 0),
    updatedAt: safeString(configured.updatedAt, isoNow()),
    baseUid: safeString(configured.baseUid, prediction && prediction.baseUid),
    basePeriod: safeString(configured.basePeriod, prediction && prediction.basePeriod)
  };
}

function syncProjectStates(options) {
  const opts = isObject(options) ? options : {};
  const dataDir = safeString(opts.dataDir);
  if (!dataDir) throw new Error("dataDir is required for algorithm project state sync");
  const currentPrediction = isObject(opts.currentPrediction) ? opts.currentPrediction : null;
  const predictions = Array.isArray(opts.predictions) ? opts.predictions.filter(isObject) : [];
  const historyLimit = Math.max(20, Math.min(300, Number(opts.historyLimit || 120) || 120));
  const healthMap = isObject(opts.projectHealth) ? opts.projectHealth : {};
  const latestRecord = isObject(opts.latestRecord) ? opts.latestRecord : {};
  const results = [];

  PROJECTS.forEach((project) => {
    const file = projectStateFile(dataDir, project.id);
    const evaluated = predictions
      .filter((prediction) => prediction.evaluated === true)
      .slice(0, historyLimit)
      .map((prediction) => projectRecord(project.id, prediction));
    const payload = {
      version: "algorithm-project-state-v1",
      project: { id: project.id, label: project.label },
      source: {
        officialHistory: "rds-shared-read-only",
        modelVersion: safeString(opts.modelVersion),
        latestPeriod: safeString(latestRecord.period || latestRecord.uid),
        syncedAt: isoNow()
      },
      health: projectHealth(project.id, healthMap, currentPrediction),
      current: currentPrediction ? projectRecord(project.id, currentPrediction) : null,
      history: evaluated
    };
    writeJsonAtomic(file, payload);
    results.push({ id: project.id, file, historyCount: evaluated.length });
  });
  return results;
}

function recordProjectIssue(dataDir, issue, prediction) {
  const projectId = projectIdForIssue(issue);
  const project = PROJECT_BY_ID.get(projectId) || PROJECT_BY_ID.get("core-runner");
  const file = projectStateFile(dataDir, project.id);
  const existing = readJson(file, {});
  const oldHealth = isObject(existing.health) ? existing.health : {};
  const nextHealth = {
    status: "degraded",
    issueCount: Math.max(0, Number(oldHealth.issueCount || 0) || 0) + 1,
    lastIssue: cloneCompact(issue || null, 0),
    updatedAt: isoNow(),
    baseUid: safeString(prediction && prediction.baseUid),
    basePeriod: safeString(prediction && prediction.basePeriod)
  };
  const payload = {
    version: "algorithm-project-state-v1",
    project: { id: project.id, label: project.label },
    source: isObject(existing.source) ? existing.source : { officialHistory: "rds-shared-read-only" },
    health: nextHealth,
    current: existing.current || (prediction ? projectRecord(project.id, prediction) : null),
    history: Array.isArray(existing.history) ? existing.history : []
  };
  writeJsonAtomic(file, payload);
  return { projectId: project.id, health: nextHealth, file };
}

module.exports = {
  PROJECTS,
  SNAPSHOT_PROJECT_IDS,
  projectIdForIssue,
  projectStateFile,
  syncProjectStates,
  recordProjectIssue
};
