"use strict";
const fs = require("fs");
const path = require("path");
const lib = require("../_shape_lib");

const MODEL_ID = "shape-size";
const SHAPE_KEY = "shapeSize";
const WIDTH = 3;
const LABEL = "大小形态·" + lib.dingLabel(WIDTH);
const TOP_N = Number(process.env.SHAPE_TOP_N || 5);
const OUT_DIR = path.join(lib.REPO_ROOT, "data", "algorithm-projects", MODEL_ID);
const OUT_FILE = path.join(OUT_DIR, "state.json");

async function main() {
  const history = await lib.fetchHistory(1500);
  if (!history.length) {
    console.error("[" + MODEL_ID + "] history empty");
    process.exit(2);
  }
  const ranking = lib.rankShapes(history, SHAPE_KEY, TOP_N, WIDTH);
  const best = ranking[0] || null;
  const state = {
    version: MODEL_ID + "-v1",
    id: MODEL_ID,
    label: LABEL,
    shapeKey: SHAPE_KEY,
    width: WIDTH,
    scope: lib.scopeLabel(WIDTH),
    generatedAt: new Date().toISOString(),
    historyCount: history.length,
    latestPeriod: history[0] && history[0].period,
    best,
    ranking,
    note: "避开形态：开奖" + lib.scopeLabel(WIDTH) + "实际形态不等于 code 即成功"
  };
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT_FILE, JSON.stringify(state, null, 2));
  console.log("==== " + LABEL + " (" + MODEL_ID + ") ====");
  console.log("scope=" + state.scope + " history=" + history.length + " latest=" + state.latestPeriod);
  console.log("BEST 避开: " + (best ? best.code : "--") +
    "  score=" + (best && best.score) +
    "  r120=" + (best && best.rate120) + "%" +
    "  lower120=" + (best && best.lower120) +
    "  (" + (best && best.hit120) + "/" + (best && best.total120) + ")");
  ranking.forEach((row) => {
    console.log(" #" + row.rank + " " + row.code +
      "  score=" + row.score +
      "  r30=" + row.rate30 +
      "  r120=" + row.rate120 +
      "  lower=" + row.lower120 +
      "  miss30=" + row.missStreak30);
  });
  console.log("wrote " + OUT_FILE);
}

main().catch((e) => { console.error(e); process.exit(1); });
