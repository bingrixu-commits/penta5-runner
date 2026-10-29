"use strict";

/**
 * Global selection policy.
 * 30/60/120 each cast 1 first-place vote.
 * Early-rotate: do not wait until a kill is already wrong —
 * yield if the current code failed >=2 times in the last 10,
 * force rotate at max hold, keep at most min hold against noise.
 * Rollback sticky-only: PENTA5_SELECTION_MODE=lock
 */

const VOTE_WINDOWS = Object.freeze([
  { win: 30, rateKey: "rate30", sampleKey: "sample30", minSample: 20 },
  { win: 60, rateKey: "rate60", sampleKey: "sample60", minSample: 40 },
  { win: 120, rateKey: "rate120", sampleKey: "sample120", minSample: 60 }
]);

const EARLY_ROTATE_WINDOW = Math.max(4, Number(process.env.EARLY_ROTATE_WINDOW || 10));
const EARLY_ROTATE_FAILS = Math.max(1, Number(process.env.EARLY_ROTATE_FAILS || 2));
const SELECT_MIN_HOLD = Math.max(1, Number(process.env.SELECT_MIN_HOLD || 2));
const SELECT_MAX_HOLD = Math.max(SELECT_MIN_HOLD, Number(process.env.SELECT_MAX_HOLD || 8));

function envFlagEnabled(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined || raw === null || String(raw).trim() === "") return !!defaultValue;
  return !/^(0|false|off|no)$/i.test(String(raw).trim());
}

function voteModeEnabled() {
  const raw = process.env.PENTA5_SELECTION_MODE;
  if (raw === undefined || raw === null || String(raw).trim() === "") {
    return envFlagEnabled("PENTA5_VOTE_MODE", true);
  }
  const mode = String(raw).trim().toLowerCase();
  if (mode === "lock" || mode === "locks" || mode === "hold" || mode === "sticky") return false;
  return true;
}

function locksEnabled() {
  return !voteModeEnabled();
}

function nestedWindow(row, win) {
  if (!row || typeof row !== "object") return null;
  const nested = row["r" + win] || row["rate" + win];
  if (nested && typeof nested === "object" && !Array.isArray(nested)) return nested;
  return null;
}

function hydrateVoteRates(row) {
  if (!row || typeof row !== "object") return row;
  [10, 15, 30, 60, 120].forEach((win) => {
    const nested = nestedWindow(row, win);
    const rateKey = "rate" + win;
    const sampleKey = "sample" + win;
    if (nested) {
      if (row[rateKey] == null) row[rateKey] = Number(nested.rate || 0);
      if (row[sampleKey] == null) row[sampleKey] = Number(nested.total || 0);
    } else {
      if (row[rateKey] == null) row[rateKey] = Number(row[rateKey] || (win === 30 ? (row.rate || row.score || 0) : 0));
      if (row[sampleKey] == null) {
        row[sampleKey] = Number(row["total" + win] || (win === 30 ? (row.total || row.sample || 0) : 0));
      }
    }
    if (row.lower120 == null && nestedWindow(row, 120)) {
      row.lower120 = Number(row.lower120 || 0);
    }
  });
  if (row.missStreak30 == null) {
    const r30 = nestedWindow(row, 30);
    row.missStreak30 = Number((r30 && r30.missStreak) || row.missStreak || 0);
  }
  if (row.fail10 == null) {
    const r10 = nestedWindow(row, 10);
    if (r10 && Number(r10.total || 0) > 0) {
      row.fail10 = Math.max(0, Number(r10.total || 0) - Number(r10.hit || 0));
    } else if (row.fail10 == null && Number(row.sample10 || 0) > 0 && row.rate10 != null) {
      row.fail10 = Math.max(0, Math.round(Number(row.sample10) * (1 - Number(row.rate10) / 100)));
    }
  }
  return row;
}

function rowCode(row) {
  if (!row) return "";
  if (row.digit != null && String(row.digit).length && String(row.digit) !== "--") return String(row.digit);
  if (Array.isArray(row.pair) && row.pair.length) return row.pair.map(String).join("");
  if (row.code != null && String(row.code).length) return String(row.code);
  if (row.value != null && String(row.value).length) return String(row.value);
  if (row.pairKey != null) return String(row.pairKey);
  if (row.key != null) return String(row.key);
  return "";
}

function recentFailCount(row) {
  if (!row || typeof row !== "object") return 0;
  hydrateVoteRates(row);
  if (row.fail10 != null && Number.isFinite(Number(row.fail10))) return Number(row.fail10);
  const r10 = nestedWindow(row, 10);
  if (r10 && Number(r10.total || 0) > 0) {
    return Math.max(0, Number(r10.total || 0) - Number(r10.hit || 0));
  }
  if (Number(row.sample10 || 0) > 0 && row.rate10 != null) {
    return Math.max(0, Math.round(Number(row.sample10) * (1 - Number(row.rate10) / 100)));
  }
  const r15 = nestedWindow(row, 15);
  if (r15 && Number(r15.total || 0) > 0) {
    return Math.max(0, Number(r15.total || 0) - Number(r15.hit || 0));
  }
  if (row.fail30 != null && Number.isFinite(Number(row.fail30))) {
    return Math.round(Number(row.fail30) * EARLY_ROTATE_WINDOW / 30);
  }
  return Number(row.missStreak30 || 0) >= 2 ? Number(row.missStreak30) : 0;
}

