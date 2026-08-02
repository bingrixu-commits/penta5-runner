"use strict";

/**
 * codex-stable primary formula (CODEX_STABLE_MODEL_0202).
 * Rule selection and validation use disjoint walk-forward windows.
 */

const DIGIT_POOL = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
const CODEX_STABLE_VALIDATION_WINDOW = 100;
const CODEX_STABLE_SELECTION_WINDOW = 200;
const CODEX_STABLE_TRAINING_WINDOW = 100;
const CODEX_STABLE_RANDOM_BASELINE = 0.90;
const CODEX_STABLE_CONFIDENCE_Z = 1.96;
const CODEX_STABLE_OVERLAY_ENABLED = String(process.env.CODEX_STABLE_OVERLAY_ENABLED || "0") === "1";

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

function historyPeriodKey(record) {
  return String(record && (record.period || record.uid) || "");
}

function newestFirstHistory(history) {
  return (Array.isArray(history) ? history : [])
    .filter(Boolean)
    .slice()
    .sort((a, b) => historyPeriodKey(b).localeCompare(historyPeriodKey(a)));
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
    const prev = seq.length ? seq[0] : null;
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

function backtestRule(records, pos, rule, offset, limit) {
  const start = Math.max(0, Number(offset || 0));
  const targetLimit = Math.max(1, Number(limit || CODEX_STABLE_VALIDATION_WINDOW));
  const maxTargets = Math.min(Math.max(0, records.length - start - 18), targetLimit);
  let total = 0;
  let hit = 0;
  for (let cursor = 0; cursor < maxTargets; cursor += 1) {
    const i = start + cursor;
    const actual = recordDigit(records[i], pos.index);
    const blockDigit = recordDigit(records[i + 1], pos.index);
    const training = records.slice(i + 1, i + 1 + CODEX_STABLE_TRAINING_WINDOW);
    const pick = pickByRule(training, pos.index, rule.id, blockDigit);
    if (actual === null || pick === null) continue;
    total += 1;
    if (String(pick) !== String(actual)) hit += 1;
  }
  return { total, hit, rate: total ? hit / total : 0 };
}

function wilsonLowerBound(hit, total, z) {
  if (!total) return 0;
  const scoreZ = Number(z || CODEX_STABLE_CONFIDENCE_Z);
  const rate = hit / total;
  const z2 = scoreZ * scoreZ;
  const denominator = 1 + z2 / total;
  const center = rate + z2 / (2 * total);
  const spread = scoreZ * Math.sqrt((rate * (1 - rate) + z2 / (4 * total)) / total);
  return (center - spread) / denominator;
}

function computeCodexStablePositionKills(history) {
  const safeHistory = newestFirstHistory(history);
  const latest = safeHistory[0] || null;
  const records = safeHistory;
  const out = {};
  POSITION_MAP.forEach((pos) => {
    out[pos.key] = {
      digit: null,
      rate: 0,
      total: 0,
      hit: 0,
      ruleId: null,
      ruleLabel: null,
      recommended: false,
      strict: false,
      evidenceEligible: false,
      overlayEnabled: CODEX_STABLE_OVERLAY_ENABLED,
      validationLowerBound: 0,
      selectionRate: 0,
      selectionTotal: 0,
      randomBaseline: CODEX_STABLE_RANDOM_BASELINE
    };
  });
  const minimumHistory = CODEX_STABLE_VALIDATION_WINDOW + CODEX_STABLE_SELECTION_WINDOW + 18;
  if (records.length < minimumHistory) return out;

  POSITION_MAP.forEach((pos) => {
    const latestDigit = latest ? recordDigit(latest, pos.index) : null;
    const options = CODEX_RULE_LIST.map((rule) => {
      const digit = pickByRule(records, pos.index, rule.id, latestDigit);
      const validation = backtestRule(records, pos, rule, 0, CODEX_STABLE_VALIDATION_WINDOW);
      const selection = backtestRule(
        records,
        pos,
        rule,
        CODEX_STABLE_VALIDATION_WINDOW,
        CODEX_STABLE_SELECTION_WINDOW
      );
      return { rule, digit, validation, selection };
    }).filter((item) => item.digit !== null &&
      item.validation.total >= CODEX_STABLE_VALIDATION_WINDOW &&
      item.selection.total >= CODEX_STABLE_SELECTION_WINDOW);

    options.sort((a, b) =>
      b.selection.rate - a.selection.rate ||
      b.selection.hit - a.selection.hit ||
      a.rule.id.localeCompare(b.rule.id));
    const best = options[0];
    if (!best) return;
    const validation = best.validation;
    const lowerBound = wilsonLowerBound(validation.hit, validation.total, CODEX_STABLE_CONFIDENCE_Z);
    const evidenceEligible = lowerBound > CODEX_STABLE_RANDOM_BASELINE;
    const strongEnough = CODEX_STABLE_OVERLAY_ENABLED && evidenceEligible;
    out[pos.key] = {
      digit: best.digit,
      rate: Number(validation.rate.toFixed(4)),
      total: validation.total,
      hit: validation.hit,
      ruleId: best.rule.id,
      ruleLabel: best.rule.label,
      recommended: strongEnough,
      strict: strongEnough && lowerBound >= 0.92,
      evidenceEligible,
      overlayEnabled: CODEX_STABLE_OVERLAY_ENABLED,
      validationLowerBound: Number(lowerBound.toFixed(4)),
      selectionRate: Number(best.selection.rate.toFixed(4)),
      selectionTotal: best.selection.total,
      randomBaseline: CODEX_STABLE_RANDOM_BASELINE
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
    entry.codexStableEvidenceEligible = !!pick.evidenceEligible;
    entry.codexStableOverlayEnabled = !!pick.overlayEnabled;
    entry.codexStableValidationLowerBound = pick.validationLowerBound;
    entry.codexStableSelectionRate = pick.selectionRate;
    entry.codexStableSelectionTotal = pick.selectionTotal;
    entry.codexStableRandomBaseline = pick.randomBaseline;
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
    entry.algorithm = "v43-position-stability-blocks+codex-stable-0202-disjoint-confidence";
    entry.reason = `codex-stable-override:${pick.ruleId}:${(pick.rate * 100).toFixed(2)}%:lb${(pick.validationLowerBound * 100).toFixed(2)}%`;
  });
  return positionKills;
}

module.exports = {
  CODEX_RULE_LIST,
  POSITION_MAP,
  computeCodexStablePositionKills,
  applyCodexStablePositionOverlay
};
