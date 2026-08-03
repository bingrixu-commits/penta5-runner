"use strict";
/**
 * 形态一千避开3组 —— 三组组合一起避开（AND 全过才算中）。
 * 按命中百分比选最优三元组（一起避开 AND）。
 */
const fs = require("fs");
const path = require("path");
const lib = require("../../ai/_shape_lib");

const MODEL_ID = "shape-avoid-3";
const GROUP_N = Math.max(1, Number(process.env.SHAPE_AVOID_GROUPS || 3));
const WIDTH = Number(process.env.SHAPE_WIDTH || 4) >= 4 ? 4 : 3;

function outPaths() {
  const dir = path.join(lib.REPO_ROOT, "data", "algorithm-projects", MODEL_ID);
  return { dir, file: path.join(dir, "state.json") };
}

function buildState(history, picked) {
  const ding = lib.dingLabel(WIDTH);
  const groups = (picked && picked.groups) || [];
  const joint = (picked && picked.joint) || null;
  return {
    version: MODEL_ID + "-v2-joint",
    kind: "shapeAvoidJointTrio",
    id: MODEL_ID,
    label: "形态百分比三组一起避开",
    parallelTo: "三对数一起排除",
    width: WIDTH,
    scope: lib.scopeLabel(WIDTH),
    ding,
    mode: "and",
    groupCount: groups.length,
    generatedAt: new Date().toISOString(),
    historyCount: history.length,
    latestPeriod: history[0] && history[0].period,
    longWindow: 1000,
    joint,
    groups: groups.map((g) => ({
      key: "shapeAvoid" + g.group,
      group: g.group,
      name: g.groupLabel,
      shapeKey: g.shapeKey,
      code: g.code,
      attack: g.attack,
      score: g.score,
      stage: g.stage,
      rate30: g.rate30,
      rate60: g.rate60,
      rate120: g.rate120,
      rate300: g.rate300,
      rateLong: g.rateLong,
      lower120: g.lower120,
      lowerLong: g.lowerLong,
      hit30: g.hit30,
      total30: g.total30,
      hit60: g.hit60,
      total60: g.total60,
      hit120: g.hit120,
      total120: g.total120,
      hit300: g.hit300,
      total300: g.total300,
      hitLong: g.hitLong,
      totalLong: g.totalLong,
      missStreak30: g.missStreak30
    })),
    shot: (joint && joint.shot) || groups.map((g) => g.attack).filter(Boolean).join(""),
    note: ding + "三组形态一起避开（AND）：按命中百分比选最优三元组；三组 AND 全过才算中"
  };
}

async function refresh() {
  const history = await lib.fetchHistory(Number(process.env.SHAPE_HISTORY_LIMIT || 1500));
  if (!history.length) throw new Error("history empty");
  const picked = lib.pickJointShapeAvoidTrio(history, GROUP_N, WIDTH);
  const state = buildState(history, picked);
  const { dir, file } = outPaths();
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(state, null, 2));
  return state;
}

module.exports = { MODEL_ID, GROUP_N, WIDTH, refresh, buildState };
