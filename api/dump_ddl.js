"use strict";
const { createPool } = require("./db");
(async () => {
  const p = createPool();
  try {
    const tables = [
      "official_draws",
      "model_predictions",
      "model_evaluations",
      "model_counters",
      "model_states",
      "system_snapshots",
    ];
    for (const t of tables) {
      const [r] = await p.query("SHOW CREATE TABLE `" + t + "`");
      console.log("=== " + t + " ===");
      console.log(r[0]["Create Table"]);
      console.log("");
    }
  } finally {
    await p.end();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
