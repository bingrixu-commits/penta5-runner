const fs = require("fs");
const s = JSON.parse(fs.readFileSync("/root/penta5-runner/data/state.json", "utf8"));
const ledger = s.dailyStreakLedger || {};
function lastDays(store, n) {
  const obj = ledger[store] || {};
  return Object.keys(obj)
    .sort()
    .slice(-n)
    .map((d) => Object.assign({ d }, obj[d]));
}
const out = {
  latestPeriod: s.latestRecord && s.latestRecord.period,
  latestOpen: s.latestRecord && s.latestRecord.opentime,
  predCount: (s.predictions || []).length,
  histCount: (s.history || []).length,
  ledgerKeys: Object.keys(ledger),
  autoTop6: lastDays("autoTop6", 3),
  recommend2: lastDays("recommend2", 3),
  killFramework: lastDays("killFramework", 3),
  compactAttack: lastDays("compactAttack", 3),
  threeDingTwo: lastDays("threeDingTwo", 3),
  threeDingOne: lastDays("threeDingOne", 3),
  twoDigitAvoidAllThree: lastDays("twoDigitAvoidAllThree", 3),
  threeDuishu: lastDays("threeDuishu", 3)
};
const cur = s.currentPrediction || {};
const ev = cur.evaluation || {};
out.currentAutoTop6 = cur.autoTop6
  ? {
      version: cur.autoTop6.version,
      total: cur.autoTop6.total,
      success: cur.autoTop6.success,
      truthEligible: cur.autoTop6.truthEligible,
      items: (cur.autoTop6.items || []).map((i) => ({
        key: i.key || i.name,
        kind: i.kind,
        code: i.code
      }))
    }
  : null;
out.hasIndependentExtrasKeys = Object.keys(ev.independentExtras || {}).slice(0, 20);
const sample = (s.predictions || []).find((p) => p && p.evaluated && p.evaluation && p.evaluation.independentExtras);
if (sample) {
  const extras = sample.evaluation.independentExtras || {};
  out.sampleExtras = Object.keys(extras).reduce((acc, k) => {
    const e = extras[k];
    acc[k] = { success: e && e.success, value: e && (e.value || e.code) };
    return acc;
  }, {});
  out.samplePeriod = sample.evaluation.targetPeriod;
}
console.log(JSON.stringify(out, null, 2));
