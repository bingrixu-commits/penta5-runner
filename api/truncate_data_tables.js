"use strict";
const { createPool } = require("./db");
(async () => {
  const p = createPool();
  const c = await p.getConnection();
  try {
    await c.query("SET FOREIGN_KEY_CHECKS=0");
    const tables = [
      "model_evaluations",
      "model_predictions",
      "model_counters",
      "model_states",
      "official_draws",
    ];
    for (const t of tables) {
      await c.query("TRUNCATE TABLE `" + t + "`");
      console.log("truncated " + t);
    }
    await c.query("SET FOREIGN_KEY_CHECKS=1");
  } finally {
    c.release();
    await p.end();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