function isEarlyRisk(row, failThreshold) {
  const threshold = Math.max(1, Number(failThreshold || EARLY_ROTATE_FAILS));
  return recentFailCount(row) >= threshold;
}

function nextHoldCount(oldCode, newCode, oldHold, period, lockPeriod) {
  const samePeriod = String(lockPeriod || "") === String(period || "");
  if (oldCode && String(oldCode) === String(newCode)) {
    return samePeriod ? Math.max(1, Number(oldHold || 0)) : Number(oldHold || 0) + 1;
  }
  return 1;
}

function compareVoted(a, b) {
  const av = Number(a && a.votes || 0);
  const bv = Number(b && b.votes || 0);
  if (bv !== av) return bv - av;
  const al = Number(a && a.lower120 || 0);
  const bl = Number(b && b.lower120 || 0);
  if (bl !== al) return bl - al;
  const ar = Number(a && a.rate120 || 0);
  const br = Number(b && b.rate120 || 0);
  if (br !== ar) return br - ar;
  const as = Number(a && a.score || 0);
  const bs = Number(b && b.score || 0);
  if (bs !== as) return bs - as;
  return String(rowCode(a)).localeCompare(String(rowCode(b)), "zh-CN");
}

function assignFirstPlaceVotes(rows) {
  const list = (Array.isArray(rows) ? rows : []).filter(Boolean);
  list.forEach((row) => {
    hydrateVoteRates(row);
    row.votes = 0;
  });
  VOTE_WINDOWS.forEach((spec) => {
    let best = null;
    list.forEach((row) => {
      if (Number(row[spec.sampleKey] || 0) < spec.minSample) return;
      if (!best) {
        best = row;
        return;
      }
      const challengerRate = Number(row[spec.rateKey] || 0);
      const incumbentRate = Number(best[spec.rateKey] || 0);
      if (challengerRate > incumbentRate) {
        best = row;
        return;
      }
      if (challengerRate < incumbentRate) return;
      if (Number(row.lower120 || 0) > Number(best.lower120 || 0)) {
        best = row;
        return;
      }
      if (Number(row.lower120 || 0) < Number(best.lower120 || 0)) return;
      if (String(rowCode(row)).localeCompare(String(rowCode(best)), "zh-CN") < 0) best = row;
    });
    if (best) best.votes += 1;
  });
  list.sort(compareVoted);
  return list;
}

function tagPick(row, reason) {
  if (row && typeof row === "object") {
    row.rotateReason = reason;
    row.recentFails = recentFailCount(row);
  }
  return row;
}

function pickVotedRow(rows, options) {
  const opts = options && typeof options === "object" ? options : {};
  const all = assignFirstPlaceVotes(rows);
  if (!all.length) return null;
  const voted = all.filter((row) => Number(row.votes || 0) > 0);
  const ranked = (voted.length ? voted : all).slice();
  ranked.sort(compareVoted);
  const failThreshold = Math.max(1, Number(opts.failThreshold || EARLY_ROTATE_FAILS));
  const minHold = Math.max(1, Number(opts.minHold != null ? opts.minHold : SELECT_MIN_HOLD));
  const maxHold = Math.max(minHold, Number(opts.maxHold != null ? opts.maxHold : SELECT_MAX_HOLD));
  const currentCode = String(opts.currentCode || "");
  const holdCount = Math.max(0, Number(opts.holdCount || 0));
  const current = currentCode
    ? all.find((row) => rowCode(row) === currentCode)
    : null;
  const full = all.slice().sort(compareVoted);
  const safe = full.filter((row) => !isEarlyRisk(row, failThreshold));
  const safePool = safe.length ? safe : full;
  const altOf = (code) => safePool.find((row) => rowCode(row) !== code)
    || full.find((row) => rowCode(row) !== code)
    || null;

  if (current && holdCount > 0 && holdCount < minHold && !isEarlyRisk(current, failThreshold)) {
    return tagPick(current, "keep-min-hold");
  }
  if (current && holdCount >= maxHold) {
    const alt = altOf(currentCode);
    if (alt) return tagPick(alt, "max-hold-rotate");
  }
  if (current && isEarlyRisk(current, failThreshold)) {
    const alt = altOf(currentCode);
    if (alt) return tagPick(alt, "early-risk-yield");
  }

  let picked = ranked[0];
  if (picked && isEarlyRisk(picked, failThreshold)) {
    const alt = altOf(rowCode(picked));
    if (alt) return tagPick(alt, "early-risk-skip");
  }
  return tagPick(picked, "vote-first-place");
}

function pickVotedBackup(rows, selected, options) {
  const skip = rowCode(selected);
  const rest = (Array.isArray(rows) ? rows : []).filter((row) => row && rowCode(row) !== skip);
  const opts = Object.assign({}, options && typeof options === "object" ? options : {}, {
    currentCode: "",
    holdCount: 0
  });
  return pickVotedRow(rest, opts);
}

module.exports = {
  VOTE_WINDOWS,
  EARLY_ROTATE_WINDOW,
  EARLY_ROTATE_FAILS,
  SELECT_MIN_HOLD,
  SELECT_MAX_HOLD,
  voteModeEnabled,
  locksEnabled,
  hydrateVoteRates,
  rowCode,
  recentFailCount,
  isEarlyRisk,
  nextHoldCount,
  assignFirstPlaceVotes,
  pickVotedRow,
  pickVotedBackup,
  compareVoted
};
