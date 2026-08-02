"use strict";

const { normalizeHistoryRows, POSITION_MODELS } = require("./scanner");
const streakGuard = require("./streak-guard");
const { streakOptsFrom, applyStreakGate } = require("./sticky");

const POS_BY_INDEX = POSITION_MODELS.reduce((acc, m) => {
  acc[m.index] = m;
  return acc;
}, {});

function dualKey(posIndexes, digits) {
  return `dual:${posIndexes.join("+")}:${digits.join("+")}`;
}

function evalDual(sample, posIndexes, digits) {
  const [i0, i1] = posIndexes;
  const [d0, d1] = digits;
  let hit = 0;
  for (let i = 0; i < sample.length; i += 1) {
    const nums = sample[i].numbers;
    if (String(nums[i0]) !== String(d0) && String(nums[i1]) !== String(d1)) hit += 1;
  }
  const total = sample.length;
  return {
    hit,
    total,
    rate: total ? Number(((hit * 100) / total).toFixed(2)) : 0
  };
}

function buildDualPick(posIndexes, digits, stat, extra) {
  const legs = posIndexes.map((pi, idx) => {
    const model = POS_BY_INDEX[pi];
    return {
      key: model.key,
      label: model.label,
      index: pi,
      digit: String(digits[idx])
    };
  });
  return {
    kind: "dual-position",
    key: legs.map((l) => l.key).join("+"),
    label: legs.map((l) => l.label + l.digit).join("+"),
    legs,
    hit: stat.hit,
    total: stat.total,
    rate: stat.rate,
    strategy: extra.strategy || "dual-position",
    locked: extra.locked !== false,
    lockedAtPeriod: extra.lockedAtPeriod || null,
    periodsHeld: Number(extra.periodsHeld) || 0
  };
}

function buildDualLock(pick, extra) {
  if (!pick) return null;
  return {
    posIndexes: pick.legs.map((l) => l.index),
    digits: pick.legs.map((l) => l.digit),
    lockedAtPeriod: extra.lockedAtPeriod,
    lockedAt: extra.lockedAt,
    unlockReason: extra.unlockReason || null,
    lastFailPeriod: extra.lastFailPeriod || null
  };
}

function gateDualPick(history, pick, lock, options) {
  if (!pick) return { pick: null, lock, gate: { ok: false, reason: "no_pick" } };
  return applyStreakGate(history, pick, lock, options);
}

function scanDualPositionPicks(historyRows, options) {
  const window = Math.max(20, Number(options && options.window) || 100);
  const minRate = Number(options && options.minRate) || 89;
  const history = normalizeHistoryRows(historyRows);
  const sample = history.slice(0, window);
  const picks = [];

  if (sample.length < window) {
    return {
      window,
      minRate,
      historyCount: history.length,
      latestPeriod: history[0] ? history[0].period : null,
      picks: []
    };
  }

  for (let a = 0; a < 4; a += 1) {
    for (let b = a + 1; b < 4; b += 1) {
      const posIndexes = [a, b];
      for (let d0 = 0; d0 <= 9; d0 += 1) {
        for (let d1 = 0; d1 <= 9; d1 += 1) {
          const digits = [String(d0), String(d1)];
          const stat = evalDual(sample, posIndexes, digits);
          if (stat.rate < minRate) continue;
          picks.push(buildDualPick(posIndexes, digits, stat, { strategy: "dual-position" }));
        }
      }
    }
  }

  picks.sort((x, y) => y.rate - x.rate || x.label.localeCompare(y.label));
  const best = picks[0] || null;
  if (!best) {
    return {
      window,
      minRate,
      historyCount: history.length,
      latestPeriod: history[0] ? history[0].period : null,
      picks: [],
      candidates: 0
    };
  }

  const lock = buildDualLock(best, {
    lockedAtPeriod: history[0].period,
    lockedAt: options && options.now ? options.now : new Date().toISOString()
  });
  const gated = gateDualPick(history, best, lock, options);

  return {
    window,
    minRate,
    historyCount: history.length,
    latestPeriod: history[0] ? history[0].period : null,
    picks: gated.pick ? [gated.pick] : [],
    candidates: picks.length,
    streakGate: gated.gate
  };
}

function periodsHeldSince(history, lockedAtPeriod) {
  if (!lockedAtPeriod) return 0;
  const idx = history.findIndex((row) => String(row.period) === String(lockedAtPeriod));
  return idx >= 0 ? idx : history.length;
}

