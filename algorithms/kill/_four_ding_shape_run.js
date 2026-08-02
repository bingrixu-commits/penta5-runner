"use strict";
/**
 * 四定杀码形态：避开千百十个 4 字形态（大小 或 单双）。
 * 成功定义：开奖四位实际形态 ≠ 杀码 code
 */
const fs = require("fs");
const path = require("path");
const lib = require("../ai/_shape_lib");

const MODELS = Object.freeze([
  { id: "four-ding-shape-size", shapeKey: "shapeSize", label: "四定杀码·大小形态" },
  { id: "four-ding-shape-parity", shapeKey: "shapeParity", label: "四定杀码·单双形态" }
]);

function buildKillState(opts) {
  const { modelId, shapeKey, label, history, ranking } = opts;
  const best = ranking[0] || null;
  const killCode = best ? String(best.code) : "";
  const attack = killCode ? ("除" + killCode) : "";
  return {
    version: modelId + "-v1",
    kind: "fourDingShapeKill",
    id: modelId,
    label,
    shapeKey,
    width: 4,
    scope: "千百十个",
    ding: "四定",
    generatedAt: new Date().toISOString(),
    historyCount: history.length,
    latestPeriod: history[0] && history[0].period,
    kill: {
      code: killCode,
      attack,
      attackTail: killCode ? ("四定" + attack) : "",
      score: best ? best.score : null,
      rate30: best ? best.rate30 : null,
      rate120: best ? best.rate120 : null,
      lower120: best ? best.lower120 : null,
      hit120: best ? best.hit120 : null,
      total120: best ? best.total120 : null,
      missStreak30: best ? best.missStreak30 : null,
      stage: best ? best.stage : null
    },
    best,
    ranking,
    note: "四定杀码形态：除" + (shapeKey === "shapeSize" ? "大小" : "单双") + "四字；实际≠code 即中"
  };
}

async function refreshModel(modelId, shapeKey, label, historyOpt) {
  const TOP_N = Math.max(1, Number(process.env.SHAPE_TOP_N || 5));
  const OUT_DIR = path.join(lib.REPO_ROOT, "data", "algorithm-projects", modelId);
  const OUT_FILE = path.join(OUT_DIR, "state.json");
  const history = Array.isArray(historyOpt) && historyOpt.length
    ? historyOpt
    : await lib.fetchHistory(Number(process.env.SHAPE_HISTORY_LIMIT || 1500));
  if (!history.length) {
    throw new Error("[" + modelId + "] history empty");
  }
  const ranking = lib.rankShapes(history, shapeKey, TOP_N, 4);
  const state = buildKillState({ modelId, shapeKey, label, history, ranking });
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT_FILE, JSON.stringify(state, null, 2));
  return state;
}

async function runOnce(modelId, shapeKey, label) {
  const state = await refreshModel(modelId, shapeKey, label);
  const k = state.kill;
  console.log("==== " + label + " (" + modelId + ") ====");
  console.log("最新期 " + state.latestPeriod + " · 样本 " + state.historyCount);
  console.log("杀码: " + k.code + "  → 攻击语: " + k.attack);
  console.log("score=" + k.score + "  r30=" + k.rate30 + "%  r120=" + k.rate120 + "% (" + k.hit120 + "/" + k.total120 + ")  lower120=" + k.lower120 + "  miss30=" + k.missStreak30);
  (state.ranking || []).forEach((row) => {
    console.log(" #" + row.rank + " 除" + row.code +
      "  score=" + row.score +
      "  r30=" + row.rate30 +
      "  r120=" + row.rate120 +
      "  lower=" + row.lower120);
  });
  console.log("wrote data/algorithm-projects/" + modelId + "/state.json");
  return state;
}

async function refreshAll() {
  const history = await lib.fetchHistory(Number(process.env.SHAPE_HISTORY_LIMIT || 1500));
  if (!history.length) throw new Error("history empty");
  const out = [];
  for (const m of MODELS) {
    out.push(await refreshModel(m.id, m.shapeKey, m.label, history));
  }
  return out;
}

module.exports = { MODELS, runOnce, refreshModel, refreshAll, buildKillState };
