"use strict";

const {
  POSITION_MODELS,
  HEWEI_MODELS,
  normalizeHistoryRows,
  digitAvoidRate,
  bestQualifiedDigit
} = require("./scanner");
const streakGuard = require("./streak-guard");

const ALL_MODELS = POSITION_MODELS.concat(HEWEI_MODELS);

function modelId(model) {
  return `${model.kind}:${model.key}`;
}

function streakOptsFrom(options) {
  return {
    maxShowStreak: Number(options.maxShowStreak != null ? options.maxShowStreak : 0),
    maxEntryConsecutive: Number(options.maxEntryConsecutive != null ? options.maxEntryConsecutive : 1),
    entryLookback: Math.max(3, Number(options.entryLookback) || 8),
    cooldownPeriods: Math.max(0, Number(options.cooldownPeriods) || 3)
  };
}

function actualSeriesForModel(history, model) {
  if (model.kind === "position") {
    return history.map((row) => row.numbers[model.index]);
  }
  return history.map((row) => row.metrics[model.field]);
}

function latestActualForModel(latestRow, model) {
  if (!latestRow) return null;
  if (model.kind === "position") return latestRow.numbers[model.index];
  return latestRow.metrics[model.field];
}

function periodsHeldSince(history, lockedAtPeriod) {
  if (!lockedAtPeriod) return 0;
  const idx = history.findIndex((row) => String(row.period) === String(lockedAtPeriod));
  return idx >= 0 ? idx : history.length;
}

function buildPick(model, stat, digit, extra) {
  const pick = {
    kind: model.kind,
    key: model.key,
    label: model.label,
    digit: String(digit),
    hit: stat.hit,
    total: stat.total,
    rate: stat.rate,
    strategy: extra.strategy || "sticky-lock",
    locked: extra.locked !== false,
    lockedAtPeriod: extra.lockedAtPeriod || null,
    periodsHeld: Number(extra.periodsHeld) || 0,
    forwardFail: !!extra.forwardFail
  };
  if (model.kind === "position") pick.index = model.index;
  return pick;
}

function applyStreakGate(history, pick, lock, options) {
  if (!pick) return { pick: null, lock, gate: { ok: false, reason: "no_pick" } };
  const gate = streakGuard.passesStreakGate(history, pick, streakOptsFrom(options), lock);
  if (gate.ok) return { pick, lock, gate };
  return { pick: null, lock, gate };
}

