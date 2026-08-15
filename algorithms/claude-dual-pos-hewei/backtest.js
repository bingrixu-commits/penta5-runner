"use strict";

/**
 * 离线回测：node backtest.js <history.json> [evalCap]
 * history.json 形如 runner /history 的返回：{ history: [{period, numbers:[...]}, ...] }（最新在前）。
 * 输出与实盘同策略的 walk-forward 结果、当前 tip，以及与随机基线 72.9% 的对比。
 */

const fs = require("fs");
const path = require("path");

const file = process.argv[2] || "/tmp/hist.json";
const evalCap = Number(process.argv[3] || 0) || 0;
if (evalCap > 0) process.env.CLAUDE_DUAL_EVAL_CAP = String(evalCap);

const formula = require("./formula");

const raw = JSON.parse(fs.readFileSync(path.resolve(file), "utf8"));
const history = Array.isArray(raw) ? raw : raw.history || [];
console.log("history periods:", history.length);

const snapshot = formula.buildSnapshot(history);
console.log("version:", snapshot.version);
console.log("current tip:", snapshot.tipText, "| basePeriod:", snapshot.basePeriod, "| complete:", snapshot.complete);
snapshot.items.forEach((it) => {
  console.log(`  ${it.label} 避开 ${it.code} | 后验出现率 ${(it.posterior * 100).toFixed(2)}% | 长窗避开率 ${it.rateLong}% (下界 ${it.lowerLong}%, 样本 ${it.sampleLong})`);
});
const hw = snapshot.hewei;
console.log(`  合尾 避开 ${hw.code} | 后验出现率 ${(hw.posterior * 100).toFixed(2)}% | 长窗避开率 ${hw.rateLong}% (下界 ${hw.lowerLong}%, 样本 ${hw.sampleLong})`);

console.log("\nwalk-forward (与实盘同一 pickTip):", snapshot.joint.method, "| 评估期数:", snapshot.joint.evalPeriods);
snapshot.joint.windows.forEach((w) => {
  console.log(
    `  窗口${String(w.window).padStart(5)} | 命中 ${w.hit}/${w.total} = ${w.rate}%`
    + ` | Wilson95%下界 ${w.lower}% | 基线 ${w.baseline}% | 超额 ${w.edgeOverBaseline >= 0 ? "+" : ""}${w.edgeOverBaseline}%`
    + ` | 最大连挂 ${w.maxMissStreak}`
  );
});
