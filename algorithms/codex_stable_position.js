"use strict";

/**
 * CODEX_STABLE_MODEL_0200 — backend mirror of enhanced.html buildOwnPositionModels().
 * Rolling backtest per position; latest draw is display-only (excluded from training).
 */

const DIGIT_POOL = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];

const CODEX_RULE_LIST = [
  { id: "balancedLow", label: "低频均衡" },
  { id: "cold8", label: "近8冷排" },
  { id: "cold16", label: "近16冷排" },
  { id: "hotCool", label: "过热回落" },
  { id: "gapSafe", label: "间隔稳排" },
  { id: "prevAvoid", label: "前期避重" }
];

const POSITION_MAP = [
  { key: "wan", label: "千位", index: 0 },
  { key: "qian", label: "百位", index: 1 },
  { key: "bai", label: "十位", index: 2 },
  { key: "shi", label: "个位", index: 3 }
];

function sameRecord(a, b) {
  if (!a || !b) return false;
  if (a.uid && b.uid && String(a.uid) === String(b.uid)) return true;
  if (a.period && b.period && String(a.period) === String(b.period)) return true;
  return false;
}

function recordDigit(record, index) {
  if (!record || !Array.isArray(record.numbers)) return null;
  const n = Number(record.numbers[index]);
  return Number.isInteger(n) && n >= 0 && n <= 9 ? n : null;
}

function sequenceFor(training, index) {
  return training.map((record) => recordDigit(record, index)).filter((digit) => digit !== null);
}

function countDigit(seq, digit, size) {
  return seq.slice(0, size).filter((item) => item === digit).length;
}

function gapOf(seq, digit) {
  const index = seq.indexOf(digit);
  return index < 0 ? 99 : index;
}

function buildStats(training, index, blockDigit) {
  const seq = sequenceFor(training, index);
  return DIGIT_POOL.map((digit) => {
    const recent8 = countDigit(seq, digit, 8);
    const recent16 = countDigit(seq, digit, 16);
    const recent40 = countDigit(seq, digit, 40);
    const gap = gapOf(seq, digit);
    const prev = seq[0] || null;
    const latestPenalty = blockDigit !== undefined && blockDigit !== null && String(digit) === String(blockDigit) ? 999 : 0;
    return { digit, recent8, recent16, recent40, gap, prev, latestPenalty };
  });
}

function pickByRule(training, index, ruleId, blockDigit) {
  const seq = sequenceFor(training, index);
  if (seq.length < 12) return null;
  const stats = buildStats(training, index, blockDigit);
  const safeStats = stats.filter((item) => item.latestPenalty < 999);
  const pool = safeStats.length ? safeStats : stats;
  const withScore = pool.map((item) => {
    let score = 0;
    if (ruleId === "prevAvoid") {
      score = item.digit === item.prev ? -100 : 100 + item.recent8 + item.latestPenalty;
    } else if (ruleId === "cold8") {
      score = item.recent8 * 20 + item.recent16 * 4 + (item.gap > 22 ? 8 : 0) + item.latestPenalty;
    } else if (ruleId === "cold16") {
      score = item.recent16 * 12 + item.recent40 * 1.5 + (item.gap > 28 ? 10 : 0) + item.latestPenalty;
    } else if (ruleId === "hotCool") {
      score = -item.recent8 * 16 - item.recent16 * 2 + (item.gap === 0 ? -6 : 0) + item.latestPenalty;
    } else if (ruleId === "gapSafe") {
      const gapCenter = Math.abs(item.gap - 7);
      score = gapCenter * 4 + item.recent8 * 7 + (item.gap > 18 ? 10 : 0) + item.latestPenalty;
    } else {
      score = item.recent8 * 10 + item.recent16 * 3 + Math.abs(item.gap - 9) * 1.3 + (item.digit === item.prev ? 10 : 0) + item.latestPenalty;
    }
    return Object.assign({}, item, { score });
  });
  withScore.sort((a, b) => a.score - b.score || a.recent8 - b.recent8 || a.recent16 - b.recent16 || Number(a.digit) - Number(b.digit));
  return withScore[0] ? String(withScore[0].digit) : null;
}

function backtestRule(records, pos, rule) {
  const maxTargets = Math.min(records.length - 18, 90);
  let total = 0;
  let hit = 0;
  for (let i = 0; i < maxTargets; i += 1) {
    const actual = recordDigit(records[i], pos.index);
    const blockDigit = recordDigit(records[i + 1], pos.index);
    const training = records.slice(i + 1, i + 91);
    const pick = pickByRule(training, pos.index, rule.id, blockDigit);
    if (actual === null || pick === null) continue;
    total += 1;
    if (String(pick) !== String(actual)) hit += 1;
  }
  return { total, hit, rate: total ? hit / total : 0 };
}

