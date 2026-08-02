"use strict";

function forwardFailStreak(history, failFn) {
  let streak = 0;
  for (let i = 0; i < (Array.isArray(history) ? history.length : 0); i += 1) {
    if (failFn(history[i])) streak += 1;
    else break;
  }
  return streak;
}

function maxConsecutiveFailInSlice(slice, failFn) {
  let best = 0;
  let cur = 0;
  for (let i = 0; i < (Array.isArray(slice) ? slice.length : 0); i += 1) {
    if (failFn(slice[i])) {
      cur += 1;
      if (cur > best) best = cur;
    } else {
      cur = 0;
    }
  }
  return best;
}

function cooldownActive(lock, history, cooldownPeriods) {
  if (!lock || !lock.lastFailPeriod || !cooldownPeriods) return false;
  const idx = history.findIndex((row) => String(row.period) === String(lock.lastFailPeriod));
  if (idx < 0) return false;
  return idx < cooldownPeriods;
}

function buildPositionFailFn(pick) {
  const modelIndex = pick.index != null ? pick.index : ({
    wan: 0, qian: 1, bai: 2, shi: 3
  }[pick.key]);
  const digit = String(pick.digit);
  return (row) => String(row.numbers[modelIndex]) === digit;
}

function buildHeweiFailFn(pick) {
  const field = {
    total: "totalHewei",
    wanQianBai: "wanQianBaiHewei",
    qianBaiShi: "qianBaiShiHewei",
    wanQianGe: "wanQianGeHewei",
    wanBaiShi: "wanBaiShiHewei"
  }[pick.key];
  const digit = String(pick.digit);
  return (row) => String(row.metrics[field]) === digit;
}

function buildDualFailFn(pick) {
  const legs = pick.legs || [];
  return (row) => legs.some((leg) => String(row.numbers[leg.index]) === String(leg.digit));
}

function failFnForPick(pick) {
  if (pick.kind === "dual-position") return buildDualFailFn(pick);
  if (pick.kind === "hewei") return buildHeweiFailFn(pick);
  return buildPositionFailFn(pick);
}

function passesStreakGate(history, pick, options, lock) {
  const opts = options || {};
  const maxShowStreak = Number(opts.maxShowStreak != null ? opts.maxShowStreak : 0);
  const maxEntryConsecutive = Number(opts.maxEntryConsecutive != null ? opts.maxEntryConsecutive : 1);
  const entryLookback = Math.max(3, Number(opts.entryLookback) || 8);
  const cooldownPeriods = Math.max(0, Number(opts.cooldownPeriods) || 3);

  if (!Array.isArray(history) || !history.length || !pick) {
    return { ok: false, reason: "no_history" };
  }

  if (cooldownActive(lock, history, cooldownPeriods)) {
    return { ok: false, reason: "cooldown_after_fail", cooldownPeriods };
  }

  const failFn = failFnForPick(pick);
  const activeStreak = forwardFailStreak(history, failFn);
  if (activeStreak > maxShowStreak) {
    return { ok: false, reason: "active_forward_streak", activeStreak };
  }

  const recentMax = maxConsecutiveFailInSlice(history.slice(0, entryLookback), failFn);
  if (recentMax > maxEntryConsecutive) {
    return { ok: false, reason: "recent_consecutive_fail", recentMax, entryLookback };
  }

  return { ok: true, activeStreak, recentMax };
}

module.exports = {
  forwardFailStreak,
  maxConsecutiveFailInSlice,
  cooldownActive,
  failFnForPick,
  passesStreakGate
};
