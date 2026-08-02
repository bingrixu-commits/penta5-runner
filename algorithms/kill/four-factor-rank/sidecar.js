"use strict";

const { MODEL_ID, refresh } = require("./engine");

const POLL_MS = Math.max(15000, Number(process.env.FOUR_FACTOR_POLL_MS || 60000));
const once = process.argv.includes("--once");

let lastPeriod = "";
let busy = false;

function log(msg) {
  console.log("[" + new Date().toISOString() + "] " + MODEL_ID + " " + msg);
}

async function tick(force) {
  if (busy) return;
  busy = true;
  try {
    const state = await refresh();
    const period = String(state.latestPeriod || "");
    if (!force && period && period === lastPeriod) {
      log("skip same period " + period);
      return;
    }
    lastPeriod = period;
    log(
      "period=" + period +
      " picks=" + (state.line || "") +
      " hist=" + state.historyCount
    );
  } catch (e) {
    log("tick error: " + (e && e.message ? e.message : String(e)));
  } finally {
    busy = false;
  }
}

async function main() {
  log("start poll=" + POLL_MS + "ms");
  await tick(true);
  if (once) {
    log("once done");
    process.exit(0);
  }
  setInterval(() => tick(false), POLL_MS);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
