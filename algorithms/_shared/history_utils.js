"use strict";

/** Pure helpers for shared draw history. No algorithm state I/O. */

function safeHistoryRows(history, minNumbers) {
  const need = Number.isFinite(Number(minNumbers)) ? Number(minNumbers) : 4;
  return Array.isArray(history)
    ? history.filter((record) => record && Array.isArray(record.numbers) && record.numbers.length >= need)
    : [];
}

function modelNumbers(record) {
  if (!record || !Array.isArray(record.numbers)) return [];
  return record.numbers.slice(0, 4).map((n) => String(n));
}

function latestPeriod(history) {
  const row = Array.isArray(history) && history[0] ? history[0] : null;
  return row && (row.period || row.uid) ? String(row.period || row.uid) : "";
}

module.exports = {
  safeHistoryRows,
  modelNumbers,
  latestPeriod
};
