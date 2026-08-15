"use strict";

/**
 * Sidecar soft-demote (observe-only by default).
 * Reads claude-ensemble state.json and records conflict/demote hints on tip.
 * NEVER mutates shooting fields (autoTop6 / recommend2 / killFramework / hewei / …).
 * NEVER blocks tipReady.
 */

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const OWN_STATE = path.join(
  REPO_ROOT,
  "data",
  "algorithm-projects",
  "claude-ensemble",
  "state.json"
);

const LOW_RATE = Number(process.env.PENTA5_SIDECAR_DEMOTE_LOW_RATE || 75);
const MIN_SAMPLE = Number(process.env.PENTA5_SIDECAR_DEMOTE_MIN_SAMPLE || 40);

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function enabled() {
  const raw = String(process.env.PENTA5_SIDECAR_SOFT_DEMOTE || "1").trim().toLowerCase();
  return !(raw === "0" || raw === "false" || raw === "off" || raw === "no");
}

function readEnsembleState() {
  try {
    if (!fs.existsSync(OWN_STATE)) return null;
    const raw = fs.readFileSync(OWN_STATE, "utf8");
    const parsed = JSON.parse(raw);
    return asObject(parsed);
  } catch (err) {
    return null;
  }
}

function rateOf(stat) {
  const total = Number(stat && stat.total || 0);
  const hit = Number(stat && stat.hit || 0);
  if (!total) return null;
  if (stat && stat.rate != null && Number.isFinite(Number(stat.rate))) return Number(stat.rate);
  return (hit * 100) / total;
}

/**
 * @param {object} prediction
 * @returns {object} sidecarSoftDemote meta (also written onto prediction)
 */
function applySidecarSoftDemote(prediction) {
  const tip = asObject(prediction);
  const basePeriod = tip.basePeriod != null ? String(tip.basePeriod) : "";
  const at = new Date().toISOString();

  if (!enabled()) {
    tip.sidecarSoftDemote = {
      at,
      basePeriod,
      source: "claude-ensemble",
      mode: "observe",
      skipped: true,
      reason: "disabled",
      demotedKeys: []
    };
    return tip.sidecarSoftDemote;
  }

  const state = readEnsembleState();
  if (!state) {
    tip.sidecarSoftDemote = {
      at,
      basePeriod,
      source: "claude-ensemble",
      mode: "observe",
      skipped: true,
      reason: "not-ready",
      demotedKeys: []
    };
    return tip.sidecarSoftDemote;
  }

  const itemAggregate = asObject(state.itemAggregate);
  const demotedKeys = [];
  Object.keys(itemAggregate).forEach((key) => {
    const stat = asObject(itemAggregate[key]);
    const total = Number(stat.total || 0);
    const rate = rateOf(stat);
    if (total < MIN_SAMPLE || rate == null) return;
    if (rate < LOW_RATE) {
      demotedKeys.push({
        key,
        total,
        hit: Number(stat.hit || 0),
        rate: Number(rate.toFixed(2)),
        reason: "itemAggregate-low-rate"
      });
    }
  });

  demotedKeys.sort((a, b) => a.rate - b.rate);

  const aggregate = asObject(state.aggregate);
  tip.sidecarSoftDemote = {
    at,
    basePeriod,
    source: "claude-ensemble",
    mode: "observe",
    skipped: false,
    reason: "ok",
    aggregate: {
      total: Number(aggregate.total || 0),
      hit: Number(aggregate.hit || 0),
      rate: aggregate.rate != null ? Number(aggregate.rate) : null,
      lower: aggregate.lower != null ? Number(aggregate.lower) : null
    },
    demotedKeys: demotedKeys.slice(0, 24),
    demotedCount: demotedKeys.length,
    note: "observe-only: does not rewrite tip shooting fields"
  };
  return tip.sidecarSoftDemote;
}

module.exports = {
  applySidecarSoftDemote,
  enabled,
  OWN_STATE
};