function computeCodexStablePositionKills(history) {
  const safeHistory = Array.isArray(history) ? history.filter(Boolean) : [];
  const latest = safeHistory[0] || null;
  const sourceRecords = safeHistory.filter((item) => item && !sameRecord(item, latest));
  const records = sourceRecords.length >= 18 ? sourceRecords : safeHistory.filter(Boolean);
  const out = {};
  POSITION_MAP.forEach((pos) => {
    out[pos.key] = { digit: null, rate: 0, total: 0, hit: 0, ruleId: null, ruleLabel: null, recommended: false, strict: false };
  });
  if (records.length < 18) return out;

  POSITION_MAP.forEach((pos) => {
    const latestDigit = latest ? recordDigit(latest, pos.index) : null;
    const options = CODEX_RULE_LIST.map((rule) => {
      const digit = pickByRule(records, pos.index, rule.id, latestDigit);
      const test = backtestRule(records, pos, rule);
      const stability = test.rate * 100 + Math.min(test.total, 70) * 0.03;
      return { rule, digit, total: test.total, hit: test.hit, rate: test.rate, stability };
    }).filter((item) => item.digit !== null && item.total >= 30);

    options.sort((a, b) => b.rate - a.rate || b.hit - a.hit || b.stability - a.stability);
    const best = options[0];
    if (!best) return;
    const strongEnough = best.rate >= 0.88 && best.total >= 40;
    out[pos.key] = {
      digit: best.digit,
      rate: Number(best.rate.toFixed(4)),
      total: best.total,
      hit: best.hit,
      ruleId: best.rule.id,
      ruleLabel: best.rule.label,
      recommended: strongEnough,
      strict: best.rate >= 0.93 && best.total >= 40
    };
  });
  return out;
}

function applyCodexStablePositionOverlay(positionKills, history) {
  if (!positionKills || typeof positionKills !== "object") return positionKills;
  const codex = computeCodexStablePositionKills(history);
  POSITION_MAP.forEach((pos) => {
    const entry = positionKills[pos.key];
    const pick = codex[pos.key];
    if (!entry || typeof entry !== "object" || !pick || !pick.digit) return;
    entry.codexStableDigit = pick.digit;
    entry.codexStableRate = pick.rate;
    entry.codexStableTotal = pick.total;
    entry.codexStableHit = pick.hit;
    entry.codexStableRule = pick.ruleId;
    entry.codexStableRuleLabel = pick.ruleLabel;
    entry.codexStableRecommended = !!pick.recommended;
    entry.codexStableStrict = !!pick.strict;
    if (!pick.recommended) return;

    const blocksMain = String(entry.main || entry.mainDigit || entry.primaryDigit || (entry.killDigits && entry.killDigits[0]) || "");
    const blocksBackup = String(entry.backup || entry.backupDigit || (entry.killDigits && entry.killDigits[1]) || "");
    entry.stabilityBlocksMain = blocksMain;
    entry.stabilityBlocksBackup = blocksBackup;

    const codexDigit = String(pick.digit);
    let backup = blocksBackup;
    if (backup === codexDigit || backup === "--" || !/^\d$/.test(backup)) {
      backup = blocksMain !== codexDigit && /^\d$/.test(blocksMain) ? blocksMain : "";
    }
    if (!backup || backup === codexDigit) {
      for (let i = 0; i < DIGIT_POOL.length; i += 1) {
        const d = DIGIT_POOL[i];
        if (d !== codexDigit) {
          backup = d;
          break;
        }
      }
    }

    entry.main = codexDigit;
    entry.mainDigit = codexDigit;
    entry.recommendDigit = codexDigit;
    entry.selectedDigit = codexDigit;
    entry.primaryDigit = codexDigit;
    entry.backup = backup;
    entry.backupDigit = backup;
    entry.killDigits = [codexDigit, backup].filter((d, idx, arr) => /^\d$/.test(d) && arr.indexOf(d) === idx);
    entry.algorithm = "v42-position-stability-blocks+codex-stable-0200";
    entry.reason = `codex-stable-override:${pick.ruleId}:${(pick.rate * 100).toFixed(2)}%`;
  });
  return positionKills;
}

module.exports = {
  CODEX_RULE_LIST,
  POSITION_MAP,
  computeCodexStablePositionKills,
  applyCodexStablePositionOverlay
};
