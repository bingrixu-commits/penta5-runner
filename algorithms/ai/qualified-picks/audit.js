"use strict";

const crypto = require("crypto");

function historyWindowDigest(normalizedHistory, window) {
  const w = Math.max(1, Number(window) || 100);
  const sample = (Array.isArray(normalizedHistory) ? normalizedHistory : []).slice(0, w);
  const lines = sample.map((row) => `${row.period}|${row.numbers.slice(0, 4).join(",")}`);
  const digest = crypto.createHash("sha256").update(lines.join("\n"), "utf8").digest("hex");
  return {
    digest,
    window: w,
    lineCount: lines.length,
    newestPeriod: sample[0] ? sample[0].period : null,
    oldestPeriod: sample.length ? sample[sample.length - 1].period : null
  };
}

function crossCheckHistories(normalizedA, normalizedB, window) {
  const w = Math.max(1, Number(window) || 100);
  const issues = [];
  const la = (Array.isArray(normalizedA) ? normalizedA : []).slice(0, w);
  const lb = (Array.isArray(normalizedB) ? normalizedB : []).slice(0, w);
  if (la.length < w) issues.push(`sourceA_short:${la.length}<${w}`);
  if (lb.length < w) issues.push(`sourceB_short:${lb.length}<${w}`);
  const n = Math.min(la.length, lb.length, w);
  for (let i = 0; i < n; i += 1) {
    const a = la[i];
    const b = lb[i];
    if (a.period !== b.period) {
      issues.push(`period_mismatch@${i}:${a.period}_vs_${b.period}`);
      break;
    }
    for (let j = 0; j < 4; j += 1) {
      if (String(a.numbers[j]) !== String(b.numbers[j])) {
        issues.push(`numbers_mismatch:${a.period}:ball${j + 1}:${a.numbers[j]}_vs_${b.numbers[j]}`);
      }
    }
  }
  return { ok: issues.length === 0, issues };
}

function pickFingerprint(picks) {
  return (Array.isArray(picks) ? picks : [])
    .slice()
    .sort((a, b) => {
      const ka = `${a.kind || ""}:${a.key || ""}:${a.digit || ""}`;
      const kb = `${b.kind || ""}:${b.key || ""}:${b.digit || ""}`;
      return ka.localeCompare(kb);
    })
    .map((p) => `${p.kind}:${p.key}:${p.digit}:${p.hit}/${p.total}:${p.rate}`)
    .join("|");
}

function picksMatch(a, b) {
  return pickFingerprint(a) === pickFingerprint(b);
}

module.exports = {
  historyWindowDigest,
  crossCheckHistories,
  pickFingerprint,
  picksMatch
};
