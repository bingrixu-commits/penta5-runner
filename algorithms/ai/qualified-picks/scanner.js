"use strict";

const audit = require("./audit");

const DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];

const POSITION_MODELS = Object.freeze([
  { kind: "position", key: "wan", label: "千", index: 0 },
  { kind: "position", key: "qian", label: "百", index: 1 },
  { kind: "position", key: "bai", label: "十", index: 2 },
  { kind: "position", key: "shi", label: "个", index: 3 }
]);

function metricsFromNumbers(numbers) {
  const nums = (Array.isArray(numbers) ? numbers : []).slice(0, 4).map((n) => Number(String(n).replace(/\D/g, "") || 0));
  while (nums.length < 4) nums.push(0);
  const n0 = nums[0];
  const n1 = nums[1];
  const n2 = nums[2];
  const n3 = nums[3];
  const totalSum = n0 + n1 + n2 + n3;
  return {
    totalSum,
    totalHewei: String(totalSum % 10),
    wanQianBaiHewei: String((n0 + n1 + n2) % 10),
    qianBaiShiHewei: String((n1 + n2 + n3) % 10),
    wanQianGeHewei: String((n0 + n1 + n3) % 10),
    wanBaiShiHewei: String((n0 + n2 + n3) % 10)
  };
}

const HEWEI_MODELS = Object.freeze([
  { kind: "hewei", key: "total", label: "总合尾", field: "totalHewei" },
  { kind: "hewei", key: "wanQianBai", label: "千百十", field: "wanQianBaiHewei" },
  { kind: "hewei", key: "qianBaiShi", label: "百十个", field: "qianBaiShiHewei" },
  { kind: "hewei", key: "wanQianGe", label: "千百个", field: "wanQianGeHewei" },
  { kind: "hewei", key: "wanBaiShi", label: "千十个", field: "wanBaiShiHewei" }
]);

function normalizeHistoryRows(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => {
      const period = String(row && (row.period || row.uid) || "").replace(/^period:/, "").trim();
      const numbers = Array.isArray(row && row.numbers) ? row.numbers.map(String) : [];
      if (!period || numbers.length < 4) return null;
      const metrics = metricsFromNumbers(numbers);
      return { period, numbers: numbers.slice(0, 4), metrics };
    })
    .filter(Boolean)
    .sort((a, b) => String(b.period).localeCompare(String(a.period)));
}

function digitAvoidRate(actualSeries, killDigit) {
  const digit = String(killDigit);
  let hit = 0;
  for (let i = 0; i < actualSeries.length; i += 1) {
    if (String(actualSeries[i]) !== digit) hit += 1;
  }
  const total = actualSeries.length;
  return {
    hit,
    total,
    rate: total ? Number(((hit * 100) / total).toFixed(2)) : 0
  };
}

function bestQualifiedDigit(actualSeries, window, minRate) {
  const sample = actualSeries.slice(0, window);
  if (sample.length < window) return null;
  let best = null;
  DIGITS.forEach((digit) => {
    const stat = digitAvoidRate(sample, digit);
    if (stat.rate < minRate) return;
    if (!best || stat.rate > best.rate || (stat.rate === best.rate && digit < best.digit)) {
      best = { digit, hit: stat.hit, total: stat.total, rate: stat.rate };
    }
  });
  return best;
}

function scanQualifiedPicks(historyRows, options) {
  const window = Math.max(20, Number(options && options.window) || 100);
  const minRate = Number(options && options.minRate) || 92;
  const history = normalizeHistoryRows(historyRows);
  const digestInfo = audit.historyWindowDigest(history, window);
  const picks = [];

  POSITION_MODELS.forEach((model) => {
    const actualSeries = history.map((row) => row.numbers[model.index]);
    const best = bestQualifiedDigit(actualSeries, window, minRate);
    if (!best) return;
    picks.push({
      kind: model.kind,
      key: model.key,
      label: model.label,
      digit: best.digit,
      hit: best.hit,
      total: best.total,
      rate: best.rate,
      strategy: "history-gate"
    });
  });

  HEWEI_MODELS.forEach((model) => {
    const actualSeries = history.map((row) => row.metrics[model.field]);
    const best = bestQualifiedDigit(actualSeries, window, minRate);
    if (!best) return;
    picks.push({
      kind: model.kind,
      key: model.key,
      label: model.label,
      digit: best.digit,
      hit: best.hit,
      total: best.total,
      rate: best.rate,
      strategy: "history-gate"
    });
  });

  return {
    window,
    minRate,
    historyCount: history.length,
    latestPeriod: history[0] ? history[0].period : null,
    windowDigest: digestInfo.digest,
    windowNewestPeriod: digestInfo.newestPeriod,
    windowOldestPeriod: digestInfo.oldestPeriod,
    picks
  };
}

module.exports = {
  DIGITS,
  POSITION_MODELS,
  HEWEI_MODELS,
  metricsFromNumbers,
  normalizeHistoryRows,
  digitAvoidRate,
  bestQualifiedDigit,
  scanQualifiedPicks,
  audit
};