function resolveModelStickyPick(model, history, options, prevLock) {
  const window = Math.max(20, Number(options.window) || 100);
  const entryMinRate = Number(options.minRate) || 92;
  const exitMinRate = Number(options.exitMinRate != null ? options.exitMinRate : 88);
  const minLockPeriods = Math.max(0, Number(options.minLockPeriods) || 12);

  const series = actualSeriesForModel(history, model);
  const sample = series.slice(0, window);
  if (sample.length < window) return { pick: null, lock: prevLock || null, reason: "short_history" };

  const latest = history[0];
  const latestVal = latestActualForModel(latest, model);

  if (prevLock && prevLock.digit != null) {
    const digit = String(prevLock.digit);
    const stat = digitAvoidRate(sample, digit);
    const held = periodsHeldSince(history, prevLock.lockedAtPeriod);
    const forwardFail = String(latestVal) === digit;

    if (forwardFail) {
      const failLock = Object.assign({}, prevLock, { lastFailPeriod: latest.period });
      const replacement = bestQualifiedDigit(sample, window, entryMinRate);
      if (!replacement) {
        return { pick: null, lock: failLock, reason: "forward_fail_no_replacement" };
      }
      const candidate = buildPick(model, replacement, replacement.digit, {
        strategy: "sticky-lock",
        locked: true,
        lockedAtPeriod: latest.period,
        periodsHeld: 0,
        forwardFail: true
      });
      const nextLock = {
        kind: model.kind,
        key: model.key,
        label: model.label,
        digit: replacement.digit,
        lockedAtPeriod: latest.period,
        lockedAt: options.now || new Date().toISOString(),
        previousDigit: digit,
        unlockReason: "forward_fail",
        lastFailPeriod: latest.period
      };
      const gated = applyStreakGate(history, candidate, nextLock, options);
      return {
        pick: gated.pick,
        lock: gated.lock,
        reason: gated.pick ? "forward_fail_replaced" : (gated.gate.reason || "streak_gate"),
        streakGate: gated.gate
      };
    }

    if (held < minLockPeriods || stat.rate >= exitMinRate) {
      const candidate = buildPick(model, stat, digit, {
        strategy: "sticky-lock",
        locked: true,
        lockedAtPeriod: prevLock.lockedAtPeriod,
        periodsHeld: held
      });
      const holdLock = {
        kind: model.kind,
        key: model.key,
        label: model.label,
        digit,
        lockedAtPeriod: prevLock.lockedAtPeriod,
        lockedAt: prevLock.lockedAt || options.now || new Date().toISOString(),
        lastFailPeriod: prevLock.lastFailPeriod || null
      };
      const gated = applyStreakGate(history, candidate, holdLock, options);
      return {
        pick: gated.pick,
        lock: gated.lock,
        reason: gated.pick ? (held < minLockPeriods ? "min_lock_hold" : "rate_hold") : (gated.gate.reason || "streak_gate"),
        streakGate: gated.gate
      };
    }

    const replacement = bestQualifiedDigit(sample, window, entryMinRate);
    if (replacement) {
      const candidate = buildPick(model, replacement, replacement.digit, {
        strategy: "sticky-lock",
        locked: true,
        lockedAtPeriod: latest.period,
        periodsHeld: 0
      });
      const nextLock = {
        kind: model.kind,
        key: model.key,
        label: model.label,
        digit: replacement.digit,
        lockedAtPeriod: latest.period,
        lockedAt: options.now || new Date().toISOString(),
        previousDigit: digit,
        unlockReason: "rate_below_exit",
        lastFailPeriod: prevLock.lastFailPeriod || null
      };
      const gated = applyStreakGate(history, candidate, nextLock, options);
      return {
        pick: gated.pick,
        lock: gated.lock,
        reason: gated.pick ? "rate_exit_replaced" : (gated.gate.reason || "streak_gate"),
        streakGate: gated.gate
      };
    }
    return { pick: null, lock: prevLock, reason: "rate_exit_no_replacement" };
  }

  const best = bestQualifiedDigit(sample, window, entryMinRate);
  if (!best) return { pick: null, lock: null, reason: "no_entry" };
  const candidate = buildPick(model, best, best.digit, {
    strategy: "sticky-lock",
    locked: true,
    lockedAtPeriod: latest.period,
    periodsHeld: 0
  });
  const nextLock = {
    kind: model.kind,
    key: model.key,
    label: model.label,
    digit: best.digit,
    lockedAtPeriod: latest.period,
    lockedAt: options.now || new Date().toISOString()
  };
  const gated = applyStreakGate(history, candidate, nextLock, options);
  return {
    pick: gated.pick,
    lock: gated.lock,
    reason: gated.pick ? "new_entry" : (gated.gate.reason || "streak_gate"),
    streakGate: gated.gate
  };
}

function scanQualifiedPicksSticky(historyRows, options, previousLocks) {
  const window = Math.max(20, Number(options && options.window) || 100);
  const minRate = Number(options && options.minRate) || 92;
  const exitMinRate = Number(options && options.exitMinRate != null ? options.exitMinRate : 88);
  const minLockPeriods = Math.max(0, Number(options && options.minLockPeriods) || 12);
  const now = options && options.now ? options.now : new Date().toISOString();
  const history = normalizeHistoryRows(historyRows);
  const audit = require("./audit");
  const digestInfo = audit.historyWindowDigest(history, window);
  const locksIn = previousLocks && typeof previousLocks === "object" ? previousLocks : {};
  const locksOut = {};
  const picks = [];
  const streakGates = {};

  ALL_MODELS.forEach((model) => {
    const id = modelId(model);
    const prev = locksIn[id] || null;
    const resolved = resolveModelStickyPick(model, history, {
      window,
      minRate,
      exitMinRate,
      minLockPeriods,
      maxShowStreak: options && options.maxShowStreak,
      maxEntryConsecutive: options && options.maxEntryConsecutive,
      entryLookback: options && options.entryLookback,
      cooldownPeriods: options && options.cooldownPeriods,
      now
    }, prev);
    if (resolved.lock) locksOut[id] = resolved.lock;
    if (resolved.pick) picks.push(resolved.pick);
    if (resolved.streakGate) streakGates[id] = resolved.streakGate;
  });

  return {
    window,
    minRate,
    exitMinRate,
    minLockPeriods,
    maxShowStreak: streakOptsFrom(options || {}).maxShowStreak,
    maxEntryConsecutive: streakOptsFrom(options || {}).maxEntryConsecutive,
    cooldownPeriods: streakOptsFrom(options || {}).cooldownPeriods,
    strategy: "sticky-lock",
    historyCount: history.length,
    latestPeriod: history[0] ? history[0].period : null,
    windowDigest: digestInfo.digest,
    windowNewestPeriod: digestInfo.newestPeriod,
    windowOldestPeriod: digestInfo.oldestPeriod,
    picks,
    locks: locksOut,
    streakGates
  };
}

module.exports = {
  ALL_MODELS,
  modelId,
  periodsHeldSince,
  streakOptsFrom,
  applyStreakGate,
  scanQualifiedPicksSticky
};