function scanDualPositionSticky(historyRows, options, previousLock) {
  const window = Math.max(20, Number(options && options.window) || 100);
  const minRate = Number(options && options.minRate) || 89;
  const exitMinRate = Number(options && options.exitMinRate != null ? options.exitMinRate : 85);
  const minLockPeriods = Math.max(0, Number(options && options.minLockPeriods) || 12);
  const now = options && options.now ? options.now : new Date().toISOString();
  const history = normalizeHistoryRows(historyRows);
  const sample = history.slice(0, window);

  if (sample.length < window) {
    return { window, minRate, exitMinRate, minLockPeriods, picks: [], lock: null };
  }

  if (previousLock && previousLock.posIndexes && previousLock.digits) {
    const posIndexes = previousLock.posIndexes;
    const digits = previousLock.digits.map(String);
    const stat = evalDual(sample, posIndexes, digits);
    const held = periodsHeldSince(history, previousLock.lockedAtPeriod);
    const latest = history[0].numbers;
    const forwardFail = String(latest[posIndexes[0]]) === digits[0]
      || String(latest[posIndexes[1]]) === digits[1];

    if (forwardFail) {
      const fresh = scanDualPositionPicks(historyRows, Object.assign({}, options, { window, minRate, now }));
      const pick = fresh.picks[0] || null;
      const lock = pick ? buildDualLock(pick, {
        lockedAtPeriod: history[0].period,
        lockedAt: now,
        unlockReason: "forward_fail",
        lastFailPeriod: history[0].period
      }) : Object.assign({}, previousLock, { lastFailPeriod: history[0].period });
      const gated = gateDualPick(history, pick, lock, options);
      return {
        window,
        minRate,
        exitMinRate,
        minLockPeriods,
        picks: gated.pick ? [gated.pick] : [],
        lock: gated.lock,
        streakGate: gated.gate
      };
    }

    if (held < minLockPeriods || stat.rate >= exitMinRate) {
      const pick = buildDualPick(posIndexes, digits, stat, {
        strategy: "dual-position-sticky",
        lockedAtPeriod: previousLock.lockedAtPeriod,
        periodsHeld: held
      });
      const lock = {
        posIndexes,
        digits,
        lockedAtPeriod: previousLock.lockedAtPeriod,
        lockedAt: previousLock.lockedAt || now,
        lastFailPeriod: previousLock.lastFailPeriod || null
      };
      const gated = gateDualPick(history, pick, lock, options);
      return {
        window,
        minRate,
        exitMinRate,
        minLockPeriods,
        picks: gated.pick ? [gated.pick] : [],
        lock: gated.lock,
        streakGate: gated.gate
      };
    }

    const fresh = scanDualPositionPicks(historyRows, Object.assign({}, options, { window, minRate, now }));
    const pick = fresh.picks[0] || null;
    const lock = pick ? buildDualLock(pick, {
      lockedAtPeriod: history[0].period,
      lockedAt: now,
      unlockReason: "rate_below_exit",
      lastFailPeriod: previousLock.lastFailPeriod || null
    }) : null;
    const gated = gateDualPick(history, pick, lock, options);
    return {
      window,
      minRate,
      exitMinRate,
      minLockPeriods,
      picks: gated.pick ? [gated.pick] : [],
      lock: gated.lock,
      streakGate: gated.gate
    };
  }

  const fresh = scanDualPositionPicks(historyRows, Object.assign({}, options, { window, minRate, now }));
  const pick = fresh.picks[0] || null;
  if (!pick) {
    return { window, minRate, exitMinRate, minLockPeriods, picks: [], lock: null };
  }
  const lock = buildDualLock(pick, {
    lockedAtPeriod: history[0].period,
    lockedAt: now
  });
  const gated = gateDualPick(history, pick, lock, options);
  return {
    window,
    minRate,
    exitMinRate,
    minLockPeriods,
    picks: gated.pick ? [gated.pick] : [],
    lock: gated.lock,
    streakGate: gated.gate
  };
}

function dualPickFingerprint(picks) {
  return (Array.isArray(picks) ? picks : [])
    .map((p) => {
      const legs = (p.legs || []).map((l) => `${l.key}:${l.digit}`).join("|");
      return `${legs}:${p.hit}/${p.total}:${p.rate}`;
    })
    .join(";");
}

function dualPicksMatch(a, b) {
  return dualPickFingerprint(a) === dualPickFingerprint(b);
}

module.exports = {
  dualKey,
  evalDual,
  scanDualPositionPicks,
  scanDualPositionSticky,
  dualPickFingerprint,
  dualPicksMatch
};
