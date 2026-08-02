"use strict";
/**
 * 四定杀码形态旁路：大小 + 单双 同进程轮询写各自 state.json
 * 轻量、无 HTTP UI；只写 data/algorithm-projects/four-ding-shape-{size,parity}/
 */
const { MODELS, refreshAll } = require("../_four_ding_shape_run");

const POLL_MS = Math.max(15000, Number(process.env.FOUR_DING_SHAPE_POLL_MS || 60000));
const once = process.argv.includes("--once");

let lastPeriod = "";
let tickBusy = false;

function log(msg) {
  console.log("[" + new Date().toISOString() + "] four-ding-shape-kills " + msg);
}

async function tick(force) {
  if (tickBusy) return;
  tickBusy = true;
  try {
    const states = await refreshAll();
    const period = states[0] && states[0].latestPeriod ? String(states[0].latestPeriod) : "";
    if (!force && period && period === lastPeriod) {
      log("skip same period " + period);
      return;
    }
    lastPeriod = period;
    states.forEach((s) => {
      const k = s.kill || {};
      log(s.id + " period=" + s.latestPeriod + " kill=" + k.code + " " + k.attack +
        " r120=" + k.rate120 + "% (" + k.hit120 + "/" + k.total120 + ")");
    });
  } catch (e) {
    log("tick error: " + (e && e.message ? e.message : String(e)));
  } finally {
    tickBusy = false;
  }
}

async function main() {
  log("start models=" + MODELS.map((m) => m.id).join(",") + " poll=" + POLL_MS + "ms");
  await tick(true);
  if (once) {
    log("once done");
    process.exit(0);
  }
  setInterval(() => { tick(false); }, POLL_MS);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
