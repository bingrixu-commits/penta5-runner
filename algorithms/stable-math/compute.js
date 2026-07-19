"use strict";

/**
 * Stable-math L0 facade over the existing Python bridge.
 * Sync compute attaches cached filter; async compute refreshes from history.
 * Never reads other algorithm-projects state files.
 */

const path = require("path");
const bridge = require("../stable_math_filter_bridge");

let host = null;

function bindHost(nextHost) {
  host = nextHost && typeof nextHost === "object" ? nextHost : null;
}

function resolveCachedFilter() {
  if (host && typeof host.getCachedFilter === "function") {
    const fromHost = host.getCachedFilter();
    if (fromHost && typeof fromHost === "object") return fromHost;
  }
  return bridge.readCachedFilter() || null;
}

function compute(ctx) {
  const cached = resolveCachedFilter();
  const slim = cached ? bridge.slimStableMathFilter(cached) : null;
  return {
    payload: slim,
    meta: {
      algorithmId: "stable-math",
      mode: "cache",
      latestPeriod: slim && slim.latest_period ? slim.latest_period : null,
      historyCount: Array.isArray(ctx && ctx.history) ? ctx.history.length : 0
    }
  };
}

async function computeAsync(ctx, options) {
  const opts = options && typeof options === "object" ? options : {};
  const history = Array.isArray(ctx && ctx.history) ? ctx.history : [];
  const outcome = await bridge.runStableMathFilterAsync(history, {
    timeoutMs: opts.timeoutMs || 25000,
    outPath: opts.outPath || path.join(__dirname, "..", "..", "data", "stable_math_filter.json")
  });
  const filter = outcome && outcome.result ? outcome.result : null;
  const slim = filter ? bridge.slimStableMathFilter(filter) : null;
  if (slim && host && typeof host.setCachedFilter === "function") {
    host.setCachedFilter(slim);
  }
  return {
    payload: slim,
    meta: {
      algorithmId: "stable-math",
      mode: "async",
      ok: !!(outcome && outcome.ok),
      error: outcome && outcome.ok ? null : String((outcome && outcome.error) || ""),
      latestPeriod: slim && slim.latest_period ? slim.latest_period : null
    },
    raw: filter
  };
}

module.exports = {
  bindHost,
  compute,
  computeAsync,
  bridge
};
